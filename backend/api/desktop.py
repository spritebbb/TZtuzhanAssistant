# -*- coding: utf-8 -*-
"""L10 桌宠辅助端点：前台全屏探测（本机 Electron 轮询用，loopback 免 token）。"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..core.desktop_probe import probe_foreground

router = APIRouter(prefix="/api/desktop", tags=["desktop"])


@router.get("/fullscreen")
async def api_fullscreen():
    # 探测本身永不满屏抛错——helper 失效返回 fullscreen=False（上层按未全屏继续显示）
    return JSONResponse(probe_foreground())
