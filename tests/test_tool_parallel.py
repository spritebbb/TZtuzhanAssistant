# -*- coding: utf-8 -*-
"""同轮工具并行执行：只读工具分批 gather 并发跑；写/MCP 类保持串行；裁决先行。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_par_"))

from backend.tools.base import ToolRegistry
from backend.tools.tool_loop import run_tool_loop


async def test_read_tools_run_concurrently() -> None:
    """两只只读工具同轮各睡 0.2s：并行时两个 start 都先于任何 end。"""
    events: list[str] = []

    async def slow_a(q: str) -> str:
        events.append("a_start")
        await asyncio.sleep(0.2)
        events.append("a_end")
        return f"A:{q}"

    async def slow_b(q: str) -> str:
        events.append("b_start")
        await asyncio.sleep(0.2)
        events.append("b_end")
        return f"B:{q}"

    ToolRegistry.register_func(
        "par_a", "test", slow_a,
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )
    ToolRegistry.register_func(
        "par_b", "test", slow_b,
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )

    histories: list[list[dict]] = []
    rounds = {"n": 0}

    async def native(work, tools):
        histories.append(list(work))
        rounds["n"] += 1
        if rounds["n"] == 1:
            return "", [
                {"name": "par_a", "arguments": {"q": "x"}},
                {"name": "par_b", "arguments": {"q": "y"}},
            ]
        return "done", []

    async def warmup_native(work, tools):
        # 预热：首次 run_tool_loop 有 ~0.8s 冷启动导入（external_content/审计链），
        # 不计进并发耗时断言
        if rounds["n"] == 0:
            rounds["n"] = -1
            return "", [{"name": "par_a", "arguments": {"q": "warm"}}]
        return "warm", []

    try:
        await run_tool_loop(
            [{"role": "user", "content": "预热"}], lambda _: "", call_native=warmup_native,
        )
        rounds["n"] = 0
        histories.clear()
        events.clear()
        t0 = time.monotonic()
        out = await run_tool_loop(
            [{"role": "user", "content": "并行查一下"}], lambda _: "", call_native=native,
        )
        elapsed = time.monotonic() - t0
        assert out == "done"
        # 并发证据：两个 start 都在任何 end 之前（串行会是 a_start,a_end,b_start,b_end）
        assert events[:2] == ["a_start", "b_start"], events
        assert sorted(events[2:]) == ["a_end", "b_end"], events
        # 并行时总耗时接近单次 0.2s，而非串行的 0.4s+
        assert elapsed < 0.38, elapsed
        # 两只工具的结果都按原顺序回填给了模型
        tool_contents = [m["content"] for m in histories[-1] if m.get("role") == "tool"]
        assert any("A:x" in c for c in tool_contents) and any("B:y" in c for c in tool_contents)
    finally:
        ToolRegistry.unregister("par_a")
        ToolRegistry.unregister("par_b")
    print("[OK] 只读工具同轮并发（两 start 先于两 end，耗时≈单次）")


async def test_write_and_mcp_tools_stay_sequential() -> None:
    """write 类与 MCP 工具不进并行批：混合调用保持逐个串行的执行顺序。"""
    events: list[str] = []

    async def reader(q: str) -> str:
        events.append("r_start")
        await asyncio.sleep(0.1)
        events.append("r_end")
        return "R"

    async def writer(q: str) -> str:
        events.append("w_start")
        await asyncio.sleep(0.1)
        events.append("w_end")
        return "W"

    async def mcp_reader(q: str) -> str:
        events.append("m_start")
        await asyncio.sleep(0.1)
        events.append("m_end")
        return "M"

    ToolRegistry.register_func("seq_read", "test", reader, category="read")
    ToolRegistry.register_func("seq_write", "test", writer, category="write")
    ToolRegistry.register_func("seq_mcp", "test", mcp_reader, category="read", owner="mcp:fake")

    rounds = {"n": 0}

    async def native(work, tools):
        rounds["n"] += 1
        if rounds["n"] == 1:
            return "", [
                {"name": "seq_read", "arguments": {"q": "1"}},
                {"name": "seq_write", "arguments": {"q": "2"}},
                {"name": "seq_mcp", "arguments": {"q": "3"}},
            ]
        return "done", []

    try:
        out = await run_tool_loop(
            [{"role": "user", "content": "查一下"}], lambda _: "", call_native=native,
        )
        assert out == "done"
        # 三段完全串行：start→end 交替，不交叠
        assert events == ["r_start", "r_end", "w_start", "w_end", "m_start", "m_end"], events
    finally:
        for name in ("seq_read", "seq_write", "seq_mcp"):
            ToolRegistry.unregister(name)
    print("[OK] write/MCP 工具保持串行")


async def test_same_round_duplicate_reuses() -> None:
    """同轮重复指纹：裁决期即标记复用，只真实执行一次。"""
    calls: list[str] = []
    histories: list[list[dict]] = []
    rounds = {"n": 0}

    async def echo(q: str) -> str:
        calls.append(q)
        return f"echo:{q}"

    ToolRegistry.register_func(
        "dup_echo", "test", echo,
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )

    async def native(work, tools):
        histories.append(list(work))
        rounds["n"] += 1
        if rounds["n"] == 1:
            return "", [
                {"name": "dup_echo", "arguments": {"q": "same"}},
                {"name": "dup_echo", "arguments": {"q": "same"}},
            ]
        return "done", []

    try:
        out = await run_tool_loop(
            [{"role": "user", "content": "查一下"}], lambda _: "", call_native=native,
        )
        assert out == "done" and calls == ["same"], (out, calls)
        tool_messages = [m for m in histories[-1] if m.get("role") == "tool"]
        assert "重复调用已复用" in tool_messages[1]["content"], tool_messages[1]["content"]
    finally:
        ToolRegistry.unregister("dup_echo")
    print("[OK] 同轮重复指纹复用不二跑")


async def main() -> None:
    await test_read_tools_run_concurrently()
    await test_write_and_mcp_tools_stay_sequential()
    await test_same_round_duplicate_reuses()
    print("\n=== 工具并行执行: 全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
