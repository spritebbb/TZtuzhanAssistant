# -*- coding: utf-8 -*-
"""对话工具服务：把"组装消息 → 调工具循环 → 拿最终文本"的职责收拢。

让 pipeline.py 只关注人格/记忆/好感度/上下文注入，工具循环细节归此处。
"""
from __future__ import annotations

from typing import Any, Callable

from ..core.log import logger
from .base import ToolRegistry


async def run_tool_round(
    messages: list[dict],
    *,
    chat: Callable[[list[dict]], str] | None = None,
    chat_native: Callable[[list[dict], list[dict] | None], tuple[str, list[dict]]] | None = None,
    mock: bool = False,
    max_loops: int = 2,
    final_instruction: list[dict] | None = None,
    on_progress: Callable[[dict], Any] | None = None,
) -> str:
    """执行工具循环，返回最终 LLM 回复文本。

    Args:
        messages: 已注入人格/记忆/上下文的消息列表（最后一条是 user）
        chat: 纯文本 LLM 回调（用于回退模式）
        chat_native: 原生 Function Calling LLM 回调
        mock: 测试模式（不真实调用 LLM）
        max_loops: 工具循环最大轮次
        final_instruction: 最终回复轮次追加的 system 消息
        on_progress: 可选的阶段进度回调，接收事件 dict（thinking/tool/tool_done）

    Returns:
        最终回复文本（不含工具代码块）
    """
    if mock or chat is None:
        # mock 模式或没有文本回调 → 直接返回第一条消息的回复
        # （mock 模式由 pipeline 中的 mock 处理，这里不重复处理）
        if chat and mock:
            return await chat(messages)
        return ""

    from .tool_loop import run_tool_loop

    # 组装工具循环回调（支持纯文本回退）
    async def _call_llm(msgs: list[dict]) -> str:
        if chat is None:
            return ""
        return await chat(msgs)

    return await run_tool_loop(
        messages,
        call_llm=_call_llm,
        call_native=chat_native,
        max_loops=max_loops,
        final_instruction=final_instruction,
        on_progress=on_progress,
    )