# -*- coding: utf-8 -*-
"""C5 养成仪表盘只读接口。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query

from ..core.dashboard import dashboard_summary
from ..core.persona_profiles import active_user_id

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

@router.get("")
async def api_dashboard(days: int = Query(30, ge=7, le=90)):
    summary = await asyncio.to_thread(dashboard_summary, active_user_id(), days)
    return {"ok": True, "dashboard": summary}
