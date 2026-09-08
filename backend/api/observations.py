# -*- coding: utf-8 -*-
"""L02 观察日志 API：开/记/暂停/继续/收尾/取消 + 列表。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..core import observations
from ..core.activities import ActivityError
from ..core.persona_profiles import active_user_id

router = APIRouter(prefix="/api/observations", tags=["observations"])


class StartBody(BaseModel):
    title: str = Field(min_length=1, max_length=60)


class EntryBody(BaseModel):
    content: str = Field(min_length=1, max_length=400)
    observer: str = "user"
    source_type: str = ""
    source_id: int | None = None
    confidence: float = 1.0


def _error(exc: ActivityError, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=status_code)


@router.get("")
async def api_list():
    rows = await asyncio.to_thread(observations.list_observations, active_user_id())
    return {"ok": True, "observations": rows}


@router.post("")
async def api_start(body: StartBody):
    try:
        row = await asyncio.to_thread(observations.start_observation, active_user_id(), body.title)
    except ActivityError as exc:
        return _error(exc)
    return {"ok": True, "observation": row}


@router.get("/{activity_id}")
async def api_get(activity_id: int):
    row = await asyncio.to_thread(observations.get_observation, active_user_id(), activity_id)
    if row is None:
        return JSONResponse({"ok": False, "error": "这本观察日志不存在"}, status_code=404)
    return {"ok": True, "observation": row}


@router.post("/{activity_id}/entries")
async def api_add_entry(activity_id: int, body: EntryBody):
    try:
        row = await asyncio.to_thread(
            observations.add_entry, active_user_id(), activity_id, body.content,
            observer=body.observer, source_type=body.source_type,
            source_id=body.source_id, confidence=body.confidence)
    except ActivityError as exc:
        return _error(exc)
    return {"ok": True, "observation": row}


@router.post("/{activity_id}/pause")
async def api_pause(activity_id: int):
    try:
        return {"ok": True, "observation": await asyncio.to_thread(
            observations.pause_observation, active_user_id(), activity_id)}
    except ActivityError as exc:
        return _error(exc, 404)


@router.post("/{activity_id}/resume")
async def api_resume(activity_id: int):
    try:
        return {"ok": True, "observation": await asyncio.to_thread(
            observations.resume_observation, active_user_id(), activity_id)}
    except ActivityError as exc:
        return _error(exc, 404)


@router.post("/{activity_id}/cancel")
async def api_cancel(activity_id: int):
    try:
        return {"ok": True, "observation": await asyncio.to_thread(
            observations.cancel_observation, active_user_id(), activity_id)}
    except ActivityError as exc:
        return _error(exc, 404)


@router.post("/{activity_id}/complete")
async def api_complete(activity_id: int):
    try:
        return {"ok": True, "observation": await asyncio.to_thread(
            observations.complete_observation, active_user_id(), activity_id)}
    except ActivityError as exc:
        return _error(exc, 404)
