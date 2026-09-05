# -*- coding: utf-8 -*-
"""M3.4 共同清单（歌单/书单）API。"""
import asyncio
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ..core import colists
from ..core.activities import ActivityError
from ..core.persona_profiles import active_user_id

router = APIRouter(prefix="/api/lists", tags=["lists"])


class StartListBody(BaseModel):
    title: str
    list_kind: str = "song"


class ItemBody(BaseModel):
    title: str
    creator: str = ""
    note: str = ""
    added_by: str = "user"


def _error(exc: ActivityError, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=status_code)


@router.get("")
async def api_list_lists():
    rows = await asyncio.to_thread(colists.list_lists, active_user_id())
    return {"ok": True, "lists": rows}


@router.get("/{activity_id}")
async def api_get_list(activity_id: int):
    row = await asyncio.to_thread(colists.get_list, active_user_id(), activity_id)
    if row is None:
        return JSONResponse({"ok": False, "error": "这份清单不存在"}, status_code=404)
    return {"ok": True, "list": row}


@router.post("")
async def api_start_list(body: StartListBody):
    try:
        row = await asyncio.to_thread(
            colists.start_list, active_user_id(), body.title, body.list_kind
        )
    except ActivityError as exc:
        return _error(exc)
    return {"ok": True, "list": row}


@router.post("/{activity_id}/items")
async def api_add_item(activity_id: int, body: ItemBody):
    try:
        row = await asyncio.to_thread(
            colists.add_item,
            active_user_id(),
            activity_id,
            body.title,
            body.creator,
            body.note,
            body.added_by,
        )
    except ActivityError as exc:
        return _error(exc)
    return {"ok": True, "list": row}


@router.delete("/{activity_id}/items/{item_id}")
async def api_remove_item(activity_id: int, item_id: int):
    try:
        row = await asyncio.to_thread(
            colists.remove_item, active_user_id(), activity_id, item_id
        )
    except ActivityError as exc:
        return _error(exc)
    return {"ok": True, "list": row}


async def _status_call(fn, activity_id: int):
    try:
        row = await asyncio.to_thread(fn, active_user_id(), activity_id)
    except ActivityError as exc:
        return _error(exc)
    return {"ok": True, "list": row}


@router.post("/{activity_id}/pause")
async def api_pause_list(activity_id: int):
    return await _status_call(colists.pause_list, activity_id)


@router.post("/{activity_id}/resume")
async def api_resume_list(activity_id: int):
    return await _status_call(colists.resume_list, activity_id)


@router.post("/{activity_id}/cancel")
async def api_cancel_list(activity_id: int):
    return await _status_call(colists.cancel_list, activity_id)


@router.post("/{activity_id}/complete")
async def api_complete_list(activity_id: int):
    try:
        row = await asyncio.to_thread(colists.complete_list, active_user_id(), activity_id)
    except ActivityError as exc:
        return _error(exc)
    return {"ok": True, "list": row}


@router.get("/{activity_id}/export")
async def api_export_list(activity_id: int, format: str = Query("md", pattern="^md$")):
    try:
        detail = await asyncio.to_thread(colists.get_list, active_user_id(), activity_id)
        content = await asyncio.to_thread(colists.export_markdown, active_user_id(), activity_id)
    except ActivityError as exc:
        return _error(exc, 404)
    filename = quote(f"{detail['title'] if detail else '共同清单'}.md")
    return Response(
        content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
