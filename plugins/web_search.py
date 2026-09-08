# -*- coding: utf-8 -*-
"""工具插件：联网搜索。"""
from __future__ import annotations

PLUGIN_META = {
    "name": "联网搜索",
    "version": "1.0.0",
    "description": "web_search 工具：搜索实时信息（新闻、天气、价格等）",
    "author": "tuzhan",
}

from backend.tools.base import ToolRegistry, tool_failure
from backend.core.source_verification import verify_search


async def _web_search(query: str = "") -> str:
    """联网搜索。"""
    if not query:
        return tool_failure("（搜索缺少关键词）")
    import asyncio

    report = await asyncio.to_thread(verify_search, query)
    hits = report.get("evidence", [])
    if not hits:
        # 区分「真没结果」和「搜索引擎故障」：此前故障被伪装成无结果，
        # 模型会基于训练知识自信作答（如声称刚发布的产品不存在）。
        reason = str(report.get("reason", ""))
        if report.get("status") == "failed":
            return f"（搜索服务暂时不可用，未能联网核实：{reason}。请明确告诉用户搜索失败、信息可能过时。）"
        return "（没有搜到相关内容）"
    labels = {
        "supported": "已取得至少两个独立站点来源",
        "insufficient": "证据不足，未达到两个独立站点",
        "conflict": "来源仍有冲突，请并列说明",
    }
    lines = [f"求证状态：{labels.get(report.get('status'), report.get('status', 'failed'))}"]
    if report.get("agreement") == "not_comparable":
        lines.append("这些来源不是可直接对齐的同一指标，不要宣称数值一致。")
    for h in hits[:3]:
        lines.append(f"- [{h.get('id')}] {h.get('title', '')}：{h.get('snippet', '')} {h.get('url', '')}")
    return "\n".join(lines)


def register(ctx=None) -> None:
    ToolRegistry.register_func(
        name="web_search",
        description="联网搜索实时信息（新闻、天气、价格等）",
        func=_web_search,
        owner="web_search",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"}
            },
            "required": ["query"],
        },
    )
