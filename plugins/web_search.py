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
from backend.core.search import web_search


async def _web_search(query: str = "") -> str:
    """联网搜索。"""
    if not query:
        return tool_failure("（搜索缺少关键词）")
    import asyncio

    hits = await asyncio.to_thread(web_search, query)
    if not hits:
        return "（没有搜到相关内容）"
    lines = []
    for h in hits[:5]:
        lines.append(f"- {h.get('title', '')}：{h.get('snippet', '')}")
    return "搜索结果：\n" + "\n".join(lines)


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
