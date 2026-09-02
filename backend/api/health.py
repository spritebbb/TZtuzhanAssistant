# -*- coding: utf-8 -*-
"""健康检查接口。"""
from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..maintenance.loop import health

router = APIRouter(prefix="/api", tags=["health"])


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

    asyncio.create_task(_bye())
    return JSONResponse({"ok": True, "note": "shutting down after checkpoint"})
