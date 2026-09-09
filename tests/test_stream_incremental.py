# -*- coding: utf-8 -*-
"""批次 16B/16C：增量卫生流与工具最终轮流式回归。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.output_hygiene import HygieneContext, inspect_reply  # noqa: E402
from backend.core.stream_hygiene import IncrementalHygieneStream  # noqa: E402
from backend.tools.tool_loop import run_tool_loop  # noqa: E402


async def test_sentence_is_emitted_before_provider_finishes() -> None:
    chunks: list[str] = []

    async def emit(piece: str) -> None:
        chunks.append(piece)

    stream = IncrementalHygieneStream(emit, context=HygieneContext())
    await stream.feed("第一句已经完整。第二句还")
    assert "".join(chunks) == "第一句已经完整。"
    await stream.feed("没结束")
    await stream.finish("第一句已经完整。第二句还没结束")
    assert "".join(chunks) == "第一句已经完整。第二句还没结束"


async def test_cross_chunk_marker_never_leaks() -> None:
    chunks: list[str] = []

    async def emit(piece: str) -> None:
        chunks.append(piece)

    ctx = HygieneContext()
    stream = IncrementalHygieneStream(emit, context=ctx)
    for piece in ("<thi", "nk>隐藏。", "</think>", "可见回答。"):
        await stream.feed(piece)
    checked = inspect_reply("<think>隐藏。</think>可见回答。", context=ctx)
    assert checked.action == "accept"
    await stream.finish(checked.text)
    visible = "".join(chunks)
    assert visible == "可见回答。"
    assert "隐藏" not in visible


async def test_final_rewrite_resets_to_authoritative_text() -> None:
    events: list[str] = []

    async def emit(piece: str) -> None:
        events.append(piece)

    stream = IncrementalHygieneStream(emit, context=HygieneContext())
    await stream.feed("模型初稿。")
    await stream.finish("插件改写后的定稿。")
    reset = events.index("\x00RESET\x00")
    assert "".join(events[:reset]) == "模型初稿。"
    assert "".join(events[reset + 1:]) == "插件改写后的定稿。"


async def test_tool_final_stream_produces_before_completion() -> None:
    first_piece = asyncio.Event()
    release = asyncio.Event()

    async def native(messages, tools):
        return "非流式草稿", []

    async def final_stream(messages):
        assert messages[-1]["role"] == "user"
        assert messages[-2]["content"] == "只输出最终答复"
        yield "工具结果已经整理。"
        first_piece.set()
        await release.wait()
        yield "这是最终回答。"

    task = asyncio.create_task(
        run_tool_loop(
            [{"role": "user", "content": "查完后回答"}],
            lambda messages: None,
            call_native=native,
            call_final_stream=final_stream,
            final_instruction=[{"role": "system", "content": "只输出最终答复"}],
        )
    )
    await asyncio.wait_for(first_piece.wait(), timeout=1)
    assert not task.done()
    release.set()
    assert await task == "工具结果已经整理。这是最终回答。"


async def main() -> None:
    await test_sentence_is_emitted_before_provider_finishes()
    await test_cross_chunk_marker_never_leaks()
    await test_final_rewrite_resets_to_authoritative_text()
    await test_tool_final_stream_produces_before_completion()
    print("test_stream_incremental: all passed")


if __name__ == "__main__":
    asyncio.run(main())
