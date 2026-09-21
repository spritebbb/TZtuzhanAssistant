# -*- coding: utf-8 -*-
"""D9 局势档案：诊断端点（档案本体 + 注入预览）。"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..core import situation
from ..core.features import flag

router = APIRouter(prefix="/api/situation", tags=["situation"])


@router.get("")
async def api_situation():
    if not flag("situation_enabled"):
        return JSONResponse({"ok": False, "error": "局势档案未开启"}, status_code=403)
    from ..core.persona_profiles import active_user_id

    try:
        return situation.debug_view(active_user_id())
    except Exception:
        return JSONResponse({"ok": False, "error": "档案读取失败"}, status_code=500)
