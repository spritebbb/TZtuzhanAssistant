# -*- coding: utf-8 -*-
"""C4 好感度玩法闭环：解锁收集页（9 槽位状态）。"""
from __future__ import annotations

from fastapi import APIRouter

from ..core import unlock
from ..core.persona_profiles import active_user_id

router = APIRouter(prefix="/api/unlocks", tags=["unlocks"])

@router.get("")
async def api_unlocks_list():
    return {"ok": True, "slots": unlock.list_slots(active_user_id())}
