# -*- coding: utf-8 -*-
"""技能 → 工具通道回归：技能正文点名工具时，该轮必须打开工具循环。

背景：skills/parallel-analysis.md 明确写「用 agent_fanout 并行派发」，但它挂的
trigger（比较/对比/分析/评估…）全都不在工具循环触发词表里。修复前两道闸门正好
错开——命中技能的那句话没有工具通道，模型只能照着技能方法论嘴上说，子代理工具
自上线以来零调用。本用例锁住「命中技能且技能点名工具 ⇒ 工具通道打开」这条接线。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.pipeline import _tool_loop_enabled
from backend.plugins import loader
from backend.skills import Skill, load_catalog, match_skills, skills_reference_tools
from backend.tools.builtin.register_all import register_all

# 装载真实插件，让 agent_run / agent_fanout 进注册表（与 app startup 同路径）
register_all()
if loader.load_plugin(loader.PLUGINS_DIR / "subagent.py") is None:
    raise RuntimeError(f"subagent 插件加载失败: {loader.plugin_states().get('subagent')}")

SKILLS = load_catalog()

# 修复前 skill 命中但 tool_loop=False 的真实话术（子代理指令落空）
MUST_OPEN_CHANNEL = [
    "帮我比较一下 React 和 Vue",
    "分析一下这两个方案的优缺点",
    "评估一下这三种做法哪个好",
    "帮我分头查一下这几个项目的现状",
    "并行做这几件事：查A、查B、查C",
]

# 纯聊天不应因技能而打开工具循环（保住打字机流式体验）
MUST_STAY_CHAT = [
    "你好啊菟菚",
    "今天心情怎么样",
    "晚安，明天见",
]

fails: list[str] = []

for t in MUST_OPEN_CHANNEL:
    matched = match_skills(t, SKILLS)
    if not matched:
        fails.append(f"未命中任何技能: {t}")
        continue
    if not _tool_loop_enabled(t, None, matched, ephemeral=False, mock=False):
        names = [s.name for s in matched]
        fails.append(f"命中技能 {names} 但未打开工具通道: {t}")

for t in MUST_STAY_CHAT:
    matched = match_skills(t, SKILLS)
    if _tool_loop_enabled(t, None, matched, ephemeral=False, mock=False):
        fails.append(f"纯聊天不应打开工具循环: {t}")

# 工具名词边界：中文紧邻要命中，英文单词内部不能误命中
BOUNDARY = [
    (Skill(name="x", description="用 editor 改文件", content=""), "edit", []),
    (Skill(name="x", description="用edit改文件", content=""), "edit", ["edit"]),
    (Skill(name="x", description="用agent_fanout并行", content=""), "agent_fanout", ["agent_fanout"]),
    (Skill(name="x", description="", content="调用 web_search 查资料"), "web_search", ["web_search"]),
]
for skill, name, expect in BOUNDARY:
    got = skills_reference_tools([skill], [name])
    if got != expect:
        fails.append(f"词边界错误: {name} 期望 {expect} 实得 {got}")

# 临时轮 / 测试模式仍然关闭工具循环（不因技能旁路）
matched_probe = match_skills("帮我比较一下 React 和 Vue", SKILLS)
for kwargs in ({"ephemeral": True, "mock": False}, {"ephemeral": False, "mock": True}):
    if _tool_loop_enabled("帮我比较一下 React 和 Vue", None, matched_probe, **kwargs):
        fails.append(f"ephemeral/mock 下不应打开工具循环: {kwargs}")

assert not fails, "技能→工具通道回归失败:\n" + "\n".join(fails)
print(
    f"[OK] 技能→工具通道: {len(MUST_OPEN_CHANNEL)} 条命中技能后打开工具通道, "
    f"{len(MUST_STAY_CHAT)} 条纯聊天不受影响, {len(BOUNDARY)} 条词边界正确"
)
print("\n=== 技能→工具通道回归: 全部通过 ===")
