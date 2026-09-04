# -*- coding: utf-8 -*-
"""C4 好感度玩法闭环：解锁收集页（9 槽位状态）。"""
from __future__ import annotations

from fastapi import APIRouter

from ..core import unlock

router = APIRouter(prefix="/api/unlocks", tags=["unlocks"])

_UID = "assistant-main"


@router.get("")
async def api_unlocks_list():
    return {"ok": True, "slots": unlock.list_slots(_UID)}
