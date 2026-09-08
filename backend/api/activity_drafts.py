# -*- coding: utf-8 -*-
"""F06 活动草稿确认 API：服务端重校验后调用权威 start 函数。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from ..core import activity_drafts as drafts
from ..core.log import logger
from ..core.persona_profiles import active_user_id

router = APIRouter(prefix="/api/activity-drafts", tags=["activity-drafts"])


@router.post("/confirm")
async def api_confirm_draft(
    draft_id: str = Body(...),
    title: str | None = Body(None),
    payload: dict | None = Body(None),
):
    """确认草稿并创建活动；同草稿二次确认返回同一活动（幂等）。"""
    uid = active_user_id()
    try:
        result = await asyncio.to_thread(
            drafts.confirm_draft, uid, draft_id, title=title, payload=payload)
    except drafts.ActivityDraftError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    logger.info("[活动草稿] API 确认：{}", result.get("activity_id"))
    return result
