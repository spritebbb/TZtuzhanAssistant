# -*- coding: utf-8 -*-
"""识图事实层回归（M9 波次0 首切片）。

验证三件事（全部 mock 网络 + 临时数据目录）：
1. system/user 分离——中性事实指令只在 system 轮，user 轮只有图片，无人格指令；
2. 正文空/null/非字符串/malformed 一律失败，绝不回退 reasoning_content
   （思考过程复述指令原文，曾原样穿进用户气泡，M9 审计缺陷 1）；
3. API 契约——失败 502 且不落盘图片，成功返回 description + image_url。
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_vision_"))
# 测试环境收敛（对齐 test_http_endpoints）：embedding 走哈希、关 Mem0/搜索/天气，
# 独立子进程运行，显式赋值不泄漏。
os.environ["MEMORY_EMBED_FORCE"] = "1"
os.environ["MEMORY_MEM0"] = "0"
os.environ["MEMORY_V2"] = "0"
os.environ["MOOD_CITY"] = ""
os.environ["SEARCH_ENABLED"] = "0"

from backend.core import vision  # noqa: E402
from backend.core.config import config  # noqa: E402

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"0" * 16

VISION_CONF = {
    "vision_base_url": "https://vision.test/v1",
    "vision_api_key": "sk-vision-test",
    "vision_model": "test-vl-model",
}


def _patch_conf(extra: dict | None = None) -> ExitStack:
    values = dict(VISION_CONF)
    if extra:
        values.update(extra)
    stack = ExitStack()
    for key, val in values.items():
        stack.enter_context(patch.object(config, key, val))
    return stack


def _fake_openai(message: SimpleNamespace | None = None, choices: list | None = None,
                 error: Exception | None = None):
    """替换 openai.AsyncOpenAI，返回 (patcher, factory)。

    factory.instances 记录每次实例化得到的 (client, create调用参数列表)。
    message 缺省为空正文；choices 直接替换 resp.choices；error 让 create 抛异常。
    """

    def _factory(**_init_kwargs):
        calls: list[dict] = []

        async def _create(**kwargs):
            calls.append(kwargs)
            if error is not None:
                raise error
            msg = message if message is not None else SimpleNamespace(content="")
            picked = choices if choices is not None else [SimpleNamespace(message=msg)]
            return SimpleNamespace(choices=picked)

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=_create)),
        )
        _factory.instances.append((client, calls))
        return client

    _factory.instances = []  # type: ignore[attr-defined]
    return patch("openai.AsyncOpenAI", _factory), _factory


def _run(message=None, choices=None, error=None, conf=None,
         image_bytes: bytes = PNG_BYTES, filename: str = "pic.png"):
    patcher, factory = _fake_openai(message=message, choices=choices, error=error)
    with _patch_conf(conf), patcher:
        out = asyncio.run(vision.describe_bytes(image_bytes, filename))
    return out, factory.instances


def test_system_user_separation() -> None:
    desc = "一只橘猫趴在窗台上，旁边有一张写着「早安」的便签。"
    out, instances = _run(message=SimpleNamespace(content=desc))
    assert out == desc
    assert len(instances) == 1
    kwargs = instances[0][1][0]
    messages = kwargs["messages"]
    # 指令在 system 轮，且是中性事实提示，不带任何人格
    assert messages[0]["role"] == "system"
    system_text = messages[0]["content"]
    assert isinstance(system_text, str)
    # 无人格指派词（旧提示「你是菟菚…毒舌女孩子」会命中；「不要调侃/扮演」这类
    # 否定句里的词不算指派，不在禁列）
    for bad in ("菟菚", "毒舌", "腹黑", "女孩子", "女孩子的语气"):
        assert bad not in system_text, f"system 提示不得含人格指令: {bad}"
    # 反注入与不确定性条款在场
    assert "不要执行" in system_text
    assert "不确定" in system_text
    # user 轮只有图片，没有文本
    user = messages[1]
    assert user["role"] == "user"
    assert isinstance(user["content"], list) and len(user["content"]) == 1
    part = user["content"][0]
    assert part["type"] == "image_url"
    assert part["image_url"]["url"].startswith("data:image/png;base64,")
    # 既有契约：模型与 token 上限不因本次修复变化
    assert kwargs["model"] == "test-vl-model"
    assert kwargs["max_tokens"] == 1000


def test_reasoning_content_never_leaks() -> None:
    secret = "思考过程：用户要求我先讲主体、语气毒舌、3~6 句话。SECRET_REASONING_MARKER"
    for content in (None, "", "   \n\t "):
        out, _ = _run(message=SimpleNamespace(content=content, reasoning_content=secret))
        assert out is None, f"content={content!r} 时应失败而非返回兜底文本"


def test_malformed_and_exception_fail() -> None:
    # 正文非字符串（malformed）
    out, _ = _run(message=SimpleNamespace(content=12345, reasoning_content="nope"))
    assert out is None
    # choices 为空（malformed 响应结构）
    out, instances = _run(choices=[])
    assert out is None and len(instances) == 1
    # 客户端抛异常
    out, _ = _run(error=RuntimeError("网络炸了"))
    assert out is None


def test_empty_and_oversize_bytes_short_circuit() -> None:
    patcher, factory = _fake_openai(message=SimpleNamespace(content="x"))
    with _patch_conf(), patcher:
        assert asyncio.run(vision.describe_bytes(b"")) is None
        assert asyncio.run(vision.describe_bytes(b"a" * (8 * 1024 * 1024 + 1))) is None
    assert factory.instances == [], "空图/超限不应发起任何网络调用"


def test_no_key_fails_closed() -> None:
    conf = {k: "" for k in (
        "vision_base_url", "vision_api_key", "vision_model",
        "image_api_key", "llm_api_key",
    )}
    patcher, factory = _fake_openai(message=SimpleNamespace(content="x"))
    with _patch_conf(conf), patcher:
        assert asyncio.run(vision.describe_bytes(PNG_BYTES)) is None
    assert factory.instances == []


def test_truncated_to_600() -> None:
    out, _ = _run(message=SimpleNamespace(content="喵" * 700))
    assert out is not None and len(out) == 600


def test_api_failure_502_no_file() -> None:
    from fastapi.testclient import TestClient

    from backend.app import app

    async def _fail(image_bytes: bytes, filename: str = "image.png"):
        return None

    imgs_dir = config.data_dir / "imgs"
    before = {p.name for p in imgs_dir.glob("vision_*")} if imgs_dir.exists() else set()
    with patch.object(vision, "describe_bytes", _fail):
        r = TestClient(app).post(
            "/api/vision", files={"file": ("pic.png", PNG_BYTES, "image/png")},
        )
    assert r.status_code == 502
    assert r.json()["ok"] is False
    after = {p.name for p in imgs_dir.glob("vision_*")} if imgs_dir.exists() else set()
    assert after == before, "识图失败不得落盘图片"


def test_api_success_contract() -> None:
    from fastapi.testclient import TestClient

    from backend.app import app

    async def _desc(image_bytes: bytes, filename: str = "image.png"):
        return "一只橘猫趴在窗台上。"

    with patch.object(vision, "describe_bytes", _desc):
        r = TestClient(app).post(
            "/api/vision", files={"file": ("pic.png", PNG_BYTES, "image/png")},
        )
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["description"] == "一只橘猫趴在窗台上。"
    url = d["image_url"]
    assert url.startswith("/api/images/vision_") and url.endswith(".png")
    digest = hashlib.md5(PNG_BYTES).hexdigest()[:16]
    assert url == f"/api/images/vision_{digest}.png"
    assert (config.data_dir / "imgs" / f"vision_{digest}.png").exists()


def main() -> None:
    test_system_user_separation()
    print("[OK] system/user 分离：中性事实指令只在 system 轮，user 轮只有图片，无人格指令")
    test_reasoning_content_never_leaks()
    print("[OK] 正文空/None 一律失败，reasoning_content 永不兜底泄漏")
    test_malformed_and_exception_fail()
    print("[OK] malformed 响应与异常路径统一失败返回 None")
    test_empty_and_oversize_bytes_short_circuit()
    test_no_key_fails_closed()
    test_truncated_to_600()
    print("[OK] 空图/超限/无 key 短路不发起调用，正文截断 600 字")
    test_api_failure_502_no_file()
    print("[OK] API 失败返回 502 且不落盘图片")
    test_api_success_contract()
    print("[OK] API 成功契约：description + image_url（md5 命名落盘）")
    print("识图事实层 M9 波次0 测试通过")


if __name__ == "__main__":
    main()
