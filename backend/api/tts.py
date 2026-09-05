# -*- coding: utf-8 -*-
"""语音朗读接口（edge-tts）。"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from ..core.tts import MAX_TEXT_CHARS, synth_async

router = APIRouter(prefix="/api", tags=["tts"])


@router.get("/tts")
async def api_tts(text: str = "", voice: str = ""):
    """语音朗读：edge-tts 合成 mp3（带缓存）。text 为空或合成失败返回 400。"""
    if not voice:
        from ..core.persona_profiles import active_voice

        voice = active_voice()
    if not text.strip():
        return JSONResponse({"ok": False, "error": "缺少 text"}, status_code=400)
    if len(text) > MAX_TEXT_CHARS or len(voice) > 100:
        return JSONResponse({"ok": False, "error": "文本或音色参数过长"}, status_code=413)
    path = await synth_async(text, voice)
    if path is None or not path.exists():
        return JSONResponse({"ok": False, "error": "语音合成失败"}, status_code=502)
    return FileResponse(path, media_type="audio/mpeg")
