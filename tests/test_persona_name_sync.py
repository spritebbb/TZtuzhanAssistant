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


def test_viewpoint_and_diary_prompts_replaceable() -> None:
    from backend.core.activities import _VIEWPOINT_DRAFT_PROMPT
    from backend.core.daily import DIARY_PROMPT as _DIARY_PROMPT

    _switch_to_alt()
    try:
        name = persona_profiles.active_name()
        for template in (_VIEWPOINT_DRAFT_PROMPT, _DIARY_PROMPT):
            swapped = template.replace("菟菚", name)
            assert "橘九" in swapped and "菟菚" not in swapped
    finally:
        _switch_back_to_default()
    print("[OK] activities/daily 提示词可按人格替换")


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


def test_activate_invalidates_active_id_cache() -> None:
    """activate() 之后 active_id() 必须立刻反映新人格，不能只靠 mtime 失效。

    回归的是这个真实故障：active_id() 拿 active.json 的 mtime 当缓存失效键，
    而 activate() 写盘后不刷缓存。Windows 会延迟更新最后写入时间，两次快速切换
    可能落到同一个 mtime，缓存于是继续返回上一个人格——而 active_id() 决定
    scoped_user_id()，也就是记忆/会话写进哪个人格命名空间。
    （CI 上间歇失败：main 的 run #31 红、#33 绿。）

    这里把「两次写入 mtime 相同」这个条件**固定下来**，让间歇复现变成必然：
    只要有人把 activate() 里的显式失效去掉，这个断言就会稳定地红。
    """
    from unittest.mock import patch

    from backend.core import persona_profiles as profiles

    # 先切换一次：既进入被测状态，也保证 active.json 一定存在
    # （不要依赖"前面某个测试已经建过它"，那样单独跑这个文件就会崩）
    _switch_to_alt()
    try:
        frozen = profiles._STATE.stat().st_mtime
        real_write = profiles._write_json

        def write_with_frozen_mtime(path, value):
            real_write(path, value)
            if path == profiles._STATE:
                import os as _os

                _os.utime(path, (frozen, frozen))  # 模拟「写完了但 mtime 没变」

        with patch.object(profiles, "_write_json", write_with_frozen_mtime):
            # 缓存里是切换前那个人格，且时间戳与文件一致（= 看起来仍然有效）
            profiles._cached_active_id = "上一轮激活过的人格"
            profiles._cached_state_mtime = frozen
            profiles.activate(profiles.DEFAULT_PERSONA_ID)
            assert profiles.active_id() == profiles.DEFAULT_PERSONA_ID, \
                "mtime 未变时，activate() 必须显式失效缓存"
            assert profiles.active_user_id() == profiles.DEFAULT_USER_ID, \
                "命名空间必须跟着切回来（否则新记忆写进上一个人格）"
    finally:
        _switch_back_to_default()
    print("[OK] 切人格后 active_id 缓存立刻失效（不依赖 mtime）")


def test_canon_life_gated() -> None:
    """菟菚的正典生活（生活模板池/状态行）只对默认人格成立。"""

    from backend.core.life_templates import load_templates

    default_pool = load_templates()
    assert any(t.output_kind != "rest" for t in default_pool)  # 内置池完整

    _switch_to_alt()
    try:
        # 生活模板池：无定制文件时空池（只剩中性 rest 兜底），不回退菟菚内置池
        alt_pool = load_templates()
        assert all(t.output_kind == "rest" for t in alt_pool)
        assert len(alt_pool) == 1
    finally:
        _switch_back_to_default()
    print("[OK] 正典生活（模板池）仅默认人格")


def main() -> None:
    test_viewpoint_and_diary_prompts_replaceable()
    test_dispatch_pattern_follows_persona()
    test_image_prompts_diverge_by_persona()
    test_her_profile_gated()
    test_activate_invalidates_active_id_cache()
    test_canon_life_gated()
    print("\n=== 人格名同步回归：全部通过 ===")


if __name__ == "__main__":
    main()
