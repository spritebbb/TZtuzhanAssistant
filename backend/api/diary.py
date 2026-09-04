# -*- coding: utf-8 -*-
"""菟菚私人日记与《观察人类》阶段研究记录，只读浏览接口。"""
from __future__ import annotations

import re

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from .chat import _user_id
from ..core.userdb import get_diary, list_diaries, list_research_reports

router = APIRouter(prefix="/api", tags=["diary"])
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@router.get("/diary")
async def api_diaries(session_id: str = "", limit: int = Query(60, ge=1, le=365)):
    uid = _user_id(session_id) if session_id else "assistant-main"
    return {"ok": True, "diaries": list_diaries(uid, limit)}


@router.get("/diary/{day}")
async def api_diary(day: str, session_id: str = ""):
    if not _DAY_RE.fullmatch(day):
        return JSONResponse({"ok": False, "error": "日期格式应为 YYYY-MM-DD"}, status_code=400)
    uid = _user_id(session_id) if session_id else "assistant-main"
    item = get_diary(uid, day)
    if not item:
        return JSONResponse({"ok": False, "error": "这一天还没有日记"}, status_code=404)
    return {"ok": True, "diary": item}


@router.get("/research-reports")
async def api_research_reports(session_id: str = "", limit: int = Query(24, ge=1, le=100)):
    uid = _user_id(session_id) if session_id else "assistant-main"
    return {"ok": True, "reports": list_research_reports(uid, limit)}
