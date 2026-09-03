# -*- coding: utf-8 -*-
"""久别问候接口。"""
from __future__ import annotations

from fastapi import APIRouter

from ..api.chat import _user_id
from ..core.greeting import greeting_for

router = APIRouter(prefix="/api", tags=["greeting"])


@router.get("/greeting")
async def api_greeting(session_id: str = ""):
    """久别主动问候：距上次访问超阈值时生成一句菟菚的问候。"""
    if not session_id:
        return {"ok": True, "greeting": None}
    text = await greeting_for(_user_id(session_id), session_id)
    return {"ok": True, "greeting": text}