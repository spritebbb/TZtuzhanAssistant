# -*- coding: utf-8 -*-
"""M3.3 共同目标 API。"""
import asyncio
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ..core import goals
from ..core.activities import ActivityError
from ..core.persona_profiles import active_user_id

router = APIRouter(prefix="/api/goals", tags=["goals"])


class StartGoalBody(BaseModel):
    title: str
    motivation: str = ""
    next_step: str
    support_mode: str = "companion"
    reminder_at: str | None = None


class UpdateGoalBody(BaseModel):
    motivation: str | None = None
    next_step: str | None = None
    support_mode: str | None = None
    reminder_at: str | None = None


class ProgressBody(BaseModel):
    content: str
    percent: int | None = Field(default=None, ge=0, le=100)
    next_step: str = ""


class CompleteBody(BaseModel):
    create_artifact: bool = True


def _error(exc: ActivityError, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=status_code)


@router.get("")
async def api_list_goals():
    rows = await asyncio.to_thread(goals.list_goals, active_user_id())
    return {"ok": True, "goals": rows}


@router.get("/{activity_id}")
async def api_get_goal(activity_id: int):
    row = await asyncio.to_thread(goals.get_goal, active_user_id(), activity_id)
    if row is None:
        return JSONResponse({"ok": False, "error": "共同目标不存在"}, status_code=404)
    return {"ok": True, "goal": row}


@router.post("")
async def api_start_goal(body: StartGoalBody):
    try:
        row = await asyncio.to_thread(
            goals.start_goal,
            active_user_id(),
            body.title,
            body.motivation,
            body.next_step,
            body.support_mode,
            body.reminder_at,
        )
    except ActivityError as exc:
        return _error(exc)
    return {"ok": True, "goal": row}


@router.patch("/{activity_id}")
async def api_update_goal(activity_id: int, body: UpdateGoalBody):
    try:
        row = await asyncio.to_thread(
            goals.update_goal,
            active_user_id(),
            activity_id,
            motivation=body.motivation,
            next_step=body.next_step,
            support_mode=body.support_mode,
            reminder_at=body.reminder_at,
        )
    except ActivityError as exc:
        return _error(exc)
    return {"ok": True, "goal": row}


@router.post("/{activity_id}/progress")
async def api_add_progress(activity_id: int, body: ProgressBody):
    try:
        row = await asyncio.to_thread(
            goals.add_progress,
            active_user_id(),
            activity_id,
            body.content,
            percent=body.percent,
            next_step=body.next_step,
        )
    except ActivityError as exc:
        return _error(exc)
    return {"ok": True, "goal": row}


async def _status_call(fn, activity_id: int):
    try:
        row = await asyncio.to_thread(fn, active_user_id(), activity_id)
    except ActivityError as exc:
        return _error(exc)
    return {"ok": True, "goal": row}


@router.post("/{activity_id}/pause")
async def api_pause_goal(activity_id: int):
    return await _status_call(goals.pause_goal, activity_id)


@router.post("/{activity_id}/resume")
async def api_resume_goal(activity_id: int):
    return await _status_call(goals.resume_goal, activity_id)


@router.post("/{activity_id}/cancel")
async def api_cancel_goal(activity_id: int):
    return await _status_call(goals.cancel_goal, activity_id)


@router.post("/{activity_id}/complete")
async def api_complete_goal(activity_id: int, body: CompleteBody):
    try:
        row = await asyncio.to_thread(
            goals.complete_goal,
            active_user_id(),
            activity_id,
            create_artifact=body.create_artifact,
        )
    except ActivityError as exc:
        return _error(exc)
    return {"ok": True, "goal": row}


@router.get("/{activity_id}/export")
async def api_export_goal(activity_id: int, format: str = Query("md", pattern="^md$")):
    try:
        goal = await asyncio.to_thread(goals.get_goal, active_user_id(), activity_id)
        content = await asyncio.to_thread(goals.export_markdown, active_user_id(), activity_id)
    except ActivityError as exc:
        return _error(exc, 404)
    filename = quote(f"{goal['title'] if goal else '共同目标'}.md")
    return Response(
        content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
