# -*- coding: utf-8 -*-
"""通用人格卡生成器：模板提示词构造、输出校验、名字提取、API 端点。

运行：python -m tests.test_persona_generator
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_persona_gen_"))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.personas import router
from backend.core import persona_generator

_VALID_CARD = """---
name: 橘猫程序员
subtitle: 毒舌但心软的猫娘同事
theme: dark
voice: zh-CN-XiaoxiaoNeural
---

# 角色卡：橘猫程序员

> 固定人格，好感度阶段、称呼由系统注入。

## 身份

你是「阿橘」，一个猫娘程序员。

## 性格

- **嘴硬心软**：吐槽你不带重样，但你加班晚了她会默默留一盏灯

## 行为准则（检验而非顺从）

1. **事实与一致性**：对方说「我们是老同学」，你没这个记忆就不认。

## 说话风格（严格遵守）

1. 口语化、短句、不用句号

## 示例对话

【初识】
你：今晚加班吗
阿橘：加啊
不像某人有猫可撸

## 好感度阶段（由系统注入「当前阶段」，行为随之变化）

- **阶段一「初识」**：客气疏离，吐槽点到为止
- **阶段二「熟悉」**：会开玩笑
- **阶段三「亲密」**：会蹭你
- **阶段四「恋人」**：呼噜声只给你听

## 输出要求

始终用简体中文，只输出台词。
"""


def test_build_messages() -> None:
    messages = persona_generator.build_messages("一只毒舌的猫娘程序员")
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    # 模板卡全文嵌入 system（结构基准），简报在 user
    assert "菟菚" in messages[0]["content"]
    assert "检验而非顺从" in messages[0]["content"]
    assert "初识" in messages[0]["content"] and "恋人" in messages[0]["content"]
    assert "毒舌的猫娘程序员" in messages[1]["content"]
    # 空简报拒绝；超长简报截断
    try:
        persona_generator.build_messages("   ")
        raise AssertionError("空简报应拒绝")
    except ValueError:
        pass
    truncated = persona_generator.build_messages("喵" * 5000)
    assert "喵" * 4000 in truncated[1]["content"] and "喵" * 4001 not in truncated[1]["content"]
    print("[OK] build_messages：模板嵌入 + 简报透传 + 截断")


def test_sanitize_card() -> None:
    card = persona_generator.sanitize_card(_VALID_CARD)
    assert card.startswith("---") and card.endswith("\n")
    assert "## 身份" in card
    # 代码围栏剥壳
    fenced = "```markdown\n" + _VALID_CARD + "\n```"
    assert persona_generator.sanitize_card(fenced) == card
    # 缺标题（front matter 之后直接是引用行）
    try:
        persona_generator.sanitize_card(_VALID_CARD.replace("# 角色卡：橘猫程序员\n", ""))
        raise AssertionError("缺标题应拒绝")
    except ValueError as exc:
        assert "标题" in str(exc)
    # 缺必备小节
    gutted = "\n".join(line for line in _VALID_CARD.splitlines() if "## 示例对话" not in line)
    try:
        persona_generator.sanitize_card(gutted)
        raise AssertionError("缺小节应拒绝")
    except ValueError as exc:
        assert "示例对话" in str(exc)
    # 阶段名被自由发挥（系统写死初识/熟悉/亲密/恋人）
    renamed = _VALID_CARD.replace("初识", "陌路").replace("恋人", "老伴")
    try:
        persona_generator.sanitize_card(renamed)
        raise AssertionError("自造阶段名应拒绝")
    except ValueError as exc:
        assert "初识/熟悉/亲密/恋人" in str(exc)
    print("[OK] sanitize_card：围栏剥壳 + 标题/小节/阶段名校验")


def test_card_name() -> None:
    assert persona_generator.card_name(_VALID_CARD) == "橘猫程序员"
    fallback = "# 角色卡：没有元信息的卡\n\n## 身份\n\n某人。" + "".join(
        f"\n## {section}" for section in ("性格", "行为准则", "说话风格", "示例对话")
    ) + "\n\n初识 熟悉 亲密 恋人\n"
    assert persona_generator.card_name(fallback) == "没有元信息的卡"
    print("[OK] card_name：front matter 优先，回退首标题")


async def test_generate_card_monkeypatched() -> None:
    from backend.core import llm

    async def fake_stream(messages, **kwargs):
        assert messages[1]["role"] == "user"
        assert kwargs.get("max_tokens", 0) >= 4000  # 短回复口径的 500 必截断
        for piece in (_VALID_CARD[:100], _VALID_CARD[100:]):
            yield piece

    original = llm.chat_stream
    llm.chat_stream = fake_stream
    try:
        card = await persona_generator.generate_card("猫娘程序员")
        assert card == persona_generator.sanitize_card(_VALID_CARD)
    finally:
        llm.chat_stream = original

    # 截断兜底：长输出缺尾部小节 → 自动续写一次，拼装后过校验
    head = _VALID_CARD.split("## 示例对话", 1)[0]
    head = head.replace("## 性格\n", "## 性格\n\n" + ("补充特质描写。" * 400) + "\n")
    assert len(head) >= persona_generator._CONTINUE_MIN_CHARS
    tail = "## 示例对话" + _VALID_CARD.split("## 示例对话", 1)[1]
    calls: list[list[dict]] = []

    async def truncated_stream(messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            yield head
        else:
            assert messages[-2]["role"] == "assistant"  # 续写带上半成品
            assert "示例对话" in messages[-1]["content"]
            for index in range(0, len(tail), 50):
                yield tail[index:index + 50]

    llm.chat_stream = truncated_stream
    try:
        card = await persona_generator.generate_card("猫娘程序员")
        assert persona_generator.sanitize_card(card) == card
        assert "## 示例对话" in card
        assert len(calls) == 2
    finally:
        llm.chat_stream = original

    async def junk_stream(messages, **kwargs):
        yield "这不是一张卡，就是一段闲聊。"  # 太短，判胡写不判截断，不触发续写

    llm.chat_stream = junk_stream
    try:
        try:
            await persona_generator.generate_card("猫娘程序员")
            raise AssertionError("垃圾输出应拒绝")
        except ValueError:
            pass
    finally:
        llm.chat_stream = original
    print("[OK] generate_card：流式拼装 + 截断续写 + 校验兜底")


def test_api_generate() -> None:
    from backend.core import persona_generator as gen_module

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    async def fake_generate(brief: str) -> str:
        assert brief == "猫娘程序员"
        return _VALID_CARD

    original = gen_module.generate_card
    gen_module.generate_card = fake_generate
    try:
        resp = client.post("/api/personas/generate", json={"brief": "猫娘程序员"})
        assert resp.status_code == 200 and resp.json()["ok"] is True
        assert resp.json()["name"] == "橘猫程序员"
        assert "阿橘" in resp.json()["markdown"]
    finally:
        gen_module.generate_card = original

    # 空简报 400；LLM 失败 502；坏 JSON 400
    assert client.post("/api/personas/generate", json={"brief": "  "}).status_code == 400

    async def boom(brief: str) -> str:
        raise RuntimeError("未配置 API Key")

    gen_module.generate_card = boom
    try:
        resp = client.post("/api/personas/generate", json={"brief": "猫娘"})
        assert resp.status_code == 502 and "生成失败" in resp.json()["error"]
    finally:
        gen_module.generate_card = original
    assert client.post("/api/personas/generate", content=b"not-json",
                       headers={"Content-Type": "application/json"}).status_code == 400
    print("[OK] API /api/personas/generate：200/400/502")


def main() -> None:
    test_build_messages()
    test_sanitize_card()
    test_card_name()
    asyncio.run(test_generate_card_monkeypatched())
    # TestClient 自管事件循环，保持在无运行循环的上下文里调用
    test_api_generate()
    print("\n=== 通用人格卡生成器：全部通过 ===")


if __name__ == "__main__":
    main()
