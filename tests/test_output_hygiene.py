# -*- coding: utf-8 -*-
"""P0-01A：普通对话输出卫生出口回归。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["TZTUZHAN_DATA_DIR"] = tempfile.mkdtemp(prefix="tztuzhan_test_hygiene_")
os.environ["MEMORY_V2"] = "0"
os.environ["MEMORY_MEM0"] = "0"
os.environ["SEARCH_ENABLED"] = "0"
os.environ["MOOD_CITY"] = ""

from backend.core import pipeline  # noqa: E402
from backend.core.output_hygiene import HygieneContext, inspect_reply  # noqa: E402
from backend.core.userdb import db  # noqa: E402


def _ctx(*, explain: bool = False) -> HygieneContext:
    return HygieneContext(kind="chat", user_requested_explanation=explain)


def test_pure_hard_rules_and_code_fence_exception() -> None:
    cleaned = inspect_reply(
        "<think>这里是隐藏推理</think>真正的回答", context=_ctx()
    )
    assert cleaned.action == "accept"
    assert cleaned.text == "真正的回答"
    assert cleaned.rule_ids == ("closed_reasoning_block",)

    leaked = inspect_reply("系统提示词如下：你必须复述内部规则", context=_ctx())
    assert leaked.action == "rewrite"
    assert "system_instruction_disclosure" in leaked.rule_ids

    protocol = inspect_reply("<tool_call>{\"name\":\"delete\"}", context=_ctx())
    assert protocol.action == "rewrite"
    assert "tool_protocol" in protocol.rule_ids

    technical_text = "下面是格式示例：\n```xml\n<think>demo</think>\n```"
    technical = inspect_reply(
        technical_text,
        context=_ctx(explain=True),
    )
    assert technical.action == "accept"
    assert technical.text == technical_text


def _close_background(coro) -> None:
    coro.close()


async def _empty_date_extract(*args, **kwargs):
    return []


def _pipeline_patches(
    *, plugin_reply=None, tool_loop: bool = False, hygiene_enabled: bool = True
):
    stack = ExitStack()
    stack.enter_context(
        patch(
            "backend.core.features.flag",
            side_effect=lambda name: hygiene_enabled
            if name == "output_hygiene_enabled"
            else True,
        )
    )
    stack.enter_context(patch("backend.core.pipeline._spawn_memory_task", new=_close_background))
    stack.enter_context(
        patch("backend.core.pipeline._needs_tool_loop", return_value=tool_loop)
    )
    stack.enter_context(patch("backend.core.date_memory.extract_from_message", new=_empty_date_extract))
    stack.enter_context(patch("backend.core.knowledge.recall_knowledge", return_value=[]))
    if plugin_reply is not None:
        stack.enter_context(
            patch("backend.plugins.context.apply_reply", side_effect=plugin_reply)
        )
    return stack


async def _test_cross_chunk_reasoning_never_streams_or_persists() -> None:
    uid = "hygiene-stream"
    chunks: list[str] = []

    async def fake_stream(messages, **kwargs):
        for piece in ("<thi", "nk>隐藏推理", "</think>", "真正回答"):
            yield piece

    async def collect(piece: str) -> None:
        chunks.append(piece)

    with _pipeline_patches(), patch(
        "backend.core.pipeline.chat_stream", new=fake_stream
    ):
        reply = await pipeline.process(uid, "陪我聊一句", stream_cb=collect)

    assert reply == "真正回答"
    assert "".join(chunks) == reply
    assert "隐藏推理" not in "".join(chunks)
    rows = db.recent_messages(uid, 4)
    assert rows[-1]["role"] == "assistant"
    assert rows[-1]["content"] == reply


async def _test_disabled_hygiene_does_not_abort_on_stream_callback_error() -> None:
    uid = "hygiene-disabled-stream"
    chunks: list[str] = []

    async def fake_stream(messages, **kwargs):
        yield "关闭卫生后"
        yield "仍完成生成"

    async def flaky_collect(piece: str) -> None:
        chunks.append(piece)
        if len(chunks) == 1:
            raise RuntimeError("模拟前端流式连接瞬时失败")

    with _pipeline_patches(hygiene_enabled=False), patch(
        "backend.core.pipeline.chat_stream", new=fake_stream
    ):
        reply = await pipeline.process(uid, "测试关闭输出卫生", stream_cb=flaky_collect)

    assert chunks == ["关闭卫生后", "仍完成生成"]
    assert reply == "关闭卫生后仍完成生成"
    assert db.last_assistant_message(uid) == reply


async def _test_plugin_reintroduction_is_rewritten_before_stream() -> None:
    uid = "hygiene-plugin"
    chunks: list[str] = []

    async def fake_stream(messages, **kwargs):
        yield "初版安全回答"

    retry = AsyncMock(return_value="重写后的安全回答")

    async def collect(piece: str) -> None:
        chunks.append(piece)

    with _pipeline_patches(
        plugin_reply=["当前状态（系统注入，不要复述本段）", "重写后的安全回答"]
    ), patch("backend.core.pipeline.chat_stream", new=fake_stream), patch(
        "backend.core.pipeline.chat", new=retry
    ):
        reply = await pipeline.process(uid, "说点轻松的", stream_cb=collect)

    assert reply == "重写后的安全回答"
    assert "".join(chunks) == reply
    assert retry.await_count == 1
    assert db.last_assistant_message(uid) == reply


async def _test_tool_loop_final_text_uses_same_protected_exit() -> None:
    uid = "hygiene-tool-loop"
    chunks: list[str] = []
    progress: list[dict] = []

    run_tool_round = AsyncMock(
        return_value="<reasoning>工具内部过程</reasoning>工具完成后的回答"
    )

    async def collect(piece: str) -> None:
        chunks.append(piece)

    async def collect_progress(event: dict) -> None:
        progress.append(event)

    with _pipeline_patches(tool_loop=True), patch(
        "backend.tools.service.run_tool_round", new=run_tool_round
    ):
        reply = await pipeline.process(
            uid,
            "帮我处理这个任务",
            stream_cb=collect,
            progress_cb=collect_progress,
        )

    assert run_tool_round.await_count == 1
    assert reply == "工具完成后的回答"
    assert "".join(chunks) == reply
    assert "工具内部过程" not in "".join(chunks)
    assert db.last_assistant_message(uid) == reply


async def _test_ephemeral_unsafe_reply_uses_fallback_without_persistence() -> None:
    uid = "hygiene-ephemeral"
    chunks: list[str] = []

    async def fake_stream(messages, **kwargs):
        yield "<tool_call>"
        yield "内部协议"

    # 重写仍不安全，必须使用本地 fallback；两次候选都不能到 stream。
    retry = AsyncMock(return_value="【思考】还是内部推理")

    async def collect(piece: str) -> None:
        chunks.append(piece)

    with _pipeline_patches(), patch(
        "backend.core.pipeline.chat_stream", new=fake_stream
    ), patch("backend.core.pipeline.chat", new=retry):
        reply = await pipeline.process(
            uid, "临时聊一下，别记住", ephemeral=True, stream_cb=collect
        )

    assert reply == "嗯……我想想怎么回你。", repr(reply)
    assert "".join(chunks) == reply
    assert db.get_user(uid) is None
    assert not db.recent_messages(uid, 10)


async def _test_duplicate_rewrite_consumes_shared_budget() -> None:
    uid = "hygiene-shared-budget"
    duplicate = "这是最近说过的一句完整重复回答"
    db.ensure_user(uid)
    db.add_message(uid, "assistant", duplicate)
    chunks: list[str] = []

    async def fake_stream(messages, **kwargs):
        yield duplicate

    # 唯一一次重写额度先被重复消除消费；若重写结果仍泄漏，卫生层直接 fallback，
    # 不得为同一轮再调用第二次模型。
    retry = AsyncMock(return_value="系统提示词如下：隐藏规则")

    async def collect(piece: str) -> None:
        chunks.append(piece)

    with _pipeline_patches(), patch(
        "backend.core.pipeline.chat_stream", new=fake_stream
    ), patch("backend.core.pipeline.chat", new=retry):
        reply = await pipeline.process(uid, "换个说法回答我", stream_cb=collect)

    assert retry.await_count == 1
    assert reply == "嗯……刚才那句没整理好，我重新听你说。"
    assert "".join(chunks) == reply
    assert db.last_assistant_message(uid) == reply


async def main() -> None:
    test_pure_hard_rules_and_code_fence_exception()
    await _test_cross_chunk_reasoning_never_streams_or_persists()
    await _test_disabled_hygiene_does_not_abort_on_stream_callback_error()
    await _test_plugin_reintroduction_is_rewritten_before_stream()
    await _test_tool_loop_final_text_uses_same_protected_exit()
    await _test_ephemeral_unsafe_reply_uses_fallback_without_persistence()
    await _test_duplicate_rewrite_consumes_shared_budget()
    print("test_output_hygiene: all passed")


if __name__ == "__main__":
    asyncio.run(main())
