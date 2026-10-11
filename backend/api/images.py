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
_PERSONA_STATES = Path(__file__).resolve().parents[2] / "assets" / "persona_states"
_PERSONA_STATE_NAMES = {"low", "plain", "lazy", "happy", "excited"}

# 全局立绘回退链：按优先级取第一个存在的文件。前两项是「私人图片资产」（被
# .gitignore 排除，只在开发机主检出存在），后两项随仓库分发。P3-5：此前只回退
# _PERSONA_FULL，新克隆 / worktree / CI 上该文件不存在 → /persona/full 与
# /persona/cutout 必 404（发布包用 robocopy 拷磁盘 assets/ 反而带得上，于是
# 「从源码构建」与「发布物」不等价）。补仓库内资产兜底，任何检出都有图。
_GLOBAL_PORTRAIT_FALLBACKS = (
    _PERSONA_FULL,
    _PERSONA_CUTOUT,
    _PERSONA_STATES / "plain.png",
    _PERSONA_IMG,
)


def _global_portrait() -> Path | None:
    """全局立绘：回退链里第一个存在的文件；全部缺失返回 None。"""
    for path in _GLOBAL_PORTRAIT_FALLBACKS:
        if path.exists():
            return path
    return None


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
    path = _global_portrait()
    if path is not None:
        return FileResponse(path, media_type="image/png")
    return JSONResponse({"ok": False, "error": "立绘未找到"}, status_code=404)


@router.get("/persona/full")
async def persona_full():
    """菟菚透明全身立绘（抠图，用于主区角色背景）。

    NP-13：激活人格自带 portraits/plain.png 时优先（立绘随人格包走），
    否则回退全局默认。
    """
    active = _active_portrait("plain")
    if active is not None:
        return FileResponse(active, media_type="image/png")
    path = _global_portrait()
    if path is not None:
        return FileResponse(path, media_type="image/png")
    return JSONResponse({"ok": False, "error": "立绘未找到"}, status_code=404)


def _active_portrait(state: str):
    """激活人格包内的立绘路径；未带立绘/状态非法/读取失败返回 None。"""
    if state not in _PERSONA_STATE_NAMES:
        return None
    try:
        from ..core.persona_profiles import active_card_path

        path = active_card_path().parent / "portraits" / f"{state}.png"
    except Exception:
        return None
    return path if path.is_file() else None


@router.get("/persona/full/{state}")
async def persona_full_state(state: str):
    """按五档心情返回差分立绘；人格包立绘优先，缺档回退包内 plain，再回退全局。"""
    if state not in _PERSONA_STATE_NAMES:
        return JSONResponse({"ok": False, "error": "未知立绘状态"}, status_code=404)
    active = _active_portrait(state)
    if active is None and state != "plain":
        active = _active_portrait("plain")
    if active is not None:
        return FileResponse(active, media_type="image/png")
    path = _PERSONA_STATES / f"{state}.png"
    if path.exists():
        return FileResponse(path, media_type="image/png")
    fallback = _global_portrait()
    if fallback is not None:
        return FileResponse(fallback, media_type="image/png")
    return JSONResponse({"ok": False, "error": "立绘未找到"}, status_code=404)


@router.get("/favicon.ico")
async def favicon():
    """站点图标：直接用菟菚人设图。"""
    if _PERSONA_IMG.exists():
        return FileResponse(_PERSONA_IMG, media_type="image/png")
    return JSONResponse({"ok": False, "error": "人设图未找到"}, status_code=404)
