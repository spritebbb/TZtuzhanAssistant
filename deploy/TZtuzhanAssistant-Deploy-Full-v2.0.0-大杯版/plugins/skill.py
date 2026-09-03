# -*- coding: utf-8 -*-
"""工具插件：技能加载（对标 Harness 的 skill 工具）。

- skill_search：按关键词检索技能目录
- skill_load：按名称加载技能全文
"""
from __future__ import annotations

PLUGIN_META = {
    "name": "技能系统",
    "version": "1.0.0",
    "description": "skill_search / skill_load：检索与加载 skills/ 技能目录",
    "author": "tuzhan",
}

from backend.tools.base import ToolRegistry
from backend.skills import load_catalog, load_skill_file
from backend.skills.catalog import SKILLS_DIR


def _skill_search(query: str = "") -> str:
    """按关键词检索可用技能，返回名称和描述。"""
    skills = load_catalog()
    if not skills:
        return "（暂无可用技能）"
    lines: list[str] = []
    if query:
        ql = query.lower()
        matched = [s for s in skills if ql in s.name.lower() or ql in s.description.lower() or any(ql in t.lower() for t in s.triggers)]
    else:
        matched = skills
    for s in matched:
        tags = ", ".join(s.triggers[:3]) if s.triggers else ""
        lines.append(f"- **{s.name}**：{s.description}" + (f"（触发词：{tags}）" if tags else ""))
    if not lines:
        # 无匹配时返回全部技能，避免 LLM 误判"技能库为空"
        for s in skills:
            tags = ", ".join(s.triggers[:3]) if s.triggers else ""
            lines.append(f"- **{s.name}**：{s.description}" + (f"（触发词：{tags}）" if tags else ""))
        if not lines:
            return "（暂无可用技能）"
        return "（未找到与查询匹配的技能，以下是全部可用技能）\n" + "\n".join(lines)
    return "\n".join(lines)


def _skill_load(name: str = "") -> str:
    """按名称加载技能全文。"""
    if not name:
        return "（缺少技能名称）"
    qn = name.strip().lower()
    skills = load_catalog()
    for s in skills:
        if s.name.lower() == qn:
            return f"# {s.name}\n\n{s.description}\n\n---\n\n{s.content}"
    return f"（未找到技能：{name}）"


def register(ctx=None) -> None:
    ToolRegistry.register_func(
        name="skill_search",
        description="按关键词检索可用技能，返回技能名称和描述。技能是预制的专家指令模板，可以指导你更高效地完成特定类型任务",
        func=_skill_search,
        is_async=False,
        owner="skill",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词，不传则返回全部技能"}
            },
        },
    )
    ToolRegistry.register_func(
        name="skill_load",
        description="按名称加载技能全文，注入到当前对话上下文作为执行指导",
        func=_skill_load,
        is_async=False,
        owner="skill",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "技能名称（精确匹配）"}
            },
            "required": ["name"],
        },
    )