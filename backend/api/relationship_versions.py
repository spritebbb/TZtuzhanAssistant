# -*- coding: utf-8 -*-
"""M8.7「不同版本的我们」API：显式创建检查点、列表、确定性比较、删除。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..core import relationship_versions
from ..core.config import config
from ..core.persona_profiles import active_user_id

router = APIRouter(prefix="/api/relationship-versions", tags=["relationship-versions"])


def _disabled() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "不同版本的我们未开启"}, status_code=403)


class CaptureBody(BaseModel):
    label: str = Field()


@router.get("")
async def api_list(limit: int = Query(50, ge=1, le=100)):
    if not config.relationship_versions_enabled:
        return _disabled()
    try:
        versions = await asyncio.to_thread(
            relationship_versions.list_versions, active_user_id(), limit
        )
    except relationship_versions.RelationshipVersionError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, "versions": versions}


@router.post("")
async def api_capture(body: CaptureBody):
    if not config.relationship_versions_enabled:
        return _disabled()
    try:
        version = await asyncio.to_thread(
            relationship_versions.capture_version, active_user_id(), body.label
        )
    except relationship_versions.RelationshipVersionError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, "version": version}


@router.get("/compare")
async def api_compare(
    before_id: int = Query(..., ge=1),
    after_id: int = Query(..., ge=1),
):
    if not config.relationship_versions_enabled:
        return _disabled()
    try:
        return await asyncio.to_thread(
            relationship_versions.compare_versions, active_user_id(), before_id, after_id
        )
    except relationship_versions.RelationshipVersionError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.delete("/{version_id}")
async def api_delete(version_id: int):
    if not config.relationship_versions_enabled:
        return _disabled()
    deleted = await asyncio.to_thread(
        relationship_versions.delete_version, active_user_id(), version_id
    )
    if not deleted:
        return JSONResponse({"ok": False, "error": "这个版本不存在或已删除"}, status_code=404)
    return {"ok": True}
