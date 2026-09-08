# -*- coding: utf-8 -*-
"""D3 共同活动 API（首期：共读）。"""
from __future__ import annotations

import asyncio
from urllib.parse import quote

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse, Response
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


class ViewpointBody(BaseModel):
    role: str = Field(pattern="^(user|tuzhan|shared)$")
    content: str = Field(max_length=2_000)


class QuestionBody(BaseModel):
    user_viewpoint: str = Field(default="", max_length=2_000)


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


@router.post("/{activity_id}/pause")
async def api_pause_activity(activity_id: int):
    try:
        row = await asyncio.to_thread(activities.pause_activity, _active_user_id(), activity_id)
    except activities.ActivityError as exc:
        return _error(exc, 404)
    return {"ok": True, "activity": row}


@router.post("/{activity_id}/cancel")
async def api_cancel_activity(activity_id: int):
    try:
        row = await asyncio.to_thread(activities.cancel_activity, _active_user_id(), activity_id)
    except activities.ActivityError as exc:
        return _error(exc, 404)
    return {"ok": True, "activity": row}


@router.post("/{activity_id}/question")
async def api_discussion_question(activity_id: int, body: QuestionBody):
    try:
        question = await activities.propose_discussion_question(
            _active_user_id(), activity_id, body.user_viewpoint
        )
    except activities.ActivityError as exc:
        return _error(exc, 404)
    return {"ok": True, "question": question}


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


@router.put("/{activity_id}/viewpoint")
async def api_save_viewpoint(activity_id: int, body: ViewpointBody):
    try:
        row = await asyncio.to_thread(
            activities.save_viewpoint,
            _active_user_id(),
            activity_id,
            body.role,
            body.content,
        )
    except activities.ActivityError as exc:
        return _error(exc)
    # M8-B：非共读活动返回观点集合；共读保持返回活动详情。
    if isinstance(row, dict) and row.get("kind") == "reading":
        return {"ok": True, "activity": row}
    return row


@router.get("/{activity_id}/viewpoints")
async def api_get_viewpoints(activity_id: int):
    try:
        return await asyncio.to_thread(activities.get_viewpoints, _active_user_id(), activity_id)
    except activities.ActivityError as exc:
        return _error(exc, 404)


@router.post("/{activity_id}/viewpoint-draft")
async def api_viewpoint_draft(activity_id: int):
    try:
        return await activities.viewpoint_draft(_active_user_id(), activity_id)
    except activities.ActivityError as exc:
        return _error(exc)


@router.post("/{activity_id}/complete")
async def api_complete_activity(activity_id: int):
    try:
        row = await asyncio.to_thread(
            activities.complete_activity, _active_user_id(), activity_id
        )
    except activities.ActivityError as exc:
        return _error(exc, 404)
    return {"ok": True, "activity": row}


# ---- F05 阅读地图与书签 ----


@router.get("/{activity_id}/reading-map")
async def api_reading_map(activity_id: int):
    from ..core import reading_map

    try:
        data = await asyncio.to_thread(
            reading_map.get_map, _active_user_id(), activity_id)
    except reading_map.ReadingMapError as exc:
        return _error(exc, 404)
    return {"ok": True, **data}


@router.post("/{activity_id}/segments/{index}/finish")
async def api_finish_segment(activity_id: int, index: int,
                             expected_version: str | None = Body(None, embed=True)):
    from ..core import reading_map

    try:
        data = await asyncio.to_thread(
            reading_map.finish_segment, _active_user_id(), activity_id, index,
            expected_version=expected_version)
    except reading_map.ReadingMapConflict as exc:
        return _error(exc, 409)
    except reading_map.ReadingMapError as exc:
        return _error(exc, 404)
    return {"ok": True, **data}


@router.post("/{activity_id}/segments/{segment_id}/bookmark-draft")
async def api_bookmark_draft(activity_id: int, segment_id: int):
    from ..core import reading_map

    try:
        draft = await asyncio.to_thread(
            reading_map.bookmark_draft, _active_user_id(), segment_id)
    except reading_map.ReadingMapError as exc:
        return _error(exc, 404)
    return {"ok": True, "draft": draft}


@router.put("/{activity_id}/segments/{segment_id}/bookmark")
async def api_save_bookmark(activity_id: int, segment_id: int,
                            user_view: str = Body("", embed=True),
                            tuzhan_view: str = Body("", embed=True),
                            summary: str = Body("", embed=True)):
    from ..core import reading_map

    try:
        saved = await asyncio.to_thread(
            reading_map.save_bookmark, _active_user_id(), segment_id,
            user_view=user_view, tuzhan_view=tuzhan_view, summary=summary)
    except reading_map.ReadingMapError as exc:
        return _error(exc, 422)
    return {"ok": True, "bookmark": saved}


@router.get("/{activity_id}/export")
async def api_export_activity(
    activity_id: int,
    format: str = Query("md", pattern="^md$"),
):
    try:
        detail = await asyncio.to_thread(activities.get_activity, _active_user_id(), activity_id)
        content = await asyncio.to_thread(activities.export_markdown, _active_user_id(), activity_id)
    except activities.ActivityError as exc:
        return _error(exc, 404)
    filename = quote(f"{detail['filename'] if detail else '共读记录'}.md")
    return Response(
        content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
