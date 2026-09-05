# -*- coding: utf-8 -*-
"""M6 共同空间 API：我们的角落——只陈列真实 artifact，不凭空生成共同历史。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query

from ..core.activities import list_artifacts
from ..core.persona_profiles import active_user_id

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


@router.get("")
async def api_list_artifacts(limit: int = Query(50, ge=1, le=100)):
    rows = await asyncio.to_thread(list_artifacts, active_user_id(), limit)
    return {"ok": True, "artifacts": rows}
