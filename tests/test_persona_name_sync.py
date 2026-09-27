# -*- coding: utf-8 -*-
"""人格名同步回归：切换人格后，旁路提示词/识别正则/生图锚点必须跟随当前人格。

此前多处以「菟菚」硬编码在 LLM 提示词、发言标签剥离正则、生图外观锚点里；
导入一张非菟菚人格卡并激活后，这些功能仍以菟菚的口吻/外貌输出。

运行：python -m tests.test_persona_name_sync
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_name_sync_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import persona_profiles  # noqa: E402

_ALT_CARD = """---
name: 橘九
subtitle: 测试用猫娘程序员
theme: dark
voice: zh-CN-XiaoxiaoNeural
---

# 角色卡：橘九

> 固定人格，好感度阶段、称呼由系统注入。

## 身份

你是「橘九」，猫娘程序员。
"""


def _switch_to_alt() -> None:
    profile = persona_profiles.import_card("alt.md", _ALT_CARD.encode("utf-8"))
    persona_profiles.activate(profile["id"])


def _switch_back_to_default() -> None:
    persona_profiles.activate(persona_profiles.DEFAULT_PERSONA_ID)


def test_dual_perspectives_draft_prompt() -> None:
    from backend.core.dual_perspectives import _DRAFT_PROMPT

    _switch_to_alt()
    try:
        # 模板常量本身不动，调用方 replace 后必须出现新人格名
        assert "菟菚" in _DRAFT_PROMPT
        swapped = _DRAFT_PROMPT.replace("菟菚", persona_profiles.active_name())
        assert "你是「橘九」" in swapped and "菟菚" not in swapped
    finally:
        _switch_back_to_default()
    print("[OK] dual_perspectives 草稿提示词可按人格替换")


def test_viewpoint_and_letter_prompts_replaceable() -> None:
    from backend.core.activities import _VIEWPOINT_DRAFT_PROMPT
    from backend.core.sealing import _LETTER_PROMPT

    _switch_to_alt()
    try:
        name = persona_profiles.active_name()
        for template in (_VIEWPOINT_DRAFT_PROMPT, _LETTER_PROMPT):
            swapped = template.replace("菟菚", name)
            assert "橘九" in swapped and "菟菚" not in swapped
    finally:
        _switch_back_to_default()
    print("[OK] activities/sealing 提示词可按人格替换")


def test_dispatch_pattern_follows_persona() -> None:
    from backend.agent.session import _dispatch_pattern_cache, detect_dispatch_request

    assert detect_dispatch_request("给菟菚派任务:整理周报") == "整理周报"
    _switch_to_alt()
    try:
        # 新人格名可识别，旧名不再匹配「给<名>」变体
        assert detect_dispatch_request("给橘九派任务:整理周报") == "整理周报"
        assert detect_dispatch_request("派个任务:整理周报") == "整理周报"  # 无名字变体仍可用
        assert _dispatch_pattern_cache and "橘九" in _dispatch_pattern_cache
    finally:
        _switch_back_to_default()
    assert detect_dispatch_request("给菟菚派任务:整理周报") == "整理周报"
    print("[OK] 派单正则跟随激活人格（含缓存切换）")


def test_tavern_clean_reply_and_identity() -> None:
    from backend.core.tavern import _clean_reply

    assert _clean_reply("菟菚：你好呀") == "你好呀"
    assert _clean_reply("【菟菚】你好") == "你好"
    assert _clean_reply("橘九：喵，上线了", "橘九") == "喵，上线了"
    assert _clean_reply("【橘九】喵", "橘九") == "喵"
    assert _clean_reply("没有标签的话", "橘九") == "没有标签的话"
    print("[OK] tavern 发言标签剥离接受动态人格名")


def test_image_prompts_diverge_by_persona() -> None:
    from backend.core.proactive_media import proactive_image_prompt
    from backend.core.stickers import StickerScene, build_sticker_prompt

    scene = StickerScene("happy", "开心", "今天心情很好的贴纸", "开心地比耶")
    # 默认人格：菟菚专属外观锚点
    assert "绿色长发" in proactive_image_prompt("selfie")
    assert "菟丝子研究所" in proactive_image_prompt("selfie")
    assert "绿色长发" in build_sticker_prompt(scene)

    _switch_to_alt()
    try:
        alt = proactive_image_prompt("selfie")
        assert "橘九" in alt
        assert "绿色长发" not in alt and "菟丝子研究所" not in alt
        assert "研究所涂鸦" not in proactive_image_prompt("doodle")
        alt_sticker = build_sticker_prompt(scene)
        assert "橘九" in alt_sticker and "绿色长发" not in alt_sticker
    finally:
        _switch_back_to_default()
    print("[OK] 生图/贴纸锚点按人格分流")


def test_her_profile_gated() -> None:
    from backend.core.her_profile import her_profile

    assert len(her_profile()) == 5  # 默认人格：完整侧写
    _switch_to_alt()
    try:
        assert her_profile() == []  # 非默认人格：不把菟菚侧写安到别人头上
    finally:
        _switch_back_to_default()
    print("[OK] her_profile 仅默认人格返回侧写")


def test_canon_life_gated() -> None:
    """菟菚的正典生活（离线补算/生活模板池/状态行）只对默认人格成立。"""
    import asyncio

    from backend.core.life_templates import load_templates
    from backend.core.offline_recap import maybe_generate

    # 默认人格：离线补算会走到窗口判断（功能开关开着时返回 None 或 pending，不抛错）
    default_pool = load_templates()
    assert any(t.output_kind != "rest" for t in default_pool)  # 内置池完整

    _switch_to_alt()
    try:
        # 离线补算：非默认人格直接跳过（行程是菟菚的正典）
        assert asyncio.run(maybe_generate(persona_profiles.active_user_id())) is None
        # 生活模板池：无定制文件时空池（只剩中性 rest 兜底），不回退菟菚内置池
        alt_pool = load_templates()
        assert all(t.output_kind == "rest" for t in alt_pool)
        assert len(alt_pool) == 1
    finally:
        _switch_back_to_default()
    print("[OK] 正典生活（离线补算/模板池）仅默认人格")


def main() -> None:
    test_dual_perspectives_draft_prompt()
    test_viewpoint_and_letter_prompts_replaceable()
    test_dispatch_pattern_follows_persona()
    test_tavern_clean_reply_and_identity()
    test_image_prompts_diverge_by_persona()
    test_her_profile_gated()
    test_canon_life_gated()
    print("\n=== 人格名同步回归：全部通过 ===")


if __name__ == "__main__":
    main()
