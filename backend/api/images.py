# -*- coding: utf-8 -*-
"""图片服务接口（生图结果文件、人设图、favicon）。"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from ..core.config import config

_DATA_DIR = config.data_dir
_PERSONA_IMG = Path(__file__).resolve().parents[2] / "assets" / "persona.png"
_PERSONA_CUTOUT = Path(__file__).resolve().parents[2] / "assets" / "persona_cutout.png"
_PERSONA_FULL = Path(__file__).resolve().parents[2] / "assets" / "hotaru_v1_nohalo.png"

router = APIRouter(tags=["images"])


@router.get("/api/images/screenshots/{filename}")
async def api_screenshot(filename: str):
    """截图文件服务（data/screenshots/ 下的图片，供前端/LLM 访问）。"""
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.(png|jpg|jpeg|gif|webp)", filename):
        return JSONResponse({"ok": False, "error": "非法文件名"}, status_code=400)
    p = _DATA_DIR / "screenshots" / filename
    if not p.exists():
        return JSONResponse({"ok": False, "error": "图片不存在"}, status_code=404)
    return FileResponse(p)


@router.get("/api/images/{filename}")
async def api_image(filename: str):
    """生图结果文件服务（data/imgs/ 下的图片）。"""
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.(png|jpg|jpeg|gif|webp)", filename):
        return JSONResponse({"ok": False, "error": "非法文件名"}, status_code=400)
    p = _DATA_DIR / "imgs" / filename
    if not p.exists():
        return JSONResponse({"ok": False, "error": "图片不存在"}, status_code=404)
    return FileResponse(p)


@router.get("/persona")
async def persona_image():
    """菟菚人设图。"""
    if _PERSONA_IMG.exists():
        return FileResponse(_PERSONA_IMG, media_type="image/png")
    return JSONResponse({"ok": False, "error": "人设图未找到"}, status_code=404)


@router.get("/persona/cutout")
async def persona_cutout():
    """菟菚透明半身立绘（抠图，用于头像/侧栏）。"""
    if _PERSONA_CUTOUT.exists():
        return FileResponse(_PERSONA_CUTOUT, media_type="image/png")
    return JSONResponse({"ok": False, "error": "立绘未找到"}, status_code=404)


@router.get("/persona/full")
async def persona_full():
    """菟菚透明全身立绘（抠图，用于主区角色背景）。"""
    if _PERSONA_FULL.exists():
        return FileResponse(_PERSONA_FULL, media_type="image/png")
    return JSONResponse({"ok": False, "error": "立绘未找到"}, status_code=404)


@router.get("/favicon.ico")
async def favicon():
    """站点图标：直接用菟菚人设图。"""
    if _PERSONA_IMG.exists():
        return FileResponse(_PERSONA_IMG, media_type="image/png")
    return JSONResponse({"ok": False, "error": "人设图未找到"}, status_code=404)
