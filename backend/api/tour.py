# -*- coding: utf-8 -*-
"""能力演示 API：给前端一份可点击执行的 Agent 演示脚本。"""
from __future__ import annotations

from fastapi import APIRouter

from ..core.demo_tour import get_tour

router = APIRouter(prefix="/api", tags=["tour"])


@router.get("/tour")
async def api_tour():
    """内置能力演示脚本（每步含建议原话、涉及工具、验收点）。"""
    return {"ok": True, **get_tour()}
