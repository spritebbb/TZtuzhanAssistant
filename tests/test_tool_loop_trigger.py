# -*- coding: utf-8 -*-
"""工具循环触发词回归：用户要求调用 Codex/DSH/插件能力时必须命中工具循环。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.pipeline import _needs_tool_loop

# 必须命中（走工具循环，模型才拿得到工具 schema）
MUST_HIT = [
    "调 external 看看它暴露了哪些能力",
    "确认下它能不能接 Codex",
    "用 Codex 帮我写个脚本",
    "让 Codex 执行一个任务",
    "调一下 DSH",
    "你根本没有调动工具！",
    "看看这个插件有什么接口",
    "测试一下外部桥接",
    "查一下汇率",
]

# 不应命中（纯聊天，保持打字机流式体验）
MUST_MISS = [
    "你好啊菟菚",
    "今天心情怎么样",
    "讲个地狱笑话听听",
    "晚安，明天见",
]

fails = []
for t in MUST_HIT:
    if not _needs_tool_loop(t, None):
        fails.append(f"应命中未命中: {t}")
for t in MUST_MISS:
    if _needs_tool_loop(t, None):
        fails.append(f"不应命中却命中: {t}")

assert not fails, "触发词回归失败:\n" + "\n".join(fails)
print(f"[OK] 工具循环触发: {len(MUST_HIT)} 条全部命中, {len(MUST_MISS)} 条纯聊天不受影响")
print("\n=== 触发词回归: 全部通过 ===")
