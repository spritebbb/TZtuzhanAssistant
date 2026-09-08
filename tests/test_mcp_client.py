# -*- coding: utf-8 -*-
"""标准 MCP 客户端：Streamable HTTP / HTTP+SSE 两种传输与回环策略。

验收锚点：
- initialize → notifications/initialized → tools/list → tools/call 全链路走通；
- Streamable HTTP 的响应既支持 application/json 也支持 text/event-stream；
- 旧版 HTTP+SSE 自动回退可用（GET 拿 endpoint，POST 后从事件流取响应）；
- 回环地址默认被 SSRF 防护拒绝，AGENT_MCP_ALLOW_LOOPBACK 开启后才放行。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", str(Path(__file__).parent / "_mcp_tmp"))

from backend.tools.mcp_client import McpClient, McpProtocolError

_TOOLS = [{
    "name": "echo",
    "description": "回显一段文本",
    "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                    "required": ["text"]},
}]


def _rpc_result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


class _StreamableHandler(BaseHTTPRequestHandler):
    sse_response = False   # 由测试切换：tools/list 用事件流返回
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 静音
        pass

    def _json(self, payload, status=200, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, payload):
        body = f"event: message\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        message = json.loads(self.rfile.read(length) or b"{}")
        method = message.get("method")
        msg_id = message.get("id")
        if method == "initialize":
            return self._json(
                _rpc_result(msg_id, {
                    "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mock-streamable", "version": "1.0"},
                }),
                extra_headers={"Mcp-Session-Id": "sess-1"},
            )
        if method == "notifications/initialized":
            return self._json({}, status=202)
        if method == "tools/list":
            payload = _rpc_result(msg_id, {"tools": _TOOLS})
            return self._sse(payload) if type(self).sse_response else self._json(payload)
        if method == "tools/call":
            params = message.get("params") or {}
            text = (params.get("arguments") or {}).get("text", "")
            return self._json(_rpc_result(msg_id, {
                "content": [{"type": "text", "text": f"echo:{text}"}],
                "isError": False,
            }))
        return self._json({"jsonrpc": "2.0", "id": msg_id,
                           "error": {"code": -32601, "message": "method not found"}}, status=404)


class _SseHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    streams: list = []
    stop_event: threading.Event = threading.Event()

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b"event: endpoint\ndata: /messages\n\n")
        self.wfile.flush()
        type(self).streams.append(self)
        # 保持连接：BaseHTTPRequestHandler 在方法返回后会关闭连接，
        # 这里阻塞到测试结束，模拟长连事件流
        while not type(self).stop_event.wait(0.2):
            pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        message = json.loads(self.rfile.read(length) or b"{}")
        method = message.get("method")
        msg_id = message.get("id")
        if method == "notifications/initialized":
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "mock-sse", "version": "1.0"}}
        elif method == "tools/list":
            result = {"tools": _TOOLS}
        elif method == "tools/call":
            text = (message.get("params", {}).get("arguments") or {}).get("text", "")
            result = {"content": [{"type": "text", "text": f"echo:{text}"}], "isError": False}
        else:
            result = None
        self.send_response(202)
        self.send_header("Content-Length", "0")
        self.end_headers()
        if result is not None:
            payload = _rpc_result(msg_id, result)
            for stream in list(type(self).streams):
                try:
                    stream.wfile.write(
                        f"event: message\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
                    )
                    stream.wfile.flush()
                except Exception:
                    type(self).streams.remove(stream)


def _serve(handler) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_streamable_json_and_sse() -> int:
    server, base = _serve(_StreamableHandler)
    try:
        _StreamableHandler.sse_response = False
        client = McpClient("mock", f"{base}/mcp", allow_private=True, timeout=10)
        info = client.initialize()
        assert info["transport"] == "streamable", info
        assert info["server"]["name"] == "mock-streamable"
        tools = client.list_tools()
        assert tools and tools[0]["name"] == "echo", tools
        assert client.call_tool("echo", {"text": "你好"}) == "echo:你好"
        assert client._session_id == "sess-1"

        # 事件流响应同样能解析
        _StreamableHandler.sse_response = True
        client2 = McpClient("mock-sse-resp", f"{base}/mcp", allow_private=True, timeout=10)
        assert client2.list_tools()[0]["name"] == "echo"
        assert client2.call_tool("echo", {"text": "x"}) == "echo:x"
    finally:
        server.shutdown()
    print("[OK] Streamable HTTP：JSON 与事件流响应均可用")
    return 0


def test_legacy_sse_fallback() -> int:
    _SseHandler.streams = []
    _SseHandler.stop_event = threading.Event()
    server, base = _serve(_SseHandler)
    try:
        client = McpClient("mock-legacy", f"{base}/sse", allow_private=True, timeout=10)
        info = client.initialize()
        assert info["transport"] == "sse", info
        assert info["server"]["name"] == "mock-sse"
        assert client.list_tools()[0]["name"] == "echo"
        assert client.call_tool("echo", {"text": "legacy"}) == "echo:legacy"
    finally:
        _SseHandler.stop_event.set()
        server.shutdown()
    print("[OK] 旧版 HTTP+SSE 自动回退可用")
    return 0


def test_loopback_policy() -> int:
    server, base = _serve(_StreamableHandler)
    try:
        blocked = McpClient("blocked", f"{base}/mcp", allow_private=False, timeout=5)
        try:
            blocked.initialize()
            raise AssertionError("回环地址默认应被 SSRF 防护拒绝")
        except McpProtocolError as exc:
            assert "不安全" in str(exc), exc
        from backend.core.config import config

        assert getattr(config, "agent_mcp_allow_loopback", None) in (True, False)
    finally:
        server.shutdown()
    print("[OK] 回环默认拒绝、显式放行才可用")
    return 0


def test_register_external_server_integration() -> int:
    """端到端：注册 → 工具进全局注册表 → 调用 → 卸载。"""
    import asyncio

    from backend.core.config import config
    from backend.tools import mcp_server
    from backend.tools.base import ToolRegistry

    server, base = _serve(_StreamableHandler)
    _StreamableHandler.sse_response = False
    old_flag = config.agent_mcp_allow_loopback
    config.agent_mcp_allow_loopback = True
    try:
        ok = asyncio.run(mcp_server.register_external_server("mock", f"{base}/mcp"))
        assert ok is True
        assert "mock::echo" in ToolRegistry.tool_names()
        spec = ToolRegistry.get("mock::echo")
        assert spec is not None and spec.category == "external" and spec.needs_confirm
        result = asyncio.run(ToolRegistry.execute("mock::echo", {"text": "hi"}))
        assert result.ok and "echo:hi" in result.output, result
        assert mcp_server.unregister_external_server("mock") is True
        assert "mock::echo" not in ToolRegistry.tool_names()
    finally:
        config.agent_mcp_allow_loopback = old_flag
        server.shutdown()
    print("[OK] 注册/调用/卸载端到端（external + 需确认）")
    return 0


def main() -> int:
    failed = (
        test_streamable_json_and_sse()
        + test_legacy_sse_fallback()
        + test_loopback_policy()
        + test_register_external_server_integration()
    )
    if failed:
        print(f"\n=== MCP 标准客户端：{failed} 项失败 ===")
        return 1
    print("\n=== MCP 标准客户端：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
