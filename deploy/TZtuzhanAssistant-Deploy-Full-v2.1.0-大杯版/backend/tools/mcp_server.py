# -*- coding: utf-8 -*-
"""MCP（Model Context Protocol）服务器。

为外部客户端（或 LLM）暴露标准 MCP 接口，遵循 MCP 协议：
- /mcp/tools：列出所有可用工具（含内置 + 外部注册）
- /mcp/call：调用某个工具

同时提供 mcp_client 用于连接外部 MCP 服务器（自动发现并注册远程工具）。
注册的外部服务器会持久化到 data/mcp_servers.json，重启后自动恢复。
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import urllib.request
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..core.config import config
from ..core.log import logger
from ..tools.safety import remote_token_ok_by_peer, request_token
from .base import ToolRegistry

router = APIRouter(prefix="/mcp", tags=["mcp"])

# 外部服务器持久化文件（重启后自动恢复）
_PERSIST_PATH = config.data_dir / "mcp_servers.json"
_persist_lock = threading.Lock()


@router.get("/tools")
async def mcp_list_tools(request: Request):
    """列出所有可用工具（含名称、描述、输入 schema、安全元数据）。"""
    # 与 /api/remote 同一 token 体系（来源 IP 语义）：回环来源免 token；
    # 非回环来源必须携带有效 token（防局域网裸调读取工具清单/安全元数据）
    if not remote_token_ok_by_peer(
        request_token(request), request.client.host if request.client else None
    ):
        return JSONResponse(
            {"jsonrpc": "2.0", "error": {"code": -32001, "message": "unauthorized"}},
            status_code=403,
        )
    tools = ToolRegistry.list()
    return {
        "jsonrpc": "2.0",
        "result": {
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.input_schema,
                    "category": t.category,
                    "dangerLevel": t.danger_level,
                    "needsConfirm": t.needs_confirm,
                    "maxOutputChars": t.max_output_chars,
                }
                for t in tools
            ]
        },
    }


@router.post("/call")
async def mcp_call_tool(request: Request):
    """调用工具。body: {"name": "...", "arguments": {...}}

    鉴权：与 /api/remote 同一 token 体系（来源 IP 语义，Authorization:
    Bearer <token> 或 ?token=）。回环来源免 token；非回环来源必须携带与
    AGENT_REMOTE_TOKEN 匹配的 token，未配置 token 时一律拒绝。
    """
    if not remote_token_ok_by_peer(
        request_token(request), request.client.host if request.client else None
    ):
        return JSONResponse(
            {"jsonrpc": "2.0", "error": {"code": -32001, "message": "unauthorized"}},
            status_code=403,
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}},
            status_code=400,
        )
    name = body.get("name", "") if isinstance(body, dict) else ""
    args = body.get("arguments", {}) if isinstance(body, dict) else {}
    if not isinstance(args, dict):
        args = {}
    result = await ToolRegistry.execute(name, args)
    return {
        "jsonrpc": "2.0",
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": result.output if result.ok else (result.error or "调用失败"),
                }
            ],
            "isError": not result.ok,
        },
    }
# ---- 简化 MCP 客户端：连接外部 MCP 服务器 ----

# 已注册的外部服务器登记表（运行时内存态）
_EXTERNAL_SERVERS: dict[str, dict] = {}


# 不自动跟随重定向：每一跳都显式复检目标 URL（防 302 → 内网 SSRF）。
# 与 plugins/web_fetch.py 的 _NoRedirect 同一策略：注册时 check_url 只校验了
# 首跳地址，若 urllib 自动跟随后续 302 到内网，会绕过 SSRF 防线。
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_MAX_REDIRECTS = 5


def _request_json(url: str, *, method: str = "GET", body: bytes | None = None, timeout: int = 10) -> dict:
    """向外部 MCP 服务器发 JSON-RPC 请求，逐跳复检重定向（防 SSRF）。

    返回解析后的 JSON dict。重定向不自动跟随：每跳先 check_url 复检，
    通过后手动拼接新 URL 继续；命中内网/超次数则抛异常。
    """
    import urllib.error
    import urllib.parse

    from .safety import build_pinned_opener, resolve_public_url

    cur = url
    for _ in range(_MAX_REDIRECTS + 1):
        ok, err, resolved_ip = resolve_public_url(cur)
        if not ok:
            raise ValueError(f"拒绝访问不安全的服务器地址: {err}")
        opener = build_pinned_opener(resolved_ip, _NoRedirect())
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(cur, data=body, headers=headers, method=method)
        try:
            with opener.open(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # 不自动跟随的重定向会以 HTTPError(3xx) 抛出：取 Location 复检后手动跳转
            if e.code in (301, 302, 303, 307, 308):
                loc = e.headers.get("Location")
                if not loc:
                    raise RuntimeError("重定向缺少 Location") from e
                cur = urllib.parse.urljoin(cur, loc)
                continue
            raise
    raise RuntimeError("重定向次数过多")


class McpClient:
    """连接一个外部 MCP 服务器（HTTP + JSON-RPC），自动发现并注册远程工具。"""

    def __init__(self, name: str, url: str) -> None:
        self.name = name
        self.url = url.rstrip("/")
        self._tools: list[dict] = []

    async def list_tools(self) -> list[dict]:
        """请求远程服务器工具列表。"""
        data = await asyncio.to_thread(
            _request_json, self.url + "/tools", method="GET", timeout=10
        )
        self._tools = data.get("result", {}).get("tools", [])
        return self._tools

    async def call_tool(self, name: str, arguments: dict) -> str:
        """调用远程工具，返回文本结果。"""
        body = json.dumps({"name": name, "arguments": arguments}).encode("utf-8")
        data = await asyncio.to_thread(
            _request_json, self.url + "/call", method="POST", body=body, timeout=30
        )
        content = data.get("result", {}).get("content", [])
        parts = [c.get("text", "") for c in content if isinstance(c, dict)]
        return "\n".join(parts)


def _persist_servers() -> None:
    """把当前外部服务器登记表写盘（失败静默，不影响主流程）。原子写：临时文件 + os.replace。"""
    try:
        with _persist_lock:
            _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = [
                {"name": v["name"], "url": v["url"]}
                for v in _EXTERNAL_SERVERS.values()
            ]
            tmp = _PERSIST_PATH.with_suffix(_PERSIST_PATH.suffix + ".tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, _PERSIST_PATH)
    except Exception:
        logger.warning("[MCP] 外部服务器登记表写盘失败")


def _load_persisted() -> list[dict]:
    """读取持久化的外部服务器登记（name/url 列表）。"""
    try:
        if not _PERSIST_PATH.exists():
            return []
        with _persist_lock:
            data = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return [{"name": str(d.get("name", "")).strip(),
                 "url": str(d.get("url", "")).strip()}
                for d in data
                if isinstance(d, dict) and d.get("name") and d.get("url")]
    except Exception:
        return []


def list_external_servers() -> list[dict]:
    """列出已注册的外部 MCP 服务器及其工具数。"""
    result = []
    for name, info in _EXTERNAL_SERVERS.items():
        prefix = f"{name}::"
        count = sum(1 for t in ToolRegistry.list() if t.name.startswith(prefix))
        result.append({**info, "tools_count": count})
    return result


def unregister_external_server(name: str) -> bool:
    """卸载某外部服务器注册的工具，返回是否成功。"""
    if name not in _EXTERNAL_SERVERS:
        return False
    prefix = f"{name}::"
    for tname in ToolRegistry.tool_names():
        if tname.startswith(prefix):
            ToolRegistry.unregister(tname)
    _EXTERNAL_SERVERS.pop(name, None)
    _persist_servers()
    return True


async def restore_persisted_servers() -> int:
    """启动时恢复持久化的外部 MCP 服务器（逐个重连）。

    连接失败的服务器保留登记（等待下次启动重试），不影响其它恢复。
    返回成功恢复的数量。
    """
    saved = _load_persisted()
    if not saved:
        return 0
    ok_count = 0
    for entry in saved:
        name, url = entry["name"], entry["url"]
        try:
            success = await register_external_server(name, url)
            if success:
                ok_count += 1
                logger.info("[MCP] 已恢复外部服务器: {} ({})", name, url)
            else:
                logger.warning("[MCP] 恢复外部服务器失败（保留登记待重试）: {}", name)
        except Exception:
            logger.warning("[MCP] 恢复外部服务器异常: {}", name)
    return ok_count


async def register_external_server(name: str, url: str) -> bool:
    """连接外部 MCP 服务器并把其工具注册进全局注册表。

    注册名为 `{server_name}::{tool_name}`，避免与内置工具冲突。
    """
    # SSRF 防护：只允许公网 http(s) 地址（拒绝本机/内网/保留地址），
    # 避免借后端探测内网服务（同 web_fetch 的校验策略）
    from .safety import check_url

    url_ok, url_err = check_url(url)
    if not url_ok:
        logger.warning("[MCP] 拒绝注册不安全的服务器地址: {}（{}）", url, url_err)
        return False
    client = McpClient(name, url)
    try:
        tools = await client.list_tools()
    except Exception:
        return False

    async def make_proxy(tool_name: str) -> Any:
        async def proxy(**kwargs: Any) -> str:
            return await client.call_tool(tool_name, kwargs)

        return proxy

    # 若该服务器已注册过，先清掉旧工具（避免重复注册）
    unregister_external_server(name)

    for t in tools:
        tname = t.get("name", "")
        if not tname:
            continue
        desc = t.get("description", f"外部 MCP 工具（{name}/{tname}）")
        schema = t.get("inputSchema", {})
        # 注册为 name::tool_name，保持 namespace 隔离
        full = f"{name}::{tname}"
        ToolRegistry.register_func(
            name=full,
            description=f"[MCP:{name}] {desc}",
            func=await make_proxy(tname),
            input_schema=schema,
            # 外部服务器默认按"外部类 + 需确认"注册：不因远程声明而免确认
            category="external",
            danger_level="normal",
            needs_confirm=True,
        )

    _EXTERNAL_SERVERS[name] = {"name": name, "url": url, "tools": len(tools)}
    _persist_servers()
    return True
