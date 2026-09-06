# -*- coding: utf-8 -*-
"""M8 梦境/平行可能性 API：生成草稿（不落库）、收藏（唯一持久化点）、删除。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..core import possibilities
from ..core.config import config
from ..core.persona_profiles import active_user_id

router = APIRouter(prefix="/api/possibilities", tags=["possibilities"])


def _disabled() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "梦境与平行可能性未开启"}, status_code=403)


class DraftBody(BaseModel):
    mode: str = Field()
    title: str = Field()
    premise: str = Field()


class CollectBody(BaseModel):
    mode: str = Field()
    title: str = Field()
    content: str = Field()


@router.get("")
async def api_status():
    """轻量探针：前端据此决定是否渲染虚构创作区（flag 关闭时 403，同款降级）。"""
    if not config.possibilities_enabled:
        return _disabled()
    return {"ok": True}


@router.post("/draft")
async def api_draft(body: DraftBody):
    if not config.possibilities_enabled:
        return _disabled()
    try:
        return await possibilities.generate_draft(
            active_user_id(), body.mode, body.title, body.premise
        )
    except possibilities.PossibilityError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/collect")
async def api_collect(body: CollectBody):
    if not config.possibilities_enabled:
        return _disabled()
    try:
        item = await asyncio.to_thread(
            possibilities.collect, active_user_id(), body.mode, body.title, body.content
        )
    except possibilities.PossibilityError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, "item": item}


@router.delete("/{artifact_id}")
async def api_delete(artifact_id: int):
    if not config.possibilities_enabled:
        return _disabled()
    deleted = await asyncio.to_thread(
        possibilities.delete_collected, active_user_id(), artifact_id
    )
    if not deleted:
        return JSONResponse({"ok": False, "error": "这个片段不存在或已删除"}, status_code=404)
    return {"ok": True}
