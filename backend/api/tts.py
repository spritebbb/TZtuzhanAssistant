# -*- coding: utf-8 -*-
"""语音朗读接口：本地 GPT-SoVITS 优先（P3-03），显式回退 edge-tts。"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from ..core.tts import MAX_TEXT_CHARS, synth_async
from ..core.userdb import db

router = APIRouter(prefix="/api", tags=["tts"])


@router.get("/tts")
async def api_tts(text: str = "", voice: str = "", mood: int | None = None):
    """语音朗读。mood 缺省读当前人格心情（情绪声线）。

    P3-03：local_tts_enabled 开且当前人格有启用声纹、能力协商成功 → GPT-SoVITS
    （wav）；任何一步不满足 → 显式回退 edge-tts（mp3）。响应头 X-TTS-Provider
    标明实际使用方。
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

    # 1) 本地 GPT-SoVITS（人格声纹 + 能力协商 + GPU 单飞都在模块内把关）
    from ..core.features import flag

    if flag("local_tts_enabled"):
        try:
            from ..core.local_tts import synth_local
            from ..core.persona_profiles import active_id

            local = await synth_local(text, persona_id=active_id(), mood=mood)
        except Exception:
            local = None
        if local is not None:
            path, provider = local
            return FileResponse(path, media_type="audio/wav",
                                headers={"X-TTS-Provider": provider})

    # 2) 显式回退：edge-tts
    path = await synth_async(text, voice, mood)
    if path is None or not path.exists():
        return JSONResponse({"ok": False, "error": "语音合成失败"}, status_code=502)
    return FileResponse(path, media_type="audio/mpeg",
                        headers={"X-TTS-Provider": "edge-tts"})


@router.get("/tts/provider")
async def api_tts_provider():
    """诊断：本地服务协商结果与当前人格声纹（无密钥无正文）。"""
    from ..core.local_tts import enabled_voice_profile, negotiate
    from ..core.persona_profiles import active_id

    caps = await negotiate()
    profile = enabled_voice_profile(active_id())
    return {
        "ok": True,
        "local": {
            "flag_on": True,
            "negotiated": caps is not None,
            "provider_version": (caps or {}).get("provider_version", ""),
            "gpu": (caps or {}).get("gpu", False),
            "endpoint": (caps or {}).get("endpoints", {}).get("tts", ""),
        },
        "voice_profile": {
            "profile_id": profile["id"] if profile else None,
            "model_hash": (profile or {}).get("model_hash", ""),
            "ref_lang": (profile or {}).get("ref_lang", ""),
        } if profile else None,
        "fallback": "edge-tts",
    }
