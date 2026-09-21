# -*- coding: utf-8 -*-
"""D11 离线补算：取待回放内容与回放确认（逐条、可跳过）。"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..core import offline_recap
from ..core.features import flag

router = APIRouter(prefix="/api/offline-recap", tags=["offline-recap"])


@router.get("")
async def api_offline_recap():
    if not flag("offline_recap_enabled"):
        return JSONResponse({"ok": False, "error": "离线补算未开启"}, status_code=403)
    from ..core.persona_profiles import active_user_id

    try:
        await offline_recap.maybe_generate(active_user_id())  # 懒生成兜底（启动预生成失败时）
        return offline_recap.pending_view(active_user_id())
    except Exception:
        return JSONResponse({"ok": False, "error": "离线补算读取失败"}, status_code=500)


class AckBody(BaseModel):
    action: str = Field(pattern="^(delivered|skip)$")


@router.post("/ack")
async def api_offline_recap_ack(body: AckBody):
    if not flag("offline_recap_enabled"):
        return JSONResponse({"ok": False, "error": "离线补算未开启"}, status_code=403)
    from ..core.persona_profiles import active_user_id

    return offline_recap.ack(active_user_id(), body.action)
