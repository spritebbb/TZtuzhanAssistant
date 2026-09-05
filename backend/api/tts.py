# -*- coding: utf-8 -*-
"""语音朗读接口（edge-tts）。"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from ..core.tts import MAX_TEXT_CHARS, synth_async

router = APIRouter(prefix="/api", tags=["tts"])


@router.get("/tts")
async def api_tts(text: str = "", voice: str = "", mood: int | None = None):
    """语音朗读：edge-tts 合成 mp3（带缓存）。text 为空或合成失败返回 400。

    mood 缺省时自动读取当前人格此刻的心情（M6 情绪声线：语速/音高随心情微调）。
    """
    if not voice:
        from ..core.persona_profiles import active_voice

        voice = active_voice()
    if not text.strip():
        return JSONResponse({"ok": False, "error": "缺少 text"}, status_code=400)
    if len(text) > MAX_TEXT_CHARS or len(voice) > 100:
        return JSONResponse({"ok": False, "error": "文本或音色参数过长"}, status_code=413)
    if mood is None:
        try:
            from ..core.userdb import db
            from ..core.persona_profiles import active_user_id

            mood = db.get_mood(active_user_id())[0]
        except Exception:
            mood = None
    path = await synth_async(text, voice, mood)
    if path is None or not path.exists():
        return JSONResponse({"ok": False, "error": "语音合成失败"}, status_code=502)
    return FileResponse(path, media_type="audio/mpeg")
