# -*- coding: utf-8 -*-
"""会话接口 + 归档接口（单一会话模式）。

单一会话模式下只有一个固定会话（id='current'），因此不再提供
新建/删除/重命名/列表/搜索等多会话 CRUD，仅保留：
- 归档（结束并归档当前会话 / 归档列表 / 归档详情）
- 读取当前会话消息（GET /{session_id}，session_id 恒为 'current'）
- 导出当前会话（markdown / json）
"""
from __future__ import annotations

import datetime as _dt

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from ..session.store import (
    archive_current,
    get_archive,
    get_messages,
    list_archives,
    search_archives,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


# ---- 归档（单一会话模式下新增）----

@router.post("/archive")
async def api_sessions_archive():
    """结束并归档当前会话：打包当前所有消息存入 archives，清空当前会话。"""
    result = await archive_current()
    if result is None:
        return {"ok": True, "archived": False, "message": "当前会话没有可归档的内容"}
    return {"ok": True, "archived": True, "archive": result}


@router.get("/archives")
async def api_archives_list():
    """归档列表（只读回看）。"""
    return {"ok": True, "archives": await list_archives()}


@router.get("/archives/search")
async def api_archives_search(q: str = ""):
    """按关键词搜索归档（标题 + 内容），返回摘要列表（标题/条数/命中预览）。

    只返回列表展示所需字段，不含完整消息；点进详情再走 /archives/{id} 拉完整内容。
    注意：必须声明在 /archives/{archive_id} 之前，否则会被动态路由吞掉。
    """
    results = await search_archives(q)
    return {"ok": True, "results": results}


@router.get("/archives/{archive_id}")
async def api_archives_get(archive_id: str):
    """单条归档详情（含完整消息）。"""
    data = await get_archive(archive_id)
    if data is None:
        return JSONResponse({"ok": False, "error": "归档不存在"}, status_code=404)
    return {"ok": True, "archive": data}


# ---- 单一会话读取 / 导出 ----

@router.get("/{session_id}")
async def api_sessions_get(session_id: str):
    msgs = await get_messages(session_id)
    if msgs is None:
        return JSONResponse({"ok": False, "error": "会话不存在"}, status_code=404)
    return msgs


@router.get("/{session_id}/export")
async def api_sessions_export(session_id: str, fmt: str = "md"):
    """导出会话：fmt=md（Markdown）/ json（原始消息）。"""
    msgs = await get_messages(session_id)
    if msgs is None:
        return JSONResponse({"ok": False, "error": "会话不存在"}, status_code=404)

    title = "新会话"
    if fmt == "json":
        return {
            "ok": True,
            "session_id": session_id,
            "title": title,
            "messages": msgs,
        }
    # Markdown 导出
    lines = [f"# {title}", "", "> 导出自 菟菚 桌面助手", ""]
    for m in msgs:
        who = "你" if m["role"] == "user" else "菟菚"
        content = (m.get("content") or "").strip()
        img = m.get("image") or ""
        if not content and img:
            content = "（图片）"
        ts = ""
        if m.get("ts"):
            ts = _dt.datetime.fromtimestamp(m["ts"]).strftime("%Y-%m-%d %H:%M")
        lines.append(f"**{who}**" + (f"（{ts}）" if ts else "") + f": {content}")
        if img:
            lines.append(f"![]({img})")
        lines.append("")
    return Response(
        "\n".join(lines),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="session-{session_id}.md"'},
    )
