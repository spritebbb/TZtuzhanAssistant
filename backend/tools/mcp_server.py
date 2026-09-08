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
# ---- 标准 MCP 客户端：连接外部 MCP 服务器（Streamable HTTP / HTTP+SSE）----

# 已注册的外部服务器登记表（运行时内存态）
_EXTERNAL_SERVERS: dict[str, dict] = {}


def _persist_servers() -> None:
    """把当前外部服务器登记表写盘（失败静默，不影响主流程）。原子写：临时文件 + os.replace。"""
    try:
        with _persist_lock:
            _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = [
                {"name": v["name"], "url": v["url"], "keywords": v.get("keywords") or []}
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
                 "url": str(d.get("url", "")).strip(),
                 "keywords": [str(k).strip() for k in (d.get("keywords") or []) if str(k).strip()]}
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


def mcp_tool_filter(user_text: str, skill_texts: list[str] | None = None):
    """按需注入：返回工具可见性判定函数（MCP 工具默认隐藏，命中才暴露）。

    命中条件（任一）：
    - ``AGENT_MCP_ALWAYS_ON=1``（演示用，全部 MCP 工具常驻）；
    - 本轮用户消息 / 命中的技能正文里出现了「服务器名 / 该服务器的关键词 /
      该服务器的某个工具名」（工具名按 ASCII 词边界匹配，中文紧邻也命中）。

    没注册任何 MCP 服务器、或 always-on 时返回 None（表示无需过滤，零开销）。
    """
    if not _EXTERNAL_SERVERS or getattr(config, "agent_mcp_always_on", False):
        return None
    import re as _re

    haystack = " ".join([str(user_text or ""), *(skill_texts or [])])
    visible: set[str] = set()
    for name, info in _EXTERNAL_SERVERS.items():
        prefix = f"{name}::"
        needles = [name, *(info.get("keywords") or [])]
        hit = any(n and n in haystack for n in needles)
        if not hit:
            for tool_name in ToolRegistry.tool_names():
                if not tool_name.startswith(prefix):
                    continue
                short = tool_name[len(prefix):]
                if _re.search(rf"(?<![A-Za-z0-9_]){_re.escape(short)}(?![A-Za-z0-9_])", haystack):
                    hit = True
                    break
        if hit:
            visible.update(t for t in ToolRegistry.tool_names() if t.startswith(prefix))
    if visible:
        logger.info("[MCP] 按需注入外部工具 {} 个：{}", len(visible), sorted(visible)[:5])

    def _predicate(tool) -> bool:
        owner = str(getattr(tool, "owner", "") or "")
        if not owner.startswith("mcp:"):
            return True                      # 本地工具永远可见
        return tool.name in visible          # 外部工具命中才可见

    return _predicate


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
            success = await register_external_server(
                name, url, keywords=entry.get("keywords") or []
            )
            if success:
                ok_count += 1
                logger.info("[MCP] 已恢复外部服务器: {} ({})", name, url)
            else:
                logger.warning("[MCP] 恢复外部服务器失败（保留登记待重试）: {}", name)
        except Exception:
            logger.warning("[MCP] 恢复外部服务器异常: {}", name)
    return ok_count


async def register_external_server(name: str, url: str, *,
                                   keywords: list[str] | None = None) -> bool:
    """连接外部 MCP 服务器并把其工具注册进全局注册表（标准协议）。

    传输自动探测：先试 Streamable HTTP，失败回退旧版 HTTP+SSE。
    注册名为 `{server_name}::{tool_name}`，避免与内置工具冲突。
    安全：非回环地址沿用 SSRF 防护；回环/内网地址仅在
    ``AGENT_MCP_ALLOW_LOOPBACK=1`` 时放行（本地 MCP 服务器需要）。
    """
    from .mcp_client import McpClient as _StandardMcpClient

    allow_private = bool(getattr(config, "agent_mcp_allow_loopback", False))
    client = _StandardMcpClient(name, url, allow_private=allow_private)
    try:
        tools = await asyncio.to_thread(client.list_tools)
    except Exception as exc:
        logger.warning("[MCP] 连接外部服务器失败: {}（{}）", name, exc)
        return False

    async def make_proxy(tool_name: str) -> Any:
        async def proxy(**kwargs: Any) -> str:
            return await asyncio.to_thread(client.call_tool, tool_name, kwargs)

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
            owner=f"mcp:{name}",
        )

    _EXTERNAL_SERVERS[name] = {
        "name": name, "url": url, "tools": len(tools),
        "keywords": list(keywords or []),
    }
    _persist_servers()
    return True
