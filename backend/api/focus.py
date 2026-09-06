# -*- coding: utf-8 -*-
"""M3.2 专注陪伴 API：25/50 分钟安静模式。"""
from __future__ import annotations

import asyncio
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ..core import focus
from ..core.activities import ActivityError
from ..core.config import config

router = APIRouter(prefix="/api/focus", tags=["focus"])


def _active_user_id() -> str:
    """Use the active persona namespace when that optional feature is present."""
    try:
        from ..core.persona_profiles import active_user_id
    except ImportError:
        return "assistant-main"
    return active_user_id()


class StartFocusBody(BaseModel):
    minutes: int = Field(ge=1, le=180)


def _error(exc: ActivityError, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=status_code)


def _disabled() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "专注陪伴未开启"}, status_code=403)


def _schedule_wrapup(background: BackgroundTasks, user_id: str, detail: dict) -> None:
    """复盘异步生成投递，不阻塞完成响应；不够格的完成（秒开秒关/取消）不触发。"""
    if focus.wrapup_eligible(detail):
        background.add_task(focus.maybe_send_wrapup, user_id, int(detail["id"]))


@router.post("")
async def api_start_focus(body: StartFocusBody, background: BackgroundTasks):
    if not config.focus_enabled:
        return _disabled()
    user_id = _active_user_id()
    try:
        detail = await asyncio.to_thread(focus.start_focus, user_id, body.minutes)
    except ActivityError as exc:
        return _error(exc)
    return {"ok": True, "focus": detail}


@router.get("")
async def api_list_focus():
    rows = await asyncio.to_thread(focus.list_focus, _active_user_id())
    return {"ok": True, "sessions": rows}


@router.get("/current")
async def api_current_focus(background: BackgroundTasks):
    """当前未结束的专注；自然到点的在此惰性结算，并按资格安排复盘。"""
    user_id = _active_user_id()
    detail, just_finished = await asyncio.to_thread(focus.current_focus, user_id)
    if just_finished and detail is not None:
        _schedule_wrapup(background, user_id, detail)
    return {"ok": True, "focus": detail, "just_finished": just_finished}


@router.post("/{activity_id}/pause")
async def api_pause_focus(activity_id: int):
    try:
        detail = await asyncio.to_thread(focus.pause_focus, _active_user_id(), activity_id)
    except ActivityError as exc:
        return _error(exc, 404)
    return {"ok": True, "focus": detail}


@router.post("/{activity_id}/resume")
async def api_resume_focus(activity_id: int):
    try:
        detail = await asyncio.to_thread(focus.resume_focus, _active_user_id(), activity_id)
    except ActivityError as exc:
        return _error(exc, 404)
    return {"ok": True, "focus": detail}


@router.post("/{activity_id}/complete")
async def api_complete_focus(activity_id: int, background: BackgroundTasks):
    user_id = _active_user_id()
    try:
        detail = await asyncio.to_thread(focus.complete_focus, user_id, activity_id)
    except ActivityError as exc:
        return _error(exc, 404)
    _schedule_wrapup(background, user_id, detail)
    return {"ok": True, "focus": detail}


@router.post("/{activity_id}/cancel")
async def api_cancel_focus(activity_id: int):
    try:
        detail = await asyncio.to_thread(focus.cancel_focus, _active_user_id(), activity_id)
    except ActivityError as exc:
        return _error(exc, 404)
    return {"ok": True, "focus": detail}


@router.get("/{activity_id}/export")
async def api_export_focus(
    activity_id: int,
    format: str = Query("md", pattern="^md$"),
):
    try:
        detail = await asyncio.to_thread(focus.get_focus, _active_user_id(), activity_id)
        content = await asyncio.to_thread(focus.export_markdown, _active_user_id(), activity_id)
    except ActivityError as exc:
        return _error(exc, 404)
    filename = quote(f"{detail['title'] if detail else '专注记录'}.md")
    return Response(
        content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
