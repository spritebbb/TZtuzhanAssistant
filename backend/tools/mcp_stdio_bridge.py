# -*- coding: utf-8 -*-
"""stdio → Streamable HTTP 桥：把任意 stdio MCP 服务器接进菟菚。

为什么需要它：生态里大多数 MCP 服务器（含 Playwright MCP 默认模式）只讲
**stdio**（子进程 stdin/stdout 上的换行分隔 JSON-RPC），而菟菚的 MCP 客户端
讲标准 **Streamable HTTP**。本桥把两者接起来，不改动服务器本身。

用法（在项目根目录）：

    .venv/Scripts/python.exe -m backend.tools.mcp_stdio_bridge \
        --port 8932 -- npx @playwright/mcp@latest

然后在菟菚设置页注册：名称任意，URL 填 http://127.0.0.1:8932/mcp
（另需 .env 里 AGENT_MCP_ALLOW_LOOPBACK=1 才允许回环地址）。

协议要点：
- 子进程 stdout 每行一条 JSON-RPC 消息；stderr 原样转发到本进程 stderr 便于排错；
- 桥给每条客户端请求分配独立 id（避免与服务器主动请求撞号），响应时还原；
- 通知（无 id / notifications/*）立即返回 202，不等响应；
- 只监听回环地址，不对外暴露。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_REQUEST_TIMEOUT = 60.0


class StdioBridge:
    """把子进程的 stdio JSON-RPC 转成请求/响应。"""

    def __init__(self, command: list[str]) -> None:
        self.command = command
        self._lock = threading.Lock()
        self._next_id = 0
        self._pending: dict[int, dict] = {}
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,           # 子进程日志直接进本进程 stderr
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                        name="mcp-stdio-reader")
        self._reader.start()

    # ---- 子进程 → 桥 ----

    def _read_loop(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                print(f"[bridge] 非 JSON 输出（已忽略）：{line[:200]}", file=sys.stderr)
                continue
            msg_id = message.get("id")
            if msg_id is None:
                print(f"[bridge] 服务器通知：{line[:200]}", file=sys.stderr)
                continue
            with self._lock:
                slot = self._pending.pop(int(msg_id), None)
            if slot is not None:
                slot["result"] = message
                slot["event"].set()
        # 子进程退出：唤醒所有等待者
        with self._lock:
            waiters = list(self._pending.values())
            self._pending.clear()
        for slot in waiters:
            slot["result"] = {"jsonrpc": "2.0", "id": slot["client_id"],
                              "error": {"code": -32000, "message": "MCP 子进程已退出"}}
            slot["event"].set()

    def _send(self, payload: dict) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    # ---- 桥 → 客户端 ----

    def request(self, message: dict) -> dict:
        with self._lock:
            self._next_id += 1
            bridge_id = self._next_id
            slot: dict[str, Any] = {
                "event": threading.Event(), "result": None,
                "client_id": message.get("id"),
            }
            self._pending[bridge_id] = slot
        forward = dict(message)
        forward["id"] = bridge_id
        try:
            self._send(forward)
        except Exception as exc:
            with self._lock:
                self._pending.pop(bridge_id, None)
            return {"jsonrpc": "2.0", "id": message.get("id"),
                    "error": {"code": -32000, "message": f"写入子进程失败：{exc}"}}
        if not slot["event"].wait(_REQUEST_TIMEOUT):
            with self._lock:
                self._pending.pop(bridge_id, None)
            return {"jsonrpc": "2.0", "id": message.get("id"),
                    "error": {"code": -32000, "message": "MCP 服务器响应超时"}}
        result = dict(slot["result"] or {})
        result["id"] = message.get("id")     # 还原客户端原始 id
        return result

    def notify(self, message: dict) -> None:
        forward = {k: v for k, v in message.items() if k != "id"}
        self._send(forward)

    def close(self) -> None:
        try:
            self._proc.terminate()
        except Exception:
            pass


def make_handler(bridge: StdioBridge):
    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _reply(self, payload: dict, status: int = 200, extra: dict | None = None):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path.rstrip("/") != "/mcp":
                return self._reply({"error": "not found"}, status=404)
            length = int(self.headers.get("Content-Length") or 0)
            try:
                message = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._reply({"jsonrpc": "2.0", "id": None,
                                    "error": {"code": -32700, "message": "parse error"}},
                                   status=400)
            method = str(message.get("method") or "")
            if message.get("id") is None or method.startswith("notifications/"):
                bridge.notify(message)
                return self._reply({}, status=202)
            response = bridge.request(message)
            extra = {"Mcp-Session-Id": "stdio-bridge"} if method == "initialize" else None
            return self._reply(response, extra=extra)

        def do_GET(self):
            # Streamable HTTP 允许 GET 开 SSE 流；本桥只支持请求/响应模式
            self._reply({"error": "this bridge only supports POST /mcp"}, status=405)

    return _Handler


def main() -> int:
    ap = argparse.ArgumentParser(description="stdio MCP → Streamable HTTP 桥")
    ap.add_argument("--port", type=int, default=8932, help="本地监听端口（仅回环）")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    ap.add_argument("command", nargs=argparse.REMAINDER,
                    help="-- 后面是要启动的 stdio MCP 服务器命令")
    args = ap.parse_args()
    command = [c for c in args.command if c != "--"]
    if not command:
        print("用法：python -m backend.tools.mcp_stdio_bridge --port 8932 -- npx @playwright/mcp@latest",
              file=sys.stderr)
        return 2
    bridge = StdioBridge(command)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(bridge))
    print(f"🌉 MCP stdio 桥已启动：http://{args.host}:{args.port}/mcp")
    print(f"   子进程：{' '.join(command)}")
    print("   在菟菚设置页注册上面的地址（需 .env 里 AGENT_MCP_ALLOW_LOOPBACK=1）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
