# -*- coding: utf-8 -*-
"""工具插件：子代理编排（对标 Harness 的 subagent + parallel fan-out）。

- agent_run：派发一个独立子任务给 LLM，返回其结果
- agent_fanout：并行派发多个独立子任务，收集所有结果

子代理是"独立上下文"的执行者：它看不到主对话，只拿到任务描述和可选背景。
适合：把大任务拆成小块并行处理、独立研究、独立审查。

成本闸门（§24.3 第 1 项，2026-09-20）：全局并发信号量 + 嵌套深度闸 +
任务 token 预算预检；子代理用量增量显式回流父任务计量。
"""
from __future__ import annotations

import asyncio
import contextvars
import json

from backend.tools.base import ToolRegistry, tool_failure
from backend.core import llm
from backend.core.config import config

# ---- 成本闸门 ----
# 全局并发信号量：跨任务/跨轮次共享，限制同时在飞的子代理 LLM 调用数。
# 配置变化时重建（热重载支持）；重建瞬间旧持有者不受影响，可能短暂超限一格，
# 下一轮即收敛——可接受，不做跨代等待队列。
_semaphore: asyncio.Semaphore | None = None
_semaphore_limit: int = -1
# 嵌套深度：当前子代理默认不带工具，此闸防未来工具化子代理递归派发失控
_subagent_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "tztuzhan_subagent_depth", default=0
)


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore, _semaphore_limit
    limit = max(1, config.agent_subagent_concurrency)
    if _semaphore is None or limit != _semaphore_limit:
        _semaphore = asyncio.Semaphore(limit)
        _semaphore_limit = limit
    return _semaphore


# 子代理系统提示词：中性执行者，不带菟菚人格（避免把拟人人格混入分析任务）
_SUBAGENT_SYSTEM = (
    "你是一个任务执行子代理。你的任务是独立完成用户交给你的子任务，"
    "并输出完整、准确、可用的结果。只输出任务要求的内容本身，不要寒暄，"
    "不要添加与任务无关的说明。"
)


async def _agent_run(prompt: str = "", background: str = "") -> str:
    """派发一个独立子任务，返回子代理的完整结果。"""
    if not prompt:
        return tool_failure("（缺少任务描述）")
    depth = _subagent_depth.get()
    if depth + 1 > max(1, config.agent_subagent_max_depth):
        return tool_failure(
            f"（嵌套深度超限：子代理最多 {config.agent_subagent_max_depth} 层，"
            "不能再从子代理里派发子代理；请直接给出结果）"
        )
    if llm.task_budget_exceeded():
        return tool_failure(
            f"（任务 token 预算已用尽（AGENT_TASK_TOKEN_BUDGET="
            f"{config.agent_task_token_budget}），本子代理未执行；请基于已有结果总结收尾）"
        )
    messages: list[dict] = [{"role": "system", "content": _SUBAGENT_SYSTEM}]
    if background:
        messages.append({
            "role": "user",
            "content": f"[任务背景信息]\n{background}\n\n[任务]\n{prompt}",
        })
    else:
        messages.append({"role": "user", "content": prompt})
    depth_token = _subagent_depth.set(depth + 1)
    try:
        async with _get_semaphore():
            result = await llm.chat(messages, temperature=0.3, max_tokens=2048)
        return result or "（子代理返回空结果）"
    except Exception as e:
        return tool_failure(f"（子代理执行失败：{type(e).__name__}: {e}）")
    finally:
        _subagent_depth.reset(depth_token)


async def _agent_fanout(tasks_json: str = "") -> str:
    """并行执行多个独立子任务（JSON 数组），收集全部结果。

    tasks_json 示例：
    [{"id": "task1", "prompt": "调查 A 方案"}, {"id": "task2", "prompt": "调查 B 方案", "background": "可选背景"}]
    """
    if not tasks_json:
        return tool_failure("（缺少任务列表 JSON）")
    fanout_max = max(1, config.agent_fanout_max)
    try:
        tasks = json.loads(tasks_json)
        if not isinstance(tasks, list) or not tasks:
            return tool_failure("（任务列表必须是非空 JSON 数组）")
        if len(tasks) > fanout_max:
            return tool_failure(f"（任务数量超过上限 {fanout_max}，请分批执行）")
    except json.JSONDecodeError as e:
        return tool_failure(f"（JSON 解析失败：{e}）")
    if llm.task_budget_exceeded():
        return tool_failure(
            f"（任务 token 预算已用尽（AGENT_TASK_TOKEN_BUDGET="
            f"{config.agent_task_token_budget}），本轮 fanout 未执行；请基于已有结果总结收尾）"
        )

    async def _run_one(item: object) -> tuple[str, str, int]:
        if not isinstance(item, dict):
            return "?", "（任务项格式错误：必须是对象）", 0
        tid = str(item.get("id", "?"))
        prompt = str(item.get("prompt", ""))
        background = str(item.get("background", ""))
        if not prompt:
            return tid, "（缺少 prompt）", 0
        # gather 创建的子任务会复制上下文：子代理的 token 增量不会自动回流
        # 父任务计量，这里把增量带回来由外层统一入账
        start = llm.task_tokens()
        out = await _agent_run(prompt, background)
        return tid, out, llm.task_tokens() - start

    # 并发受全局信号量约束；单个失败不丢掉其余结果
    results = await asyncio.gather(*[_run_one(t) for t in tasks], return_exceptions=True)
    lines = []
    usage_delta = 0
    for t, r in zip(tasks, results):
        if isinstance(r, BaseException):
            tid = str(t.get("id", "?")) if isinstance(t, dict) else "?"
            lines.append(f"▶ 子任务 [{tid}]\n（子任务异常：{type(r).__name__}: {r}）")
        else:
            tid, out, delta = r
            usage_delta += max(0, delta)
            lines.append(f"▶ 子任务 [{tid}]\n{out}")
    llm.add_task_tokens(usage_delta)
    return "\n\n".join(lines)


def register(ctx=None) -> None:
    ToolRegistry.register_func(
        name="agent_run",
        description="派发一个独立子任务给子代理执行（类似 subagent）。子代理有独立上下文，只返回任务结果",
        func=_agent_run,
        owner="subagent",
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "子任务描述（要子代理做什么、输出什么）"},
                "background": {"type": "string", "description": "可选：任务背景信息（主对话上下文摘录等）"}
            },
            "required": ["prompt"],
        },
        category="external",
        needs_confirm=True,
    )
    ToolRegistry.register_func(
        name="agent_fanout",
        description="并行执行多个独立子任务并收集结果（类似 workflow 的 parallel）。tasks_json 为 JSON 数组",
        func=_agent_fanout,
        owner="subagent",
        input_schema={
            "type": "object",
            "properties": {
                "tasks_json": {"type": "string",
                               "description": 'JSON 数组，如 [{"id":"t1","prompt":"任务1"},{"id":"t2","prompt":"任务2","background":"背景"}]'}
            },
            "required": ["tasks_json"],
        },
        category="external",
        needs_confirm=True,
    )
