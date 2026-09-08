# -*- coding: utf-8 -*-
"""能力演示：技能触发、脚本完整性、以及「每步真能跑起来」的联动校验。

验收锚点：
- 说「新手教程/演示/带我看看」能命中 agent-tour 技能，且该技能点名了要用的工具
  （技能点名工具 → 工具通道会打开）；
- 脚本每步都有 id/标题/建议原话/工具/验收点，顺序符合「日常 → Agent 主秀」；
- 关键步骤的提示词确实能触发对应能力：
  · 并行子代理步 → 命中 parallel-analysis 技能（agent_fanout）；
  · 浏览器步 → MCP 按需注入暴露 playwright 工具；
  · 查文档步 → 暴露 context7 工具。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_tour_"))

from backend.core.demo_tour import TOUR_STEPS, get_tour
from backend.skills import load_catalog, match_skills, skills_reference_tools


def test_skill_triggers_and_names_tools() -> int:
    catalog = load_catalog()
    names = [s.name for s in catalog]
    assert "能力演示" in names, names
    for text in ("来个新手教程", "演示一下你的能力", "带我看看你会什么", "tour 一下"):
        hits = match_skills(text, catalog)
        assert any(s.name == "能力演示" for s in hits), text
    # 技能正文点名了工具 → 工具通道会被打开
    skill = next(s for s in catalog if s.name == "能力演示")
    referenced = skills_reference_tools([skill], [
        "agent_fanout", "agent_run", "web_search", "write_file", "read_file",
        "memory_add", "todo_create", "browser_navigate", "query-docs",
    ])
    for expected in ("agent_fanout", "web_search", "write_file", "memory_add", "todo_create"):
        assert expected in referenced, (expected, referenced)
    print("[OK] 教程技能可被触发，且点名了所需工具")
    return 0


def test_steps_shape_and_order() -> int:
    tour = get_tour()
    steps = tour["steps"]
    assert len(steps) >= 6, len(steps)
    for step in steps:
        for key in ("id", "title", "shows", "prompt", "tools", "check"):
            assert step.get(key), (step.get("id"), key)
        assert isinstance(step["tools"], list) and step["tools"]
    ids = [s["id"] for s in steps]
    # Agent 主秀（并行子代理 / 浏览器）必须在后半段
    assert ids.index("fanout") >= len(ids) // 2, ids
    assert ids.index("browser") > ids.index("fanout"), ids
    print("[OK] 脚本结构完整、主秀步骤靠后")
    return 0


def test_key_prompts_trigger_capability() -> int:
    steps = {s["id"]: s for s in TOUR_STEPS}
    catalog = load_catalog()

    # 并行子代理步：命中 parallel-analysis（该技能指令使用 agent_fanout）
    fanout_prompt = steps["fanout"]["prompt"]
    hits = [s.name for s in match_skills(fanout_prompt, catalog)]
    assert "并行分析" in hits, hits

    # 浏览器步：MCP 按需注入应暴露 playwright 工具
    from backend.core.config import config
    from backend.tools import mcp_server

    config.agent_mcp_always_on = False
    mcp_server._EXTERNAL_SERVERS["playwright"] = {
        "name": "playwright", "url": "http://127.0.0.1:1/mcp", "tools": 1,
        "keywords": ["浏览器"],
    }
    from backend.tools.base import ToolRegistry

    ToolRegistry.register_func(
        name="playwright::browser_navigate", description="x",
        func=_noop, category="external", needs_confirm=True, owner="mcp:playwright",
    )
    try:
        f = mcp_server.mcp_tool_filter(steps["browser"]["prompt"], [])
        assert f is not None and f(ToolRegistry.get("playwright::browser_navigate")) is True
        # 查文档步：context7 关键词命中
        mcp_server._EXTERNAL_SERVERS["context7"] = {
            "name": "context7", "url": "https://mcp.context7.com/mcp", "tools": 1,
            "keywords": ["文档"],
        }
        ToolRegistry.register_func(
            name="context7::query-docs", description="x",
            func=_noop, category="external", needs_confirm=True, owner="mcp:context7",
        )
        f2 = mcp_server.mcp_tool_filter(steps["docs"]["prompt"], [])
        assert f2(ToolRegistry.get("context7::query-docs")) is True
        # 日常闲聊不该暴露外部工具
        f3 = mcp_server.mcp_tool_filter("今天心情不错", [])
        assert f3(ToolRegistry.get("playwright::browser_navigate")) is False
    finally:
        for name in ("playwright::browser_navigate", "context7::query-docs"):
            ToolRegistry.unregister(name)
        mcp_server._EXTERNAL_SERVERS.clear()
    print("[OK] 关键步骤提示词能触发对应能力（含 MCP 按需注入）")
    return 0


async def _noop(**kwargs):
    return "ok"


def main() -> int:
    failed = (
        test_skill_triggers_and_names_tools()
        + test_steps_shape_and_order()
        + test_key_prompts_trigger_capability()
    )
    if failed:
        print(f"\n=== 能力演示：{failed} 项失败 ===")
        return 1
    print("\n=== 能力演示：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
