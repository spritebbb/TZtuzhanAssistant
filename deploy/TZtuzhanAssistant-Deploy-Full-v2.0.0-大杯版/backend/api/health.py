# -*- coding: utf-8 -*-
"""健康检查接口。"""
from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..maintenance.loop import health

router = APIRouter(prefix="/api", tags=["health"])

# 持有退出任务强引用，避免 pending task 被 GC 静默取消（与 pipeline._memory_tasks
# / agent._agent_bg_tasks 同一修法），否则 checkpoint/备份可能在完成前被回收。
_shutdown_tasks: set[asyncio.Task] = set()


@router.get("/health")
async def api_health() -> JSONResponse:
    return JSONResponse(health())


@router.post("/health/shutdown")
async def api_shutdown() -> JSONResponse:
    """优雅关闭：checkpoint + 备份后退出。

    由 Electron 退出流程调用（Origin/Host 守卫已拦截跨站写请求）；
    备用强杀路径由调用方兜底，无需等待本清理完成。
    """
    from ..maintenance.loop import backup, checkpoint_all

    async def _bye() -> None:
        await asyncio.sleep(0.3)  # 让响应先送达
        try:
            await asyncio.to_thread(checkpoint_all)
            await asyncio.to_thread(backup)
        except Exception:
            pass
        os._exit(0)

    task = asyncio.create_task(_bye())
    _shutdown_tasks.add(task)
    task.add_done_callback(_shutdown_tasks.discard)
    return JSONResponse({"ok": True, "note": "shutting down after checkpoint"})
