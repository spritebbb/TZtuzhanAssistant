# -*- coding: utf-8 -*-
"""M8 双视角叙事 API：同一经历的双方解释页。feature flag 关闭时全部 403。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..core import dual_perspectives
from ..core.config import config
from ..core.persona_profiles import active_user_id

router = APIRouter(prefix="/api/dual-perspectives", tags=["dual-perspectives"])


def _disabled() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "双视角叙事未开启"}, status_code=403)


class CreateBody(BaseModel):
    title: str = Field()
    source_type: str = Field(default="free")
    source_id: int | None = Field(default=None)
    user_view: str = Field(default="")
    tuzhan_view: str = Field(default="")
    tuzhan_view_origin: str = Field(default="user")


class ViewBody(BaseModel):
    role: str = Field(pattern="^(user|tuzhan)$")
    content: str = Field(default="")
    origin: str = Field(default="user", pattern="^(llm|user)$")


@router.get("")
async def api_list():
    if not config.dual_perspectives_enabled:
        return _disabled()
    return {"ok": True, "items": await asyncio.to_thread(dual_perspectives.list_perspectives, active_user_id())}


@router.get("/anchors")
async def api_anchor_candidates():
    if not config.dual_perspectives_enabled:
        return _disabled()
    return {"ok": True, **(await asyncio.to_thread(dual_perspectives.anchor_candidates, active_user_id()))}


@router.post("")
async def api_create(body: CreateBody):
    if not config.dual_perspectives_enabled:
        return _disabled()
    try:
        item = await asyncio.to_thread(
            dual_perspectives.create_perspective,
            active_user_id(),
            body.title,
            source_type=body.source_type,
            source_id=body.source_id,
            user_view=body.user_view,
            tuzhan_view=body.tuzhan_view,
            tuzhan_view_origin=body.tuzhan_view_origin,
        )
    except dual_perspectives.DualPerspectiveError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, "item": item}


@router.put("/{perspective_id}/view")
async def api_set_view(perspective_id: int, body: ViewBody):
    if not config.dual_perspectives_enabled:
        return _disabled()
    try:
        item = await asyncio.to_thread(
            dual_perspectives.set_view,
            active_user_id(),
            perspective_id,
            body.role,
            body.content,
            origin=body.origin,
        )
    except dual_perspectives.DualPerspectiveError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, "item": item}


@router.post("/{perspective_id}/tuzhan-draft")
async def api_tuzhan_draft(perspective_id: int):
    if not config.dual_perspectives_enabled:
        return _disabled()
    try:
        return await dual_perspectives.generate_tuzhan_draft(active_user_id(), perspective_id)
    except dual_perspectives.DualPerspectiveError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.delete("/{perspective_id}")
async def api_delete(perspective_id: int):
    if not config.dual_perspectives_enabled:
        return _disabled()
    deleted = await asyncio.to_thread(
        dual_perspectives.delete_perspective, active_user_id(), perspective_id
    )
    if not deleted:
        return JSONResponse({"ok": False, "error": "这一页不存在"}, status_code=404)
    return {"ok": True}
