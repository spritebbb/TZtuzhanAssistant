# -*- coding: utf-8 -*-
"""stdio→HTTP 桥：用假 stdio MCP 服务器验证全链路。

验收锚点：
- 桥把 HTTP JSON-RPC 转发到子进程 stdin，并把 stdout 响应还原给客户端；
- 通知（notifications/*）返回 202 不阻塞；
- 客户端 id 与桥内部 id 解耦（响应 id 还原为客户端原值）；
- 子进程退出后等待中的请求得到结构化错误，不会挂死。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_bridge_"))

from backend.tools.mcp_client import McpClient
from backend.tools.mcp_stdio_bridge import StdioBridge, make_handler
from http.server import ThreadingHTTPServer

MOCK_SERVER = r'''
import json, sys
TOOLS = [{"name": "echo", "description": "回显", "inputSchema": {"type": "object"}}]
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        out = {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
            "serverInfo": {"name": "mock-stdio", "version": "1"}}}
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        out = {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    elif method == "tools/call":
        text = (msg.get("params", {}).get("arguments") or {}).get("text", "")
        out = {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": "echo:" + text}], "isError": False}}
    else:
        out = {"jsonrpc": "2.0", "id": mid,
               "error": {"code": -32601, "message": "method not found"}}
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    sys.stdout.flush()
'''


def _write_mock(dir_path: Path) -> Path:
    path = dir_path / "mock_stdio_server.py"
    path.write_text(MOCK_SERVER, encoding="utf-8")
    return path


def _start_bridge(script: Path) -> tuple[ThreadingHTTPServer, str, StdioBridge]:
    bridge = StdioBridge([sys.executable, "-X", "utf8", str(script)])
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(bridge))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/mcp", bridge


def test_end_to_end_through_client() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        script = _write_mock(Path(tmp))
        server, url, bridge = _start_bridge(script)
        try:
            client = McpClient("stdio-mock", url, allow_private=True, timeout=15)
            info = client.initialize()
            assert info["server"]["name"] == "mock-stdio", info
            tools = client.list_tools()
            assert tools[0]["name"] == "echo", tools
            assert client.call_tool("echo", {"text": "桥测试"}) == "echo:桥测试"
            # 客户端 id 与桥内部 id 解耦：连续多次调用仍正确匹配
            assert client.call_tool("echo", {"text": "第二次"}) == "echo:第二次"
        finally:
            server.shutdown()
            bridge.close()
    print("[OK] 桥端到端：initialize / tools/list / tools/call（含连续调用 id 匹配）")
    return 0


def test_notification_and_dead_child() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        script = _write_mock(Path(tmp))
        server, url, bridge = _start_bridge(script)
        try:
            # 通知：202 且不阻塞
            body = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode()
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:
                assert resp.status == 202, resp.status
            # 子进程退出后：等待中的请求返回结构化错误而非挂死
            bridge.close()
            time.sleep(0.5)
            resp = bridge.request({"jsonrpc": "2.0", "id": 99, "method": "tools/list"})
            assert resp["error"]["code"] == -32000, resp
            assert resp["id"] == 99
        finally:
            server.shutdown()
    print("[OK] 通知 202 / 子进程退出返回结构化错误")
    return 0


def main() -> int:
    failed = test_end_to_end_through_client() + test_notification_and_dead_child()
    if failed:
        print(f"\n=== stdio 桥：{failed} 项失败 ===")
        return 1
    print("\n=== stdio 桥：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
