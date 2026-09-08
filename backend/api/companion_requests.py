# -*- coding: utf-8 -*-
"""G04 求助与欲望 API：列出她的请求 + 回应（与聊天意图共用同一 respond）。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from .chat import _user_id  # noqa: F401  （保持与其余路由一致的依赖约定）
from ..core import companion_requests as cr
from ..core.log import logger
from ..core.persona_profiles import active_user_id

router = APIRouter(prefix="/api/companion-requests", tags=["companion-requests"])


@router.get("")
async def api_list_requests():
    """她的请求列表（新→旧）；offer 未过期时可在此回应。"""
    uid = active_user_id()
    items = await asyncio.to_thread(cr.list_requests, uid)
    return {"ok": True, "requests": items}


@router.post("/{request_id}/respond")
async def api_respond(request_id: int, action: str = Body(..., embed=True)):
    """接受或拒绝一次求助；接受会写入角色虚构产物。"""
    if action not in {"accept", "decline"}:
        return JSONResponse({"ok": False, "error": "不支持的回应"}, status_code=400)
    uid = active_user_id()
    try:
        result = await asyncio.to_thread(cr.respond, uid, request_id, action)
    except cr.CompanionRequestError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    logger.info("[求助] API 回应 #{}：{}", request_id, result.get("status"))
    return {"ok": True, **result}
