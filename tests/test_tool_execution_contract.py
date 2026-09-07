# -*- coding: utf-8 -*-
"""P3-02A 工具执行合同：配对、去重、取消与硬上限。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.tools.base import ToolRegistry
from backend.tools.tool_loop import MAX_TOOL_CALLS, run_tool_loop

TOOL = "p3_contract_counter"


async def test_native_pairing_and_dedup() -> None:
    executions: list[str] = []
    histories: list[list[dict]] = []

    async def counter(value: str) -> str:
        executions.append(value)
        return f"ok:{value}"

    ToolRegistry.register_func(
        TOOL, "test", counter,
        input_schema={"type": "object", "properties": {"value": {"type": "string"}},
                      "required": ["value"]},
    )

    async def native(work, tools):
        histories.append(list(work))
        if len(histories) == 1:
            call = {"name": TOOL, "arguments": {"value": "same"}}
            return "", [dict(call), dict(call)]
        return "完成", []

    try:
        out = await run_tool_loop(
            [{"role": "user", "content": "执行"}], lambda _: "", call_native=native,
        )
        assert out == "完成" and executions == ["same"]
        tool_messages = [m for m in histories[-1] if m.get("role") == "tool"]
        assistant = next(m for m in histories[-1] if m.get("tool_calls"))
        assert [m["tool_call_id"] for m in tool_messages] == [
            call["id"] for call in assistant["tool_calls"]
        ]
        assert "重复调用已复用" in tool_messages[1]["content"]
    finally:
        ToolRegistry.unregister(TOOL)


async def test_cancel_and_call_limit() -> None:
    count = 0

    async def counter(value: str) -> str:
        nonlocal count
        count += 1
        return value

    ToolRegistry.register_func(TOOL, "test", counter)

    async def should_not_run(work, tools):
        raise AssertionError("取消后不得再调用模型")

    try:
        out = await run_tool_loop(
            [{"role": "user", "content": "取消"}], lambda _: "",
            call_native=should_not_run, is_cancelled=lambda: True,
        )
        assert "已取消" in out and count == 0

        rounds = 0

        async def native(work, tools):
            nonlocal rounds
            rounds += 1
            if rounds == 1:
                return "", [
                    {"name": TOOL, "arguments": {"value": str(i)}}
                    for i in range(MAX_TOOL_CALLS + 3)
                ]
            return "完成", []

        await run_tool_loop(
            [{"role": "user", "content": "执行很多次"}], lambda _: "", call_native=native,
        )
        assert count == MAX_TOOL_CALLS
    finally:
        ToolRegistry.unregister(TOOL)


async def test_text_fallback_dedup() -> None:
    count = 0
    rounds = 0

    async def counter(value: str) -> str:
        nonlocal count
        count += 1
        return value

    async def llm(work):
        nonlocal rounds
        rounds += 1
        if rounds <= 2:
            return f'```tool\n{{"tool":"{TOOL}","args":{{"value":"same"}}}}\n```'
        return "完成"

    ToolRegistry.register_func(TOOL, "test", counter)
    try:
        out = await run_tool_loop([{"role": "user", "content": "执行"}], llm)
        assert out == "完成" and count == 1
    finally:
        ToolRegistry.unregister(TOOL)


async def main() -> None:
    await test_native_pairing_and_dedup()
    await test_cancel_and_call_limit()
    await test_text_fallback_dedup()
    print("[OK] P3-02A 工具配对、去重、取消与调用上限")


if __name__ == "__main__":
    asyncio.run(main())
