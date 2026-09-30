# -*- coding: utf-8 -*-
"""L10 桌宠辅助端点：前台全屏探测（本机 Electron 轮询用，loopback 免 token）。"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..core.config import config
from ..core.desktop_probe import probe_foreground

router = APIRouter(prefix="/api/desktop", tags=["desktop"])


@router.get("/fullscreen")
async def api_fullscreen():
    # 探测本身永不满屏抛错——helper 失效返回 fullscreen=False（上层按未全屏继续显示）
    return JSONResponse(probe_foreground())


@router.get("/foreground")
async def api_desktop_foreground():
    """NP-14 桌面感知：前台应用类别（仅 DESKTOP_AWARENESS=1 时探测）。

    关闭时只返回 enabled=false，不调用探测——隐私默认：她默认「看不见」。
    只含应用类别、空闲秒数与脱敏语境标签（#24：敏感域/未知语境为空串），
    不含窗口标题原文/内容。
    """
    if not config.desktop_awareness:
        return {"enabled": False}
    data = probe_foreground()
    return {
        "enabled": True,
        "category": data.get("category", "other"),
        "title_context": data.get("title_context", ""),
        "idle_seconds": data.get("idle_seconds", 0.0),
        "fullscreen": bool(data.get("fullscreen")),
    }
