# -*- coding: utf-8 -*-
"""P4 AgentSession 长任务验证：计划生成（mock）、持久化、执行上下文组装。"""
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agent import session as agent_session


def _mock_plan() -> None:
    """mock 掉 LLM 计划生成，返回固定计划。"""
    async def fake_chat(messages, **kw):
        return '[{"title": "打开记事本", "detail": "用 open_app 打开 notepad"},' \
               ' {"title": "截图", "detail": "用 screenshot 截图"}]'
    agent_session.chat = fake_chat


async def test_create_task_with_plan() -> None:
    _mock_plan()
    uid = f"test-user-{uuid.uuid4().hex[:6]}"
    task = await agent_session.create_task(uid, "帮我打开记事本写点东西")
    assert task.id
    assert len(task.plan) >= 2, f"计划应为多步: {[s.title for s in task.plan]}"
    assert task.status == "planned"
    # 重新读取（持久化）
    loaded = agent_session._load(task.id)
    assert loaded is not None and loaded.objective == "帮我打开记事本写点东西"
    print(f"[OK] 创建任务：{task.id}，计划 {len(task.plan)} 步: {[s.title for s in task.plan]}")
    # 清理测试数据
    agent_session._connect().execute("DELETE FROM agent_tasks WHERE user_id=?", (uid,)).connection.commit()


async def test_list_tasks() -> None:
    _mock_plan()
    uid = f"test-list-{uuid.uuid4().hex[:6]}"
    t1 = await agent_session.create_task(uid, "任务A")
    await agent_session.create_task(uid, "任务B")
    tasks = agent_session.list_tasks(uid)
    assert len(tasks) >= 2
    print(f"[OK] 任务列表：{len(tasks)} 条")
    agent_session._connect().execute("DELETE FROM agent_tasks WHERE user_id=?", (uid,)).connection.commit()


async def test_run_task_context() -> None:
    """验证 run_task 能组装上下文并走完（mock LLM 返回工具调用→结束）。"""
    from backend.tools.base import ToolRegistry
    from tests._helpers import load_all_tools
    load_all_tools()

    _mock_plan()
    uid = f"test-run-{uuid.uuid4().hex[:6]}"

    # mock 原生函数调用：第一轮调用 system_info（只读无需确认），第二轮结束
    calls = {"n": 0}

    async def fake_native(messages, tools=None, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return "", [{"name": "system_info", "arguments": {}}]
        return "任务完成：已获取系统信息。", []

    agent_session.chat_native = fake_native
    # 覆盖 run_tool_round 内部使用的工具循环 LLM 回调
    from backend.tools import tool_loop
    orig_call = tool_loop.run_tool_loop

    async def fake_loop(messages, call_llm, *, max_loops=2, mock=False,
                        final_instruction=None, call_native=None):
        # 直接调用 fake_native 模拟一轮工具调用 + 一轮结束
        text, tcs = await fake_native(messages, None)
        return text or "任务完成。"

    tool_loop.run_tool_loop = fake_loop
    try:
        task = await agent_session.create_task(uid, "查一下系统信息")
        # 新语义（步骤确认门禁）：全部步骤 pending 时不得执行
        blocked = await agent_session.run_task(task.id)
        assert blocked.status == "planned", f"全 pending 应保持 planned: {blocked.status}"
        assert "等待步骤确认" in (blocked.result or ""), blocked.result
        # 确认全部步骤后放行执行
        agent_session.confirm_all(task.id, True)
        done = await agent_session.run_task(task.id)
        assert done.status == "done", f"状态应 done: {done.status}"
        assert done.result, "应有结果文本"
        print(f"[OK] 步骤门禁 + 任务执行：status={done.status}, result={done.result[:40]}")
    finally:
        tool_loop.run_tool_loop = orig_call
        agent_session._connect().execute("DELETE FROM agent_tasks WHERE user_id=?", (uid,)).connection.commit()


async def main() -> None:
    await test_create_task_with_plan()
    await test_list_tasks()
    await test_run_task_context()
    print("\n=== P4 AgentSession: 3 项全部通过 ===")


asyncio.run(main())
