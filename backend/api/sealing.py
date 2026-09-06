# -*- coding: utf-8 -*-
"""M8 阶段封存与告别 API：选定范围导出纪念包 + 告别信。只导出、不删除。"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ..core import sealing
from ..core.persona_profiles import active_user_id
from ..core.relationship_export import BundleError

router = APIRouter(prefix="/api/sealing", tags=["sealing"])


class SealBody(BaseModel):
    categories: list[str] = Field(default_factory=list)
    letter: bool = True


@router.post("")
async def api_seal(body: SealBody):
    try:
        bundle = await sealing.seal(
            active_user_id(), body.categories or None, letter=body.letter
        )
    except BundleError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    summary = sealing.sealing_summary(bundle)
    filename = summary.get("filename_suggestion") or "sealing.json"
    content = json.dumps(bundle, ensure_ascii=False, indent=1)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/preview")
async def api_seal_preview(body: SealBody):
    """不生成告别信、不触发模型：只回传范围与计数，供封存前确认。"""
    from ..core.relationship_export import export_bundle

    selected = list(body.categories) if body.categories else None
    try:
        bundle = await asyncio.to_thread(export_bundle, active_user_id(), selected)
    except BundleError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {
        "ok": True,
        "categories": bundle["categories"],
        "counts": bundle["counts"],
        "exported_at": bundle["exported_at"],
    }
