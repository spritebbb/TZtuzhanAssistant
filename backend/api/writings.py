# -*- coding: utf-8 -*-
"""M3.4 共同创作 API（首切片：轮流续写）。"""
import asyncio
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ..core import cowriting
from ..core.activities import ActivityError
from ..core.persona_profiles import active_user_id

router = APIRouter(prefix="/api/writings", tags=["writings"])


class StartWritingBody(BaseModel):
    title: str
    premise: str = ""
    subtype: str = "story"   # L02：story / world / character


class OutlineBody(BaseModel):
    outline: dict
    expected_version: int | None = None


class TurnBody(BaseModel):
    content: str = Field(min_length=1, max_length=2_000)


class CompleteBody(BaseModel):
    create_artifact: bool = True


def _error(exc: ActivityError, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=status_code)


@router.get("")
async def api_list_writings():
    rows = await asyncio.to_thread(cowriting.list_writings, active_user_id())
    return {"ok": True, "writings": rows}


@router.get("/{activity_id}")
async def api_get_writing(activity_id: int):
    row = await asyncio.to_thread(cowriting.get_writing, active_user_id(), activity_id)
    if row is None:
        return JSONResponse({"ok": False, "error": "这个故事不存在"}, status_code=404)
    return {"ok": True, "writing": row}


@router.post("")
async def api_start_writing(body: StartWritingBody):
    try:
        row = await asyncio.to_thread(
            cowriting.start_writing, active_user_id(), body.title, body.premise,
            subtype=body.subtype
        )
    except ActivityError as exc:
        return _error(exc)
    return {"ok": True, "writing": row}


@router.post("/{activity_id}/outline-draft")
async def api_outline_draft(activity_id: int):
    """L02 结构化大纲草稿（世界观/角色设定）。"""
    try:
        draft = await asyncio.to_thread(
            cowriting.propose_outline, active_user_id(), activity_id)
    except ActivityError as exc:
        return _error(exc)
    return {"ok": True, **draft}


@router.put("/{activity_id}/outline")
async def api_confirm_outline(activity_id: int, body: OutlineBody):
    """用户确认大纲（版本化）。"""
    try:
        saved = await asyncio.to_thread(
            cowriting.confirm_outline, active_user_id(), activity_id, body.outline,
            expected_version=body.expected_version)
    except ActivityError as exc:
        return _error(exc)
    return {"ok": True, **saved}


@router.post("/{activity_id}/turn")
async def api_add_user_turn(activity_id: int, body: TurnBody):
    try:
        row = await asyncio.to_thread(
            cowriting.add_user_turn, active_user_id(), activity_id, body.content
        )
    except ActivityError as exc:
        return _error(exc)
    return {"ok": True, "writing": row}


@router.post("/{activity_id}/tuzhan-turn")
async def api_tuzhan_turn(activity_id: int):
    try:
        row = await cowriting.generate_tuzhan_turn(active_user_id(), activity_id)
    except ActivityError as exc:
        return _error(exc)
    except Exception:
        return _error(ActivityError("她这轮没接上，稍后再试一次"), 502)
    return {"ok": True, "writing": row}


async def _status_call(fn, activity_id: int):
    try:
        row = await asyncio.to_thread(fn, active_user_id(), activity_id)
    except ActivityError as exc:
        return _error(exc)
    return {"ok": True, "writing": row}


@router.post("/{activity_id}/pause")
async def api_pause_writing(activity_id: int):
    return await _status_call(cowriting.pause_writing, activity_id)


@router.post("/{activity_id}/resume")
async def api_resume_writing(activity_id: int):
    return await _status_call(cowriting.resume_writing, activity_id)


@router.post("/{activity_id}/cancel")
async def api_cancel_writing(activity_id: int):
    return await _status_call(cowriting.cancel_writing, activity_id)


@router.post("/{activity_id}/complete")
async def api_complete_writing(activity_id: int, body: CompleteBody):
    try:
        row = await asyncio.to_thread(
            cowriting.complete_writing,
            active_user_id(),
            activity_id,
            create_artifact=body.create_artifact,
        )
    except ActivityError as exc:
        return _error(exc)
    return {"ok": True, "writing": row}


@router.get("/{activity_id}/export")
async def api_export_writing(activity_id: int, format: str = Query("md", pattern="^md$")):
    try:
        writing = await asyncio.to_thread(cowriting.get_writing, active_user_id(), activity_id)
        content = await asyncio.to_thread(cowriting.export_markdown, active_user_id(), activity_id)
    except ActivityError as exc:
        return _error(exc, 404)
    filename = quote(f"{writing['title'] if writing else '共同故事'}.md")
    return Response(
        content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
