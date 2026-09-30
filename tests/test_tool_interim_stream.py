# -*- coding: utf-8 -*-
"""工具选择轮过渡语流式：chat_native_stream 聚合流式 tool_calls 分片；
run_tool_loop 的 interim/interim_reset 事件序；service 条件传参保持旧契约。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_interim_"))

from backend.core import llm
from backend.tools.base import ToolRegistry
from backend.tools.tool_loop import run_tool_loop


# ---- chat_native_stream：SDK 流分片 → 正文外推 + tool_calls 聚合 ----

def _fake_stream_client(chunks):
    """构造假 AsyncOpenAI 替身：chat.completions.create(stream=True) 逐块产出 chunks。"""

    class _FakeCompletions:
        async def create(self, **kwargs):
            async def _gen():
                for c in chunks:
                    yield c
            return _gen()

    class _FakeChat:
        def __init__(self):
            self.completions = _FakeCompletions()

    class _FakeClient:
        def __init__(self, **kwargs):
            self.chat = _FakeChat()

    return _FakeClient


def _chunk(delta, usage=None):
    return SimpleNamespace(usage=usage, choices=[SimpleNamespace(delta=delta)])


def _route():
    return SimpleNamespace(base_url="https://fake", key_ref="LLM_API_KEY", model="fake",
                           timeout_sec=10, max_tokens=100, fallback_tasks=())


async def test_native_stream_aggregates_tool_calls() -> None:
    pieces: list[str] = []

    async def on_text(piece: str) -> None:
        pieces.append(piece)

    chunks = [
        _chunk(SimpleNamespace(content=None, reasoning_content="先想", tool_calls=None)),
        _chunk(SimpleNamespace(content=None, reasoning_content="一步", tool_calls=None)),
        _chunk(SimpleNamespace(content="我查一下", tool_calls=None)),
        _chunk(SimpleNamespace(content=None, tool_calls=[
            SimpleNamespace(index=0, id="call_1",
                            function=SimpleNamespace(name="web_search", arguments='{"q"')),
        ])),
        _chunk(SimpleNamespace(content=None, tool_calls=[
            SimpleNamespace(index=0, id=None,
                            function=SimpleNamespace(name=None, arguments=':"天气"}')),
        ])),
        SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1), choices=[]),
    ]
    with ExitStack() as stack:
        stack.enter_context(patch("backend.core.model_routes.resolve_route", return_value=_route()))
        stack.enter_context(patch("backend.core.model_routes.resolve_api_key", return_value="k"))
        stack.enter_context(patch("openai.AsyncOpenAI", _fake_stream_client(chunks)))
        stack.enter_context(patch("backend.core.llm._build_http_client", return_value=None))
        llm._client_cache.clear()
        try:
            text, calls, reasoning = await llm.chat_native_stream(
                [{"role": "user", "content": "查天气"}],
                [{"type": "function", "function": {"name": "web_search"}}],
                on_text=on_text,
            )
        finally:
            llm._client_cache.clear()
    assert text == "我查一下", text
    assert pieces == ["我查一下"], pieces
    assert calls == [{"name": "web_search", "arguments": {"q": "天气"}}], calls
    assert reasoning == "先想一步", reasoning
    print("[OK] chat_native_stream：正文外推 + 流式 tool_calls 分片聚合 + reasoning 聚合")


# ---- run_tool_loop：interim/interim_reset 事件序 ----

async def test_interim_events_order() -> None:
    events: list[dict] = []

    async def progress(ev: dict) -> None:
        events.append(ev)

    TOOL = "interim_echo"

    async def echo(q: str) -> str:
        return f"echo:{q}"

    ToolRegistry.register_func(
        TOOL, "t", echo,
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )

    async def stream_native(work, tools, on_text):
        await on_text("我查一下哈")
        await on_text("稍等。")
        return "我查一下哈稍等。", [{"name": TOOL, "arguments": {"q": "x"}}]

    async def final_stream(work):
        yield "查到啦"

    try:
        out = await run_tool_loop(
            [{"role": "user", "content": "查一下"}], lambda _: "",
            call_native=_should_not_run,
            call_native_stream=stream_native,
            call_final_stream=final_stream,
            on_progress=progress,
        )
        assert out == "查到啦"
        types = [e["type"] for e in events]
        assert types[0] == "thinking", types
        assert {"type": "interim", "text": "我查一下哈"} in events, events
        assert {"type": "interim", "text": "稍等。"} in events, events
        # 过渡语清空必须发生在工具开始执行之前（气泡最终正文=持久化正文）
        assert types.index("interim_reset") < types.index("tool"), types
        assert "tool_done" in types, types
    finally:
        ToolRegistry.unregister(TOOL)
    print("[OK] interim/interim_reset 事件序（reset 先于工具执行）")


async def test_interim_reset_before_final_stream() -> None:
    """选择轮无工具调用时：过渡语也要在最终流式开始前清空。"""
    timeline: list[tuple[str, str]] = []

    async def progress(ev: dict) -> None:
        timeline.append(("ev", str(ev.get("type"))))

    async def stream_native(work, tools, on_text):
        await on_text("这个问题嘛")
        return "这个问题嘛", []

    async def final_stream(work):
        timeline.append(("piece", "final:0"))
        yield "最终答案"

    out = await run_tool_loop(
        [{"role": "user", "content": "你好"}], lambda _: "",
        call_native=_should_not_run,
        call_native_stream=stream_native,
        call_final_stream=final_stream,
        on_progress=progress,
    )
    assert out == "最终答案"
    assert ("ev", "interim_reset") in timeline, timeline
    assert timeline.index(("ev", "interim_reset")) < timeline.index(("piece", "final:0")), timeline
    print("[OK] 无工具轮：interim_reset 先于最终流式")


async def _should_not_run(work, tools):
    raise AssertionError("提供流式回调时不应再走非流式原生调用")


# ---- service.run_tool_round：新参数条件传参，旧测试替身签名不炸 ----

async def test_service_passes_stream_kwarg_conditionally() -> None:
    import backend.tools.service as service

    captured: dict = {}

    async def fake_run_tool_loop(messages, **kwargs):
        captured.update(kwargs)
        return "ok"

    async def _stream(w, t, on_text):
        return "", []

    with patch("backend.tools.tool_loop.run_tool_loop", fake_run_tool_loop):
        await service.run_tool_round(
            [{"role": "user", "content": "x"}],
            chat=lambda ms: None,
            chat_native=lambda ms, t: ("", []),
        )
        assert "call_native_stream" not in captured, captured.keys()
        await service.run_tool_round(
            [{"role": "user", "content": "x"}],
            chat=lambda ms: None,
            chat_native=lambda ms, t: ("", []),
            chat_native_stream=_stream,
        )
        assert captured.get("call_native_stream") is _stream
    print("[OK] service 条件传参：不传 stream 回调时保持旧调用契约")


async def main() -> None:
    await test_native_stream_aggregates_tool_calls()
    await test_interim_events_order()
    await test_interim_reset_before_final_stream()
    await test_service_passes_stream_kwarg_conditionally()
    print("\n=== 过渡语流式: 全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
