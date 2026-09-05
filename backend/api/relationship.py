# -*- coding: utf-8 -*-
"""M1/E03 关系导出与恢复 API：带走、预览、恢复到空命名空间。"""
from __future__ import annotations

import asyncio
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ..core import relationship_export
from ..core.persona_profiles import active_user_id
from ..core.relationship_export import BundleError

router = APIRouter(prefix="/api/relationship", tags=["relationship"])


class RestoreBody(BaseModel):
    bundle: dict = Field(min_length=1)
    target_user_id: str = Field(min_length=1, max_length=120, pattern=r"^[\w\-]+$")
    dry_run: bool = False


def _error(exc: BundleError, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=status_code)


@router.get("/export")
async def api_export_relationship(categories: str | None = None):
    """导出当前人格的关系数据（JSON 下载）。categories 为逗号分隔类别。"""
    selected = [item.strip() for item in categories.split(",")] if categories else None
    try:
        bundle = await asyncio.to_thread(
            relationship_export.export_bundle, active_user_id(), selected
        )
    except BundleError as exc:
        return _error(exc)
    filename = quote(f"tuzhan-relationship-{active_user_id()}.json")
    return Response(
        relationship_export.bundle_to_json(bundle),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.post("/restore/preview")
async def api_preview_restore(body: RestoreBody):
    try:
        preview = await asyncio.to_thread(
            relationship_export.preview_restore, body.bundle, body.target_user_id
        )
    except BundleError as exc:
        return _error(exc)
    return {"ok": True, "preview": preview}


@router.post("/restore")
async def api_restore_relationship(body: RestoreBody):
    try:
        result = await asyncio.to_thread(
            relationship_export.restore_bundle,
            body.bundle,
            body.target_user_id,
            dry_run=body.dry_run,
        )
    except BundleError as exc:
        return _error(exc)
    return {"ok": True, **result}
