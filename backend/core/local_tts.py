# -*- coding: utf-8 -*-
"""P3-03 本地 TTS（GPT-SoVITS）：adapter 能力协商 + 显式回退 + 声纹档案消费。

契约（docs/Zcode技术指导.md §21.2，批次15 口径：不训练，真机/素材留空位）：

- **能力协商先行**：GET {endpoint}/capabilities（runbook 约定的薄包装）或
  版本自检；固定实际部署的 provider_version/model_format/sample_rate/语言，
  不把任何教程参数当稳定合同；协商结果 60s 缓存，失败即回退；
- **推理只消费已确认最终文本**：分句器不切代码块/URL；长文本按句切块合成
  后拼接 PCM；同声纹请求按到达顺序串行（GPU 单飞，logical 顺序保持）；
- **显式回退**：服务不可用/显存不足/版本不符/无启用声纹 → 回退 edge-tts，
  绝不静默失败也不阻断朗读；
- **缓存键含 provider/version/model/voice/韵律/文本**（与 edge-tts 缓存同目录、
  不同扩展名与哈希空间）；
- **声纹档案**：voice_profiles（人格绑定）+ voice_manifests（素材清单，
  素材本体在加密工作区，不进关系导出）——本模块只消费 enabled 档案。
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import re
import wave
from datetime import datetime
from pathlib import Path

from .config import config
from .log import logger

_TTS_DIR: Path = config.data_dir / "tts_cache"
MAX_LOCAL_CHARS = 200          # 单块上限（GPT-SoVITS 长文本质量与显存考虑）
MAX_CHUNKS = 5                 # 超出截断（朗读兜底，不做超长书）
NEGOTIATE_TTL_SEC = 60.0

# ---------------------------------------------------------------------------
# 情绪参考分派（TTS 质量批）：合成音的感情来自参考音频的韵律克隆——恒用
# 中立参考，输出就是「无机质、无起伏」。按 mood 切换情绪参考（约定优于配置：
# 与 manifest 主 ref 同目录的 zh-{emotion}.wav，缺档回退主 ref）；语速档位与
# edge-tts 的 prosody_for_mood 同表换算（+4% rate ↔ speed_factor 1.04）。
# prompt 文本源自原情绪音频文件名（参考音频/遐蝶/emotions/，迁移时内嵌）。
# ---------------------------------------------------------------------------
_EMO_PROMPTS: dict[str, str] = {
    # 2026-09-30 换官方游戏语音长参考（StarRail4.2_CN 数据集，.lab 官方文本，
    # RUBY 注音标记已清洗）——原 aihobbyist 4 秒短参考韵律信息不足（无机质根因之一）
    "happy": "主人离世后，私人物品本该被清理。但缇安大人知道我喜欢读书，就送给了我。",
    "sad": "对不起…我无法令你死而复生。但你若有所求，我可以结束你漫长的困顿。",
    "neutral": "恐怕只有他自己知晓答案…不，或许连他自己都摇摆不定。",
}
# 与 tts._PROSODY_TIERS 对齐：≥65 开心（+4%）、≥35 平淡（-3%）、<35 低落（-8%）
_MOOD_TIERS: tuple[tuple[int, str, float], ...] = (
    (65, "happy", 1.04),
    (35, "neutral", 0.97),
    (0, "sad", 0.92),
)
_CHUNK_GAP_SEC = 0.18  # 块间自然停顿（句号级切分后硬拼会「赶」，听感断句异常）


def _mood_emotion_speed(mood: int | None) -> tuple[str, float]:
    """mood → (情绪参考名, speed_factor)。None/越界回退中性。"""
    try:
        value = int(mood)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "neutral", 1.0
    if value < 0 or value > 100:
        return "neutral", 1.0
    for floor, emotion, speed in _MOOD_TIERS:
        if value >= floor:
            return emotion, speed
    return "neutral", 1.0


def _emotion_ref(profile: dict, emotion: str) -> tuple[str, str]:
    """情绪参考 (ref_path, prompt_text)：约定路径存在才启用，否则回退主 ref。

    三档统一走约定路径 + _EMO_PROMPTS 配套文本（两者同批部署、天然同步）——
    包括 neutral：manifest 登记的主 ref 文件与 ref_text 是旧的短参考，若
    沿用会出现「新音频配旧文本」的对齐错乱。manifest ref 仅为约定文件
    缺失时的兜底。
    """
    ref = Path(profile["ref_path"])
    candidate = ref.parent / f"zh-{emotion}.wav"
    # ref 可能是相对推理服务的路径（如 reference_audios/castorice/…）；
    # 本地部署契约下 GPT-SoVITS 在仓库根（runbook §部署位置），据此做存在性判断
    local = candidate if candidate.is_absolute() else Path("GPT-SoVITS") / candidate
    if local.is_file() and _EMO_PROMPTS.get(emotion) and _ref_duration_ok(local):
        return candidate.as_posix(), _EMO_PROMPTS[emotion]
    return str(profile["ref_path"]), profile["ref_text"] or ""


def _ref_duration_ok(path: Path, *, lo: float = 3.0, hi: float = 10.0) -> bool:
    """api_v2 硬性要求参考音频 3~10 秒（超界 400 → 整档静默降级 edge-tts）。
    部署前置校验：界外视为缺档，回退主 ref。读失败按不过（保守）。"""
    try:
        with wave.open(str(path), "rb") as w:
            return lo <= w.getnframes() / w.getframerate() <= hi
    except Exception:
        return False

_negotiate_cache: tuple[float, dict | None] = (0.0, None)
_gpu_lock: asyncio.Semaphore | None = None

# 测试注入点：置空走真实 httpx；置为工厂则 negotiate/_synth_chunk_http 都用它
# 构造客户端（fake 推理服务用 httpx.MockTransport，见 tests/test_local_tts.py）
_client_factory = None


def _make_client(**kwargs):
    import httpx

    if _client_factory is not None:
        return _client_factory(**kwargs)
    return httpx.AsyncClient(**kwargs)


def _sem() -> asyncio.Semaphore:
    global _gpu_lock
    if _gpu_lock is None:
        _gpu_lock = asyncio.Semaphore(1)
    return _gpu_lock


# ---------------------------------------------------------------------------
# 能力协商
# ---------------------------------------------------------------------------

async def negotiate(*, force: bool = False) -> dict | None:
    """GET {endpoint}/capabilities；失败/不符 → None（回退）。60s 缓存。"""
    import time

    now = time.time()
    if not force and _negotiate_cache[1] is not None and now - _negotiate_cache[0] < NEGOTIATE_TTL_SEC:
        return _negotiate_cache[1]
    if not (config.local_tts_endpoint or "").strip():
        _set_negotiate(None, now)
        return None
    try:
        async with _make_client(timeout=config.local_tts_timeout) as client:
            resp = await client.get(f"{config.local_tts_endpoint.rstrip('/')}/capabilities")
        if resp.status_code != 200:
            raise RuntimeError(f"capabilities HTTP {resp.status_code}")
        caps = resp.json()
        version = str(caps.get("provider_version") or "").strip()
        if not version:
            raise RuntimeError("capabilities 缺 provider_version")
        result = {
            "provider": "gpt_sovits",
            "provider_version": version,
            "model_format": str(caps.get("model_format") or ""),
            "sample_rate": int(caps.get("sample_rate") or 0),
            "languages": [str(x) for x in (caps.get("languages") or [])],
            "gpu": bool(caps.get("gpu")),
            "cpu_ok": bool(caps.get("cpu_inference_ok")),
            "endpoints": {"tts": "/tts"},
        }
        _set_negotiate(result, now)
        return result
    except Exception as exc:
        logger.info("[本地语音] 能力协商失败，回退 edge-tts：{}", type(exc).__name__)
        _set_negotiate(None, now)
        return None


def _set_negotiate(value: dict | None, now: float) -> None:
    global _negotiate_cache
    _negotiate_cache = (now, value)


def reset_negotiate_for_testing() -> None:
    global _negotiate_cache
    _negotiate_cache = (0.0, None)


# ---------------------------------------------------------------------------
# 声纹档案（voice_profiles / voice_manifests，schema v45）
# ---------------------------------------------------------------------------

def enabled_voice_profile(persona_id: str) -> dict | None:
    """当前人格启用的本地声纹（无则回退）。"""
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT p.*, m.path AS ref_path, m.text AS ref_text, m.lang AS ref_lang, "
            "m.sha256 AS ref_sha256 FROM voice_profiles p "
            "LEFT JOIN voice_manifests m ON m.id = p.reference_manifest_id "
            "WHERE p.persona_id=? AND p.enabled=1 ORDER BY p.id DESC LIMIT 1",
            (persona_id,),
        ).fetchone()
    if row is None or not (row["ref_path"] or "").strip():
        return None
    return dict(row)


def set_voice_profile(
    persona_id: str, *, provider_version: str, model_hash: str,
    reference_manifest_id: int, enabled: bool = True,
) -> dict:
    """登记/切换人格声纹（同人格旧档案自动停用）。"""
    from .userdb import db

    now = datetime.now().isoformat(timespec="seconds")
    with db._lock:
        db.conn.execute(
            "UPDATE voice_profiles SET enabled=0 WHERE persona_id=?", (persona_id,)
        )
        cur = db.conn.execute(
            "INSERT INTO voice_profiles (persona_id, provider_version, model_hash, "
            "reference_manifest_id, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (persona_id, provider_version, model_hash, int(reference_manifest_id),
             1 if enabled else 0, now),
        )
        db.conn.commit()
        return {"profile_id": int(cur.lastrowid), "persona_id": persona_id,
                "enabled": bool(enabled)}


def add_voice_manifest(
    *, path: str, text: str, lang: str = "zh", sha256: str = "",
    duration_sec: float = 0.0,
) -> dict:
    """登记素材清单条目（本体在加密工作区；不进关系导出）。"""
    from .userdb import db

    now = datetime.now().isoformat(timespec="seconds")
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO voice_manifests (path, text, lang, sha256, duration_sec, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (path, text[:500], lang, sha256, float(duration_sec), now),
        )
        db.conn.commit()
        return {"manifest_id": int(cur.lastrowid)}


# ---------------------------------------------------------------------------
# 分句（不切代码块/URL）
# ---------------------------------------------------------------------------

_CODE_RE = re.compile(r"```.*?```|`[^`\n]+`", re.S)
_URL_RE = re.compile(r"https?://\S+")


def split_sentences(text: str, *, max_chars: int = MAX_LOCAL_CHARS) -> list[str]:
    """按句切分用于本地合成的块：代码块与 URL 视为原子，绝不切断。"""
    text = (text or "").strip()
    if not text:
        return []
    protected: list[str] = []

    def _stash(m: re.Match) -> str:
        protected.append(m.group(0))
        return f"\x00{len(protected) - 1}\x00"

    masked = _CODE_RE.sub(_stash, text)
    masked = _URL_RE.sub(_stash, masked)
    parts = re.split(r"(?<=[。！？!?；;\n])", masked)
    chunks: list[str] = []
    current = ""
    for part in parts:
        if not part:
            continue
        # 先还原占位符再量长度（保护段可能很长，还原后单块超限则整体一块）
        candidate = current + part
        restored_len = len(_restore(candidate, protected))
        if current and restored_len > max_chars:
            chunks.append(_restore(current, protected))
            current = part
        else:
            current = candidate
    if current:
        chunks.append(_restore(current, protected))
    return [c.strip() for c in chunks if c.strip()][:MAX_CHUNKS]


def _restore(text: str, protected: list[str]) -> str:
    return re.sub(r"\x00(\d+)\x00", lambda m: protected[int(m.group(1))], text)


# ---------------------------------------------------------------------------
# 合成（GPT-SoVITS api_v2 风格请求）与拼接
# ---------------------------------------------------------------------------

def _cache_path(*, provider_version: str, model_hash: str, voice: str,
                prosody: str, text: str) -> Path:
    digest = hashlib.sha1(
        f"gptsovits:{provider_version}:{model_hash}:{voice}:{prosody}:{text}".encode("utf-8")
    ).hexdigest()[:16]
    return _TTS_DIR / f"{digest}.wav"


async def _synth_chunk_http(chunk: str, profile: dict, caps: dict, *,
                            emotion: str = "neutral",
                            speed_factor: float = 1.0) -> bytes:
    """单块合成：POST /tts（api_v2 风格；参数以协商到的能力为准）。

    情绪参考与语速随 mood 分派（TTS 质量批）：感情起伏由参考音频韵律克隆，
    语速对齐 edge-tts 的 prosody_for_mood 档位。
    """
    ref_path, prompt_text = _emotion_ref(profile, emotion)
    payload = {
        "text": chunk,
        "text_lang": "zh",
        "ref_audio_path": ref_path,
        "prompt_text": prompt_text,
        "prompt_lang": profile["ref_lang"] or "zh",
        "model_hash": profile["model_hash"],
        "provider_version": caps["provider_version"],
        "speed_factor": round(speed_factor, 3),
    }
    async with _make_client(timeout=config.local_tts_timeout) as client:
        resp = await client.post(
            f"{config.local_tts_endpoint.rstrip('/')}/tts", json=payload
        )
    resp.raise_for_status()
    return resp.content


def _concat_wav(frames: list[bytes], *, gap_sec: float = _CHUNK_GAP_SEC) -> bytes:
    """拼接同格式 WAV，块间插入短静音模拟句间自然停顿（GPT-SoVITS 单次部署
    输出格式一致；不一致直接抛→回退）。"""
    outs = io.BytesIO()
    params: wave._WaveParams | None = None
    with wave.open(outs, "wb") as writer:
        for idx, raw in enumerate(frames):
            with wave.open(io.BytesIO(raw), "rb") as reader:
                p = reader.getparams()
                if params is None:
                    params = p
                    writer.setparams(p)
                elif (p.nchannels, p.sampwidth, p.framerate) != (
                    params.nchannels, params.sampwidth, params.framerate
                ):
                    raise ValueError("WAV 格式不一致，拒绝拼接")
                writer.writeframes(reader.readframes(reader.getnframes()))
            if idx < len(frames) - 1 and gap_sec > 0:
                silence = int(params.framerate * gap_sec)  # 帧 = 采样数（单声道）
                writer.writeframes(b"\x00" * silence * params.sampwidth * params.nchannels)
    return outs.getvalue()


async def synth_local(text: str, *, persona_id: str, mood: int | None = None) -> tuple[Path, str] | None:
    """本地 GPT-SoVITS 合成入口。返回 (wav 路径, provider) 或 None（回退信号）。

    任何失败（协商/档案/网络/拼接）都返回 None，由调用方显式回退 edge-tts。
    """
    profile = enabled_voice_profile(persona_id)
    if profile is None:
        return None
    caps = await negotiate()
    if caps is None:
        return None
    chunks = split_sentences(text)
    if not chunks:
        return None
    prosody = str(mood if mood is not None else "")
    cache_path = _cache_path(
        provider_version=caps["provider_version"], model_hash=profile["model_hash"],
        voice=str(profile["id"]), prosody=prosody, text=text[:500],
    )
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return cache_path, "gpt_sovits"
    try:
        _TTS_DIR.mkdir(parents=True, exist_ok=True)
        emotion, speed = _mood_emotion_speed(mood)
        # GPU 单飞：按到达顺序串行，保持消息级顺序（logical_message_id 顺序）
        async with _sem():
            frames = [
                await _synth_chunk_http(c, profile, caps,
                                        emotion=emotion, speed_factor=speed)
                for c in chunks
            ]
        data = _concat_wav(frames) if len(frames) > 1 else frames[0]
        cache_path.write_bytes(data)
        return cache_path, "gpt_sovits"
    except Exception as exc:
        logger.info("[本地语音] 合成失败，回退 edge-tts：{}: {}",
                    type(exc).__name__, str(exc)[:120])
        try:
            cache_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None


__all__ = [
    "MAX_LOCAL_CHARS",
    "add_voice_manifest",
    "enabled_voice_profile",
    "negotiate",
    "reset_negotiate_for_testing",
    "set_voice_profile",
    "split_sentences",
    "synth_local",
]
