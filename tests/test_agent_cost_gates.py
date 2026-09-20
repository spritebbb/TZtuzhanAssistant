# -*- coding: utf-8 -*-
"""成本闸门（§24.3 第 1 项）回归：子代理并发上限 / 单任务 token 预算 / 嵌套深度。

覆盖五件事：
1. llm 任务级计量——_record_usage 累计、reset 清零、预算判定；
2. agent_fanout 任务数上限（config 外置）与预算用尽拒绝；
3. 全局并发信号量——同时在飞的子代理 LLM 调用不超过 AGENT_SUBAGENT_CONCURRENCY；
4. 子代理用量增量回流父任务计量（gather 子任务上下文不自动回流）；
5. 嵌套深度闸（防未来工具化子代理递归）与工具循环回合间预算熔断。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 数据目录隔离：先于任何 backend import 生效，避免读写真实加密库
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_costgate_"))

from backend.core import llm  # noqa: E402
from backend.core.config import config  # noqa: E402
from backend.tools.base import ToolRegistry  # noqa: E402
from backend.tools.tool_loop import _run_native  # noqa: E402
from plugins import subagent  # noqa: E402


def test_usage_meter_accumulates_and_resets() -> None:
    llm.reset_task_usage(budget=100)
    usage = SimpleNamespace(prompt_tokens=40, completion_tokens=10)
    llm._record_usage("chat", "test-model", usage, "prompt", "completion")
    assert llm.task_tokens() == 50, f"计量应为 50，实际 {llm.task_tokens()}"
    assert not llm.task_budget_exceeded(), "50 < 100 不应超预算"
    llm._record_usage("chat", "test-model", usage, "prompt", "completion")
    assert llm.task_tokens() == 100 and llm.task_budget_exceeded(), "100 >= 100 应超预算"
    llm.reset_task_usage()
    assert llm.task_tokens() == 0 and not llm.task_budget_exceeded()
    print("[OK] 任务级 token 计量：累计/清零/预算判定")


def test_fanout_task_cap_and_budget_refusal() -> None:
    old_max = config.agent_fanout_max
    config.agent_fanout_max = 2
    try:
        tasks = [{"id": f"t{i}", "prompt": f"任务{i}"} for i in range(3)]
        out = asyncio.run(subagent._agent_fanout(__import__("json").dumps(tasks, ensure_ascii=False)))
        assert "上限" in out and "t0" not in out.split("上限")[0], f"超上限应整批拒绝：{out[:120]}"
    finally:
        config.agent_fanout_max = old_max

    llm.reset_task_usage(budget=100)
    llm.add_task_tokens(150)
    two = __import__("json").dumps([{"id": "a", "prompt": "x"}, {"id": "b", "prompt": "y"}])
    out = asyncio.run(subagent._agent_fanout(two))
    assert "预算" in out, f"预算用尽时 fanout 应拒绝：{out[:120]}"
    out = asyncio.run(subagent._agent_run("任务"))
    assert "预算" in out, f"预算用尽时 agent_run 应拒绝：{out[:120]}"
    llm.reset_task_usage()
    print("[OK] fanout 任务数上限 + 预算用尽拒绝（run 与 fanout 双入口）")


def test_global_concurrency_and_usage_backflow() -> None:
    old_conc = config.agent_subagent_concurrency
    config.agent_subagent_concurrency = 2
    state = {"cur": 0, "peak": 0}

    async def fake_chat(messages, **kwargs):
        state["cur"] += 1
        state["peak"] = max(state["peak"], state["cur"])
        await asyncio.sleep(0.03)
        llm.add_task_tokens(10)  # 模拟子任务上下文里的用量增量
        state["cur"] -= 1
        return "ok"

    tasks = [{"id": f"t{i}", "prompt": f"任务{i}"} for i in range(5)]
    payload = __import__("json").dumps(tasks, ensure_ascii=False)

    async def scenario() -> tuple[str, int]:
        # 计量与断言必须在同一个 asyncio 任务里：ContextVar 的写入不会跨任务回流
        llm.reset_task_usage(budget=None)
        with patch.object(llm, "chat", new=fake_chat):
            out = await subagent._agent_fanout(payload)
        return out, llm.task_tokens()

    try:
        out, tokens = asyncio.run(scenario())
    finally:
        config.agent_subagent_concurrency = old_conc
    assert out.count("▶ 子任务") == 5, "五个子任务结果都应返回"
    assert state["peak"] <= 2, f"并发峰值 {state['peak']} 超过上限 2"
    assert tokens == 50, f"子代理用量应回流父计量（5×10=50），实际 {tokens}"
    llm.reset_task_usage()
    print(f"[OK] 全局并发信号量（峰值 {state['peak']} ≤ 2）+ 用量回流父计量")


def test_depth_guard() -> None:
    max_depth = config.agent_subagent_max_depth
    token = subagent._subagent_depth.set(max_depth)
    try:
        out = asyncio.run(subagent._agent_run("嵌套任务"))
        assert "嵌套深度超限" in out, f"深度达上限应拒绝：{out[:120]}"
    finally:
        subagent._subagent_depth.reset(token)
    # 正常深度（0）不受影响
    async def fake_chat(messages, **kwargs):
        return "ok"

    with patch.object(llm, "chat", new=fake_chat):
        out = asyncio.run(subagent._agent_run("正常任务"))
    assert "嵌套深度超限" not in out
    print("[OK] 嵌套深度闸：达上限拒绝、正常深度放行")


def test_tool_loop_budget_breaks_between_rounds() -> None:
    async def probe(x: int = 0) -> str:
        return "probe-ok"

    ToolRegistry.register_func(
        name="cost_gate_probe",
        description="成本闸门回归探针（仅本测试使用）",
        func=probe,
        owner="test",
        input_schema={"type": "object", "properties": {"x": {"type": "integer"}}},
        category="builtin",
        needs_confirm=False,
    )
    calls = {"native": 0}

    async def call_native(work, tools):
        calls["native"] += 1
        if calls["native"] == 1:
            llm.add_task_tokens(50)  # 第一轮结束后即超预算
            return "先调工具看看", [{"name": "cost_gate_probe", "arguments": {"x": 1}}]
        return "不应到达这里", []

    llm.reset_task_usage(budget=10)
    work = [{"role": "user", "content": "测试任务"}]
    out = asyncio.run(_run_native(work, call_native, [], max_loops=8, final_instruction=None))
    llm.reset_task_usage()
    assert "预算已用尽" in out, f"第二轮前应熔断：{out[:160]}"
    assert calls["native"] == 1, f"call_native 应只被调用 1 次，实际 {calls['native']}"
    print("[OK] 工具循环回合间预算熔断：第二轮不再发起 LLM 调用")


def main() -> None:
    test_usage_meter_accumulates_and_resets()
    test_fanout_task_cap_and_budget_refusal()
    test_global_concurrency_and_usage_backflow()
    test_depth_guard()
    test_tool_loop_budget_breaks_between_rounds()
    print("\n=== 成本闸门（§24.3-1）：5 组全部通过 ===")


if __name__ == "__main__":
    main()
