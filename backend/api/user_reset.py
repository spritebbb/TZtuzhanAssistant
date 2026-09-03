# -*- coding: utf-8 -*-
"""彻底重置接口：用户主动选择「重新开始/失忆」时调用。"""
from __future__ import annotations

from fastapi import APIRouter

from ..core.log import logger
from ..core import reset as reset_core

router = APIRouter(prefix="/api", tags=["reset"])


@router.post("/user/reset")
async def api_user_reset():
    """让菟菚忘记当前用户积累的一切（好感/昵称/记忆/向量/当前会话气泡）。

    返回清理统计。此操作不可撤销（不删除归档），前端调用前应二次确认。
    """
    try:
        stats = await reset_core.reset_everything()
        logger.info("[重置] 用户主动触发彻底重置完成: %s", stats)
        return {"ok": True, "reset": stats}
    except Exception as e:
        logger.exception("[重置] 重置失败")
        return {"ok": False, "error": f"重置失败: {e}"}
