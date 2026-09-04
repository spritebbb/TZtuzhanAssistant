# -*- coding: utf-8 -*-
"""D3 共同活动 API（首期：共读）。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..core import activities

router = APIRouter(prefix="/api/activities", tags=["activities"])


def _active_user_id() -> str:
    """Use the active persona namespace when that optional feature is present."""
    try:
        from ..core.persona_profiles import active_user_id
    except ImportError:
        return "assistant-main"
    return active_user_id()


class StartReadingBody(BaseModel):
    document_id: int = Field(gt=0)


class PositionBody(BaseModel):
    position: int = Field(ge=0)


class NoteBody(BaseModel):
    content: str = Field(max_length=2_000)


def _error(exc: activities.ActivityError, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=status_code)


@router.get("")
async def api_activity_list():
    rows = await asyncio.to_thread(activities.list_reading_activities, _active_user_id())
    return {"ok": True, "activities": rows}


@router.get("/{activity_id}")
async def api_activity_get(activity_id: int):
    row = await asyncio.to_thread(activities.get_activity, _active_user_id(), activity_id)
    if row is None:
        return JSONResponse({"ok": False, "error": "共读记录不存在"}, status_code=404)
    return {"ok": True, "activity": row}


@router.post("/reading")
async def api_start_reading(body: StartReadingBody):
    try:
        row = await asyncio.to_thread(activities.start_reading, _active_user_id(), body.document_id)
    except activities.ActivityError as exc:
        return _error(exc, 404)
    return {"ok": True, "activity": row}


@router.post("/{activity_id}/resume")
async def api_resume_activity(activity_id: int):
    try:
        row = await asyncio.to_thread(activities.resume_activity, _active_user_id(), activity_id)
    except activities.ActivityError as exc:
        return _error(exc, 404)
    return {"ok": True, "activity": row}


@router.put("/{activity_id}/position")
async def api_set_position(activity_id: int, body: PositionBody):
    try:
        row = await asyncio.to_thread(
            activities.set_position, _active_user_id(), activity_id, body.position
        )
    except activities.ActivityError as exc:
        return _error(exc)
    return {"ok": True, "activity": row}


@router.put("/{activity_id}/note")
async def api_save_note(activity_id: int, body: NoteBody):
    try:
        row = await asyncio.to_thread(
            activities.save_note, _active_user_id(), activity_id, body.content
        )
    except activities.ActivityError as exc:
        return _error(exc)
    return {"ok": True, "activity": row}


@router.post("/{activity_id}/complete")
async def api_complete_activity(activity_id: int):
    try:
        row = await asyncio.to_thread(
            activities.complete_activity, _active_user_id(), activity_id
        )
    except activities.ActivityError as exc:
        return _error(exc, 404)
    return {"ok": True, "activity": row}
