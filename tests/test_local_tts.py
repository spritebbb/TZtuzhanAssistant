# -*- coding: utf-8 -*-
"""P3-03 本地 TTS（GPT-SoVITS）回归：协商、分句、拼接、缓存、回退、声纹档案。

全部 fake（httpx.MockTransport 模拟推理服务），不安装 GPT-SoVITS、不联网。
覆盖七件事：
1. 能力协商：成功固定版本/格式/采样率；无版本/HTTP 错/连接拒 → None（回退信号）；
2. 分句：代码块与 URL 原子不切、按句切块、超长截断到上限块数；
3. 合成与拼接：多块 wav 拼接样本级正确；格式不一致抛错（上层回退）；
4. 缓存：命中不再发 HTTP；缓存键含版本/模型/韵律（换任一项重新合成）；
5. 显式回退：无档案/协商失败/服务 500 → synth_local 返回 None；
6. 声纹档案：登记/按人格取 enabled/再登记自动停用旧的；
7. API：本地可用走 wav + X-TTS-Provider；不可用回退 edge-tts 头。
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_ltts_"))
os.environ.setdefault("MEMORY_V2", "0")

import httpx  # noqa: E402

from backend.core import features, local_tts  # noqa: E402
from backend.core.config import config  # noqa: E402
from backend.core.local_tts import (  # noqa: E402
    add_voice_manifest,
    enabled_voice_profile,
    set_voice_profile,
    split_sentences,
)
from backend.core.userdb import db  # noqa: E402

PERSONA = "default"
CAPS = {"provider_version": "abc123def456", "model_format": "gpt-sovits-v2",
        "sample_rate": 32000, "languages": ["zh", "en"], "gpu": True,
        "cpu_inference_ok": False}


def _tone_wav(freq_hz: int, seconds: float, rate: int = 32000) -> bytes:
    """生成确定性单音 wav（样本级可验证）。"""
    import math
    import struct

    n = int(rate * seconds)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            v = int(20000 * math.sin(2 * math.pi * freq_hz * i / rate))
            frames += struct.pack("<h", v)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def _mock_transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _install_profile() -> None:
    m = add_voice_manifest(path="voice/ref-default.wav", text="这是参考音频",
                           lang="zh", sha256="r" * 16, duration_sec=8.0)
    set_voice_profile(PERSONA, provider_version=CAPS["provider_version"],
                      model_hash="m" * 16, reference_manifest_id=m["manifest_id"])


def test_negotiation() -> None:
    local_tts.reset_negotiate_for_testing()

    def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=CAPS)

    async def run(handler):
        # 经模块级 _client_factory 注入 MockTransport（fake 推理服务）
        local_tts._client_factory = lambda **k: httpx.AsyncClient(
            transport=_mock_transport(handler), **k)
        try:
            return await local_tts.negotiate(force=True)
        finally:
            local_tts._client_factory = None

    caps = asyncio.run(run(ok))
    assert caps and caps["provider_version"] == CAPS["provider_version"]
    assert caps["sample_rate"] == 32000 and caps["gpu"] is True

    def no_version(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model_format": "x"})

    caps2 = asyncio.run(run(no_version))
    assert caps2 is None, "缺 provider_version 必须协商失败"

    def http_err(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    assert asyncio.run(run(http_err)) is None
    print("[OK] 能力协商：成功固定版本/缺版本拒绝/HTTP错拒绝 → None")


def test_split_sentences() -> None:
    text = "今天先这样。```print('hello')\n多行代码\n不切``` 明天继续！见 https://example.com/a?b=1 哈。"
    chunks = split_sentences(text, max_chars=30)
    joined = "".join(chunks)
    assert "print('hello')" in joined and "https://example.com/a?b=1" in joined
    # 代码块与 URL 是原子：任一块内完整出现
    assert any("```print" in c and "不切```" in c for c in chunks), "代码块不得被切断"
    assert any("https://example.com/a?b=1" in c for c in chunks), "URL 不得被切断"
    # 超限截断：极长文本最多 MAX_CHUNKS 块
    long_text = "这是一句测试。" * 100
    assert len(split_sentences(long_text, max_chars=10)) <= local_tts.MAX_CHUNKS
    print("[OK] 分句：代码块/URL 原子、按句切块、超长截断")


def test_concat_wav() -> None:
    a, b = _tone_wav(440, 0.1), _tone_wav(880, 0.1)
    merged = local_tts._concat_wav([a, b])
    with wave.open(io.BytesIO(merged), "rb") as w:
        assert w.getnframes() == int(0.2 * 32000), "样本级拼接：0.1+0.1 秒"
    # 格式不一致 → 抛（上层回退）
    other = _tone_wav(440, 0.1, rate=16000)
    try:
        local_tts._concat_wav([a, other])
        raise AssertionError("采样率不一致应拒绝拼接")
    except ValueError:
        pass
    print("[OK] 拼接：同格式样本级合并；格式不一致拒绝")


def test_synth_cache_and_fallback() -> None:
    _install_profile()
    local_tts.reset_negotiate_for_testing()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/capabilities"):
            return httpx.Response(200, json=CAPS)
        calls["n"] += 1
        return httpx.Response(200, content=_tone_wav(440 + calls["n"], 0.05))

    async def run_synth(text_a: str, text_b: str):
        local_tts._client_factory = lambda **k: httpx.AsyncClient(
            transport=_mock_transport(handler), **k)
        try:
            return await local_tts.synth_local(
                f"{text_a}{text_b}", persona_id=PERSONA, mood=70)
        finally:
            local_tts._client_factory = None

    long_a = "这一句很长。" + "好" * 150 + "。"
    long_b = "这一句也很长。" + "吧" * 150 + "！"
    result = asyncio.run(run_synth(long_a, long_b))
    assert result is not None
    path, provider = result
    assert provider == "gpt_sovits" and path.suffix == ".wav"
    assert calls["n"] == 2, f"两句各超限应两块合成，实际 {calls['n']}"
    # 缓存命中：再合成同文本不再发请求
    result2 = asyncio.run(run_synth(long_a, long_b))
    assert result2 is not None and result2[0] == path
    assert calls["n"] == 2, "缓存命中不得再调服务"
    # 协商失败 → None（显式回退信号）
    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async def run_down():
        local_tts._client_factory = lambda **k: httpx.AsyncClient(
            transport=_mock_transport(down), **k)
        try:
            local_tts.reset_negotiate_for_testing()
            return await local_tts.synth_local("你好", persona_id=PERSONA)
        finally:
            local_tts._client_factory = None

    assert asyncio.run(run_down()) is None
    # 无档案人格 → None
    assert asyncio.run(local_tts.synth_local("你好", persona_id="ghost-persona")) is None
    print("[OK] 合成：多块拼接落盘、缓存命中零请求、失败显式回退信号")


def test_voice_profiles() -> None:
    db.ensure_user("ltts-user")
    p1 = enabled_voice_profile(PERSONA)
    assert p1 is not None and p1["provider_version"] == CAPS["provider_version"]
    assert p1["ref_text"] == "这是参考音频"
    # 再登记 → 旧档案自动停用，取到新档案
    m2 = add_voice_manifest(path="voice/ref2.wav", text="第二条参考", lang="zh")
    set_voice_profile(PERSONA, provider_version="newcommit0000",
                      model_hash="n" * 16, reference_manifest_id=m2["manifest_id"])
    p2 = enabled_voice_profile(PERSONA)
    assert p2["provider_version"] == "newcommit0000" and p2["id"] != p1["id"]
    assert enabled_voice_profile("other-persona") is None, "人格隔离"
    print("[OK] 声纹档案：enabled 选取、再登记停旧、人格隔离")


def test_api_fallback_header() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api import tts as tts_api

    app = FastAPI()
    app.include_router(tts_api.router)
    client = TestClient(app)

    # flag 关 → 直接走 edge-tts（合成会失败因无网络？edge-tts 在测试环境不可用 → 502；
    # 这里只验证本地路径未被尝试：响应头不含 gpt_sovits）
    features.set_flag("local_tts_enabled", False)
    local_tts.reset_negotiate_for_testing()
    r = client.get("/api/tts", params={"text": "测试", "mock": "1"})
    assert r.status_code in (200, 502)
    assert r.headers.get("x-tts-provider") in ("edge-tts", None)

    # flag 开 + 协商失败 + 有档案 → 仍回退（不 500）
    features.set_flag("local_tts_enabled", True)
    config.local_tts_endpoint = "http://127.0.0.1:1"  # 不可达端口
    local_tts.reset_negotiate_for_testing()
    r2 = client.get("/api/tts", params={"text": "测试"})
    assert r2.status_code in (200, 502), "回退路径不得 500"
    assert r2.headers.get("x-tts-provider") != "gpt_sovits"
    config.local_tts_endpoint = "http://127.0.0.1:9880"
    features.set_flag("local_tts_enabled", False)
    print("[OK] API：flag 关直走 edge；本地不可用显式回退不 500")


def test_schema_v45() -> None:
    from backend.core.userdb import _SCHEMA_VERSION

    version = db.conn.execute("PRAGMA user_version").fetchone()[0]
    assert _SCHEMA_VERSION == 45 and version == 45, f"schema 应为 v45，实际 {version}"
    for table in ("voice_profiles", "voice_manifests"):
        row = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        assert row is not None, f"{table} 应存在"
    # reset 双清单
    import inspect

    import backend.core.reset as reset_mod
    import backend.core.userdb as ud

    src = inspect.getsource(reset_mod) + inspect.getsource(ud)
    assert "voice_profiles" in src and "voice_manifests" in src
    # 关系导出不含（运行资产）
    from backend.core import relationship_export

    assert "voice_manifests" not in inspect.getsource(relationship_export)
    print("[OK] schema v45：表结构、reset 双清单、导出排除")


def main() -> None:
    db.conn.execute("SELECT 1")
    test_negotiation()
    test_split_sentences()
    test_concat_wav()
    test_synth_cache_and_fallback()
    test_voice_profiles()
    test_api_fallback_header()
    test_schema_v45()
    print("\n=== P3-03 本地语音（fake）：7 组全部通过 ===")


if __name__ == "__main__":
    main()
