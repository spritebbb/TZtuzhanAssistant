# -*- coding: utf-8 -*-
"""标准 MCP 客户端：Streamable HTTP（2025-03-26/2025-06-18）+ 旧版 HTTP+SSE 回退。

与旧实现的区别（2026-09-09 升级）：
- 走标准 JSON-RPC 方法名：``initialize`` → ``notifications/initialized`` →
  ``tools/list`` / ``tools/call``，而不是自研的 ``GET /tools`` + ``POST /call``；
- 支持两种传输并自动探测：先试 Streamable HTTP（单端点 POST，响应可能是
  JSON 或 text/event-stream），失败再退到 2024-11-05 的 HTTP+SSE
  （GET 拿 endpoint 事件，再向该 endpoint POST）；
- 会话：``initialize`` 响应里的 ``Mcp-Session-Id`` 头在后续请求回带；服务器
  返回 404 视为会话过期，自动重新握手一次；
- 安全：非回环地址沿用 SSRF 防护（逐跳复检 + DNS pinning）；本地/内网地址
  仅在显式开启 ``AGENT_MCP_ALLOW_LOOPBACK=1`` 时放行（本地 MCP 服务器需要）。
"""
from __future__ import annotations

import json
import queue
import threading
import urllib.error
import urllib.parse
import urllib.request

from ..core.log import logger

PROTOCOL_VERSION = "2025-06-18"
_CLIENT_INFO = {"name": "tuzhan-assistant", "version": "1.0"}
_MAX_REDIRECTS = 5
_ACCEPT = "application/json, text/event-stream"


class McpProtocolError(RuntimeError):
    """MCP 握手/调用失败（协议层）。"""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不自动跟随重定向：每跳都要重新做 SSRF 校验。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _is_private_host(host: str) -> bool:
    import ipaddress

    host = (host or "").strip().strip("[]").lower()
    if host in ("localhost", "localhost.localdomain"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _open(url: str, *, method: str, body: bytes | None, headers: dict,
          timeout: float, allow_private: bool):
    """发一次 HTTP 请求（不自动跟重定向，逐跳复检）。

    返回 (status, response_headers, response_object)。response 需由调用方关闭。
    """
    from .safety import build_pinned_opener, resolve_public_url

    cur = url
    for _ in range(_MAX_REDIRECTS + 1):
        parsed = urllib.parse.urlparse(cur)
        host = parsed.hostname or ""
        if allow_private and _is_private_host(host):
            opener = urllib.request.build_opener(_NoRedirect())
        else:
            ok, err, resolved_ip = resolve_public_url(cur)
            if not ok:
                raise McpProtocolError(f"拒绝访问不安全的服务器地址：{err}")
            opener = build_pinned_opener(resolved_ip, _NoRedirect())
        req = urllib.request.Request(cur, data=body, headers=headers, method=method)
        try:
            resp = opener.open(req, timeout=timeout)
            return resp.status, resp.headers, resp
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                loc = e.headers.get("Location")
                if not loc:
                    raise McpProtocolError("重定向缺少 Location") from e
                cur = urllib.parse.urljoin(cur, loc)
                continue
            return e.code, e.headers, e
    raise McpProtocolError("重定向次数过多")


def _read_json_or_sse(resp, *, want_id: int | None, timeout: float) -> dict:
    """读取响应：application/json 直接解析；text/event-stream 抽 JSON-RPC 消息。

    ``want_id`` 非空时，只返回 id 匹配的消息（跳过通知/日志事件）。
    """
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "text/event-stream" not in ctype:
        raw = resp.read().decode("utf-8", errors="replace")
        if not raw.strip():
            raise McpProtocolError("服务器返回空响应")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise McpProtocolError(f"响应不是合法 JSON：{raw[:120]}") from exc

    data_lines: list[str] = []
    for raw_line in resp:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif line == "":
            if not data_lines:
                continue
            payload = "\n".join(data_lines)
            data_lines = []
            try:
                message = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if want_id is None or message.get("id") == want_id:
                return message
    raise McpProtocolError("事件流结束前未收到匹配的响应")


class McpClient:
    """一个外部 MCP 服务器的标准客户端（自动探测传输）。"""

    def __init__(self, name: str, url: str, *, allow_private: bool = False,
                 timeout: float = 30.0) -> None:
        self.name = name
        self.url = url.rstrip("/")
        self.allow_private = allow_private
        self.timeout = timeout
        self.transport: str | None = None      # "streamable" | "sse"
        self._session_id: str | None = None
        self._sse_endpoint: str | None = None
        self._sse_queue: queue.Queue = queue.Queue()
        self._sse_thread: threading.Thread | None = None
        self._next_id = 0
        self._lock = threading.Lock()

    # ---- 对外 API ----

    def initialize(self) -> dict:
        """握手（幂等）：返回服务器信息。"""
        if self.transport is not None:
            return {"transport": self.transport, "server": getattr(self, "_server_info", {})}
        try:
            return self._initialize_streamable()
        except McpProtocolError as exc:
            logger.info("[MCP] {} Streamable HTTP 握手失败（{}），回退 HTTP+SSE", self.name, exc)
        return self._initialize_sse()

    def list_tools(self) -> list[dict]:
        self.initialize()
        result = self._request("tools/list", {})
        return list((result or {}).get("tools") or [])

    def call_tool(self, tool: str, arguments: dict) -> str:
        self.initialize()
        result = self._request("tools/call", {"name": tool, "arguments": arguments or {}})
        if not isinstance(result, dict):
            return str(result)
        parts = []
        for item in result.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        if result.get("isError"):
            raise McpProtocolError("；".join(parts) or "远程工具返回错误")
        return "\n".join(parts)

    # ---- 传输实现 ----

    def _rpc_id(self) -> int:
        with self._lock:
            self._next_id += 1
            return self._next_id

    def _headers(self, *, json_body: bool) -> dict:
        headers = {"Accept": _ACCEPT}
        if json_body:
            headers["Content-Type"] = "application/json"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self.transport == "streamable" and self._session_id:
            headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
        return headers

    def _post(self, url: str, payload: dict, *, want_id: int | None) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        status, headers, resp = _open(
            url, method="POST", body=body, headers=self._headers(json_body=True),
            timeout=self.timeout, allow_private=self.allow_private,
        )
        try:
            if status >= 400:
                detail = resp.read().decode("utf-8", errors="replace")[:200]
                raise McpProtocolError(f"HTTP {status}：{detail}")
            session = headers.get("Mcp-Session-Id")
            if session:
                self._session_id = session
            if want_id is None:
                return {}   # 通知：服务器通常回 202 空体
            return _read_json_or_sse(resp, want_id=want_id, timeout=self.timeout)
        finally:
            resp.close()

    def _initialize_streamable(self) -> dict:
        rid = self._rpc_id()
        message = self._post(self.url, {
            "jsonrpc": "2.0", "id": rid, "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": _CLIENT_INFO,
            },
        }, want_id=rid)
        result = self._unwrap(message)
        self.transport = "streamable"
        self._server_info = (result or {}).get("serverInfo") or {}
        self._notify("notifications/initialized")
        logger.info("[MCP] {} 已连接（Streamable HTTP，服务器 {}）",
                    self.name, self._server_info.get("name", "?"))
        return {"transport": "streamable", "server": self._server_info}

    def _initialize_sse(self) -> dict:
        """旧版 HTTP+SSE：GET 事件流拿 endpoint，再向 endpoint POST。"""
        headers = {"Accept": "text/event-stream"}
        status, _h, resp = _open(
            self.url, method="GET", body=None, headers=headers,
            timeout=self.timeout, allow_private=self.allow_private,
        )
        if status >= 400:
            resp.close()
            raise McpProtocolError(f"HTTP {status}：SSE 通道打不开")
        # 读第一段事件里的 endpoint
        data_lines: list[str] = []
        event = ""
        endpoint: str | None = None
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif line == "" and data_lines:
                payload = "\n".join(data_lines)
                data_lines = []
                if event == "endpoint" or payload.startswith("/") or payload.startswith("http"):
                    endpoint = payload
                    break
        if not endpoint:
            resp.close()
            raise McpProtocolError("SSE 通道未返回 endpoint 事件")
        self._sse_endpoint = urllib.parse.urljoin(self.url + "/", endpoint)
        self.transport = "sse"
        # 后台线程继续读事件流
        self._sse_thread = threading.Thread(
            target=self._sse_reader, args=(resp,), daemon=True,
            name=f"mcp-sse-{self.name}",
        )
        self._sse_thread.start()
        # SSE 传输下 POST 只回 202，真正的响应从事件流回来 → 交给 _request 等待
        result = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": _CLIENT_INFO,
        })
        self._server_info = (result or {}).get("serverInfo") or {}
        self._notify("notifications/initialized")
        logger.info("[MCP] {} 已连接（HTTP+SSE，服务器 {}）",
                    self.name, self._server_info.get("name", "?"))
        return {"transport": "sse", "server": self._server_info}

    def _sse_reader(self, resp) -> None:
        """后台读取 SSE 事件流，把 JSON-RPC 消息放进队列。"""
        data_lines: list[str] = []
        try:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                elif line == "" and data_lines:
                    payload = "\n".join(data_lines)
                    data_lines = []
                    try:
                        self._sse_queue.put(json.loads(payload))
                    except json.JSONDecodeError:
                        continue
        except Exception as exc:
            logger.info("[MCP] {} SSE 读线程结束：{}", self.name, exc)
        finally:
            resp.close()

    def _request(self, method: str, params: dict) -> dict:
        """发一次请求（sse 传输下从事件流取响应）。"""
        rid = self._rpc_id()
        url = self._sse_endpoint if self.transport == "sse" else self.url
        if not url:
            raise McpProtocolError("未完成握手")
        payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        if self.transport == "sse":
            self._post(url, payload, want_id=None)  # 响应走事件流
            deadline = self.timeout
            while True:
                try:
                    message = self._sse_queue.get(timeout=deadline)
                except queue.Empty as exc:
                    raise McpProtocolError(f"{method} 超时未收到响应") from exc
                if message.get("id") == rid:
                    return self._unwrap(message)
        message = self._post(url, payload, want_id=rid)
        if message.get("error") and message.get("error", {}).get("code") == -32001:
            # 会话过期（部分服务器用 404 表达）：重握手一次再试
            self.transport = None
            self._session_id = None
            self.initialize()
            url = self._sse_endpoint if self.transport == "sse" else self.url
            rid = self._rpc_id()
            payload["id"] = rid
            message = self._post(url, payload, want_id=rid)
        return self._unwrap(message)

    def _notify(self, method: str, params: dict | None = None) -> None:
        url = self._sse_endpoint if self.transport == "sse" else self.url
        if not url:
            return
        try:
            self._post(url, {"jsonrpc": "2.0", "method": method,
                             "params": params or {}}, want_id=None)
        except McpProtocolError as exc:
            logger.info("[MCP] {} 通知 {} 发送失败（可忽略）：{}", self.name, method, exc)

    @staticmethod
    def _unwrap(message: dict) -> dict:
        if not isinstance(message, dict):
            raise McpProtocolError("响应不是 JSON-RPC 对象")
        if message.get("error"):
            err = message["error"]
            raise McpProtocolError(f"{err.get('code')}: {err.get('message')}")
        result = message.get("result")
        return result if isinstance(result, dict) else {}
