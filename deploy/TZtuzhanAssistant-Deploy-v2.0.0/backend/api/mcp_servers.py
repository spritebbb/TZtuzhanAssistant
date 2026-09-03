# -*- coding: utf-8 -*-
"""MCP 服务器管理 API：注册/列表/删除外部 MCP 服务器。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..tools.mcp_server import (
    list_external_servers,
    register_external_server,
    unregister_external_server,
)

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@router.get("/servers")
async def api_mcp_list_servers():
    """列出已注册的外部 MCP 服务器及其工具数。"""
    return {"ok": True, "servers": list_external_servers()}


@router.post("/servers")
async def api_mcp_register_server(request: Request):
    """注册一个外部 MCP 服务器。body: {"name": "...", "url": "..."}"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "JSON 解析失败"}, status_code=400)
    name = (body.get("name") or "").strip()
    url = (body.get("url") or "").strip()
    if not name or not url:
        return JSONResponse({"ok": False, "error": "缺少 name 或 url"}, status_code=400)
    ok = await register_external_server(name, url)
    if not ok:
        return JSONResponse({"ok": False, "error": "连接失败，请检查服务器地址"}, status_code=502)
    return {"ok": True, "server": {"name": name, "url": url}}


@router.delete("/servers/{name}")
async def api_mcp_delete_server(name: str):
    """卸载已注册的 MCP 服务器。"""
    ok = unregister_external_server(name)
    return {"ok": ok}