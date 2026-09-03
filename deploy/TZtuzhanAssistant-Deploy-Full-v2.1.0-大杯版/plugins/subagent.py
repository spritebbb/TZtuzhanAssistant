# -*- coding: utf-8 -*-
"""工具插件：子代理编排（对标 Harness 的 subagent + parallel fan-out）。

- agent_run：派发一个独立子任务给 LLM，返回其结果
- agent_fanout：并行派发多个独立子任务，收集所有结果

子代理是"独立上下文"的执行者：它看不到主对话，只拿到任务描述和可选背景。
适合：把大任务拆成小块并行处理、独立研究、独立审查。
"""
from __future__ import annotations

PLUGIN_META = {
    "name": "子代理编排",
    "version": "1.0.0",
    "description": "agent_run / agent_fanout：派发独立子任务并收集结果",
    "author": "tuzhan",
}

import asyncio
import json

from backend.tools.base import ToolRegistry, tool_failure
from backend.core import llm

# 并行子代理上限：防止极端输入一次打爆 API 限流/资源
_FANOUT_MAX = 5

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
    messages: list[dict] = [{"role": "system", "content": _SUBAGENT_SYSTEM}]
    if background:
        messages.append({
            "role": "user",
            "content": f"[任务背景信息]\n{background}\n\n[任务]\n{prompt}",
        })
    else:
        messages.append({"role": "user", "content": prompt})
    try:
        result = await llm.chat(messages, temperature=0.3, max_tokens=2048)
        return result or "（子代理返回空结果）"
    except Exception as e:
        return tool_failure(f"（子代理执行失败：{type(e).__name__}: {e}）")


async def _agent_fanout(tasks_json: str = "") -> str:
    """并行执行多个独立子任务（JSON 数组），收集全部结果。

    tasks_json 示例：
    [{"id": "task1", "prompt": "调查 A 方案"}, {"id": "task2", "prompt": "调查 B 方案", "background": "可选背景"}]
    """
    if not tasks_json:
        return tool_failure("（缺少任务列表 JSON）")
    try:
        tasks = json.loads(tasks_json)
        if not isinstance(tasks, list) or not tasks:
            return tool_failure("（任务列表必须是非空 JSON 数组）")
        if len(tasks) > _FANOUT_MAX:
            return tool_failure(f"（任务数量超过上限 {_FANOUT_MAX}，请分批执行）")
    except json.JSONDecodeError as e:
        return tool_failure(f"（JSON 解析失败：{e}）")

    async def _run_one(item: object) -> tuple[str, str]:
        if not isinstance(item, dict):
            return "?", "（任务项格式错误：必须是对象）"
        tid = str(item.get("id", "?"))
        prompt = str(item.get("prompt", ""))
        background = str(item.get("background", ""))
        if not prompt:
            return tid, "（缺少 prompt）"
        return tid, await _agent_run(prompt, background)

    # 并发执行所有子任务；单个失败不丢掉其余结果
    results = await asyncio.gather(*[_run_one(t) for t in tasks], return_exceptions=True)
    lines = []
    for t, r in zip(tasks, results):
        if isinstance(r, BaseException):
            tid = str(t.get("id", "?")) if isinstance(t, dict) else "?"
            lines.append(f"▶ 子任务 [{tid}]\n（子任务异常：{type(r).__name__}: {r}）")
        else:
            tid, out = r
            lines.append(f"▶ 子任务 [{tid}]\n{out}")
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
