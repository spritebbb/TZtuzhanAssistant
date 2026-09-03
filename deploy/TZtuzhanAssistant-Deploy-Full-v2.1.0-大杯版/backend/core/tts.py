# -*- coding: utf-8 -*-
"""语音朗读：edge-tts（微软 Edge 在线 TTS）合成 mp3，带缓存。

- synth(text, voice) → data/tts_cache/<sha1>.mp3 路径（命中缓存直接返回）
- 走系统代理（http_proxy/https_proxy 环境变量）；无代理则直连
- 失败返回 None（调用方回退为不朗读，不阻断对话）
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

from .config import config
from .log import logger

_TTS_DIR: Path = config.data_dir / "tts_cache"
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"  # 晓晓（女声，适合菟菚）
_CACHE_MAX = 200  # 缓存文件上限（超出由维护任务清理最旧）


def _proxy() -> str | None:
    return os.getenv("https_proxy") or os.getenv("http_proxy")


def _path_for(text: str, voice: str) -> Path:
    digest = hashlib.sha1(f"{voice}:{text}".encode("utf-8")).hexdigest()[:16]
    return _TTS_DIR / f"{digest}.mp3"


async def synth_async(text: str, voice: str = DEFAULT_VOICE) -> Path | None:
    """合成语音，命中缓存直接返回。text 过长时截断（TTS 有长度限制）。"""
    text = (text or "").strip()
    if not text:
        return None
    if len(text) > 500:
        text = text[:500]
    path = _path_for(text, voice)
    if path.exists() and path.stat().st_size > 0:
        return path
    try:
        _TTS_DIR.mkdir(parents=True, exist_ok=True)
        import edge_tts

        kwargs = {}
        p = _proxy()
        if p:
            kwargs["proxy"] = p
        c = edge_tts.Communicate(text, voice=voice, **kwargs)
        await c.save(str(path))
        if path.exists() and path.stat().st_size > 0:
            return path
        logger.warning("[语音] 合成返回空文件")
        return None
    except Exception as e:
        logger.warning(f"[语音] 合成失败: {e}")
        return None


def synth(text: str, voice: str = DEFAULT_VOICE) -> Path | None:
    """同步包装（放到线程池调用）。"""
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(synth_async(text, voice))
        finally:
            loop.close()
    except Exception as e:
        logger.warning(f"[语音] 合成线程失败: {e}")
        return None


def clean_cache(max_count: int = _CACHE_MAX) -> int:
    """缓存超过上限时删除最旧的，返回删除数量。"""
    if not _TTS_DIR.exists():
        return 0
    files = sorted(_TTS_DIR.glob("*.mp3"), key=lambda f: f.stat().st_mtime)
    if len(files) <= max_count:
        return 0
    removed = 0
    for f in files[:-max_count]:
        try:
            f.unlink()
            removed += 1
        except OSError:
            continue
    if removed:
        logger.info(f"[语音] 清理 TTS 缓存 {removed} 个")
    return removed


async def clean_cache_async(max_count: int = _CACHE_MAX) -> int:
    return await asyncio.to_thread(clean_cache, max_count)
