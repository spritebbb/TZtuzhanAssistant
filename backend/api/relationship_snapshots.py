# -*- coding: utf-8 -*-
"""M8 关系快照 API：30/100/365 天纪念页。GET 只读、POST 显式幂等、DELETE 真删除；
feature flag 关闭时全部 403，未来信件与共同产物不受影响。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..core import relationship_snapshots
from ..core.config import config
from ..core.persona_profiles import active_user_id

router = APIRouter(prefix="/api/relationship-snapshots", tags=["relationship-snapshots"])


class CreateSnapshotBody(BaseModel):
    snapshot_days: int = Field()


def _disabled() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "关系快照未开启"}, status_code=403)


@router.get("")
async def api_snapshot_state():
    if not config.relationship_snapshots_enabled:
        return _disabled()
    return await asyncio.to_thread(
        relationship_snapshots.snapshot_state, active_user_id()
    )


@router.post("")
async def api_create_snapshot(body: CreateSnapshotBody):
    if not config.relationship_snapshots_enabled:
        return _disabled()
    try:
        snapshot = await asyncio.to_thread(
            relationship_snapshots.create_snapshot, active_user_id(), body.snapshot_days
        )
    except relationship_snapshots.RelationshipSnapshotError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, "snapshot": snapshot}


@router.delete("/{snapshot_days}")
async def api_delete_snapshot(snapshot_days: int):
    if not config.relationship_snapshots_enabled:
        return _disabled()
    deleted = await asyncio.to_thread(
        relationship_snapshots.delete_snapshot, active_user_id(), snapshot_days
    )
    if not deleted:
        return JSONResponse({"ok": False, "error": "这一页不存在"}, status_code=404)
    return {"ok": True}
