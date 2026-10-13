# -*- coding: utf-8 -*-
"""语音朗读接口（edge-tts）。"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from ..core.tts import MAX_TEXT_CHARS, synth_async
from ..core.userdb import db

router = APIRouter(prefix="/api", tags=["tts"])


@router.get("/tts")
async def api_tts(text: str = "", voice: str = "", mood: int | None = None):
    """语音朗读。mood 缺省读当前人格心情（情绪声线）。

    响应头 X-TTS-Provider 标明实际使用方。
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
            from ..core.persona_profiles import active_user_id

            mood = db.get_mood(active_user_id())[0]
        except Exception:
            mood = None

    path = await synth_async(text, voice, mood)
    if path is None or not path.exists():
        return JSONResponse({"ok": False, "error": "语音合成失败"}, status_code=502)
    return FileResponse(path, media_type="audio/mpeg",
                        headers={"X-TTS-Provider": "edge-tts"})
