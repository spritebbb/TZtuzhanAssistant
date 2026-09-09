# -*- coding: utf-8 -*-
"""能力演示：技能触发、脚本完整性、以及「每步真能跑起来」的联动校验。

验收锚点：
- 说「新手教程/演示/带我看看」能命中 agent-tour 技能，且该技能点名了要用的工具
  （技能点名工具 → 工具通道会打开）；
- 脚本每步都有 id/分组/类型/标题/建议原话/工具/验收点，分组连续且顺序符合
  「日常 → Agent 主秀 → 长期陪伴」；
- 关键步骤的提示词确实能触发对应能力：
  · 并行子代理步 → 命中 parallel-analysis 技能（agent_fanout）；
  · 浏览器步 → MCP 按需注入暴露 playwright 工具；
  · 查文档步 → 暴露 context7 工具；
  · 生图步 → 意图路由 need_draw；
  · 跑代码 / 汇率步 → 工具循环关键词命中；
  · 多步任务步 → 派活识别 detect_dispatch_request。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_tour_"))

from backend.core.demo_tour import TOUR_GROUPS, TOUR_STEPS, get_tour
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
        "run_python", "currency_convert", "watch_add",
    ])
    for expected in (
        "agent_fanout", "web_search", "write_file", "memory_add", "todo_create",
        "run_python", "currency_convert", "watch_add",
    ):
        assert expected in referenced, (expected, referenced)
    print("[OK] 教程技能可被触发，且点名了所需工具（含新增步骤）")
    return 0


def test_steps_shape_and_groups() -> int:
    tour = get_tour()
    steps = tour["steps"]
    assert len(steps) >= 12, len(steps)

    ids = [s["id"] for s in steps]
    assert len(ids) == len(set(ids)), f"步骤 id 重复: {ids}"

    groups = tour["groups"]
    assert list(groups) == list(TOUR_GROUPS), (groups, TOUR_GROUPS)

    for step in steps:
        for key in ("id", "title", "shows", "prompt", "tools", "check", "group", "kind"):
            assert step.get(key) is not None, (step.get("id"), key)
        assert step["group"] in groups, (step["id"], step["group"])
        assert step["kind"] in ("chat", "ui"), (step["id"], step["kind"])
        assert isinstance(step["tools"], list) and step["tools"], step["id"]

    # 分组必须连续（同一分组的步骤不被打散），且出现顺序与 TOUR_GROUPS 一致
    seen: list[str] = []
    for step in steps:
        if not seen or seen[-1] != step["group"]:
            assert step["group"] not in seen, f"分组被打散: {step['group']}"
            seen.append(step["group"])
    assert seen == list(groups), seen

    # 日常在前、Agent 主秀靠后；并行子代理先于浏览器
    assert ids.index("fanout") > ids.index("files"), ids
    assert ids.index("browser") > ids.index("fanout"), ids
    print("[OK] 脚本结构完整、分组连续、主秀步骤靠后")
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


def test_new_steps_trigger_their_capability() -> int:
    steps = {s["id"]: s for s in TOUR_STEPS}

    # 生图步：意图路由必须判为 need_draw（否则走纯聊天不会真的画）
    from backend.core.intent import classify

    assert classify(steps["draw"]["prompt"])["need_draw"] is True, steps["draw"]["prompt"]

    # 跑代码 / 汇率步：必须命中工具循环关键词，否则模型拿不到工具
    from backend.core.pipeline import _needs_tool_loop

    for sid in ("code", "currency"):
        assert _needs_tool_loop(steps[sid]["prompt"], None) is True, steps[sid]["prompt"]

    # 多步任务步：必须被派活识别命中，否则只会当成一句普通聊天
    from backend.agent.session import detect_dispatch_request

    assert detect_dispatch_request(steps["report"]["prompt"]), steps["report"]["prompt"]

    # 界面型步骤：标 ui 且不要求用户「发这句话」
    for sid in ("vision", "tts", "explain", "ephemeral"):
        assert steps[sid]["kind"] == "ui", sid
    print("[OK] 新增步骤的提示词能触发对应能力（生图 / 工具循环 / 派活 / 界面操作）")
    return 0


async def _noop(**kwargs):
    return "ok"


def main() -> int:
    failed = (
        test_skill_triggers_and_names_tools()
        + test_steps_shape_and_groups()
        + test_key_prompts_trigger_capability()
        + test_new_steps_trigger_their_capability()
    )
    if failed:
        print(f"\n=== 能力演示：{failed} 项失败 ===")
        return 1
    print("\n=== 能力演示：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
