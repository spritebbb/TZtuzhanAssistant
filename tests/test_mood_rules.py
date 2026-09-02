# -*- coding: utf-8 -*-
"""心情规则可配置化回归测试：默认规则 + 文件覆盖 + 便捷读取。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core import mood_rules
from backend.core.mood_rules import (
    load_rules,
    invalidate_cache,
    mood_levels,
    weather_base,
    delta_bad,
    delta_fun,
    delta_care,
    delta_good_news,
    idle_params,
    special_day_bonus,
    bonus_multiplier,
    patterns,
    bad_words,
    compiled_pattern,
    bad_re,
)
from backend.core.mood import (
    mood_label,
    weather_baseline,
    mood_delta_from_text,
    idle_decay,
    mood_bonus_multiplier,
)
from backend.core.config import config


def test_default_rules():
    """内置默认规则：与历史行为一致。"""
    r = load_rules()
    assert "mood_levels" in r and "weather_base" in r and "deltas" in r
    assert len(mood_levels()) == 5
    assert weather_base()["晴"] == 75
    assert delta_bad() == -12
    assert delta_fun() == 4
    assert delta_care() == 3
    assert delta_good_news() == 5
    assert special_day_bonus() == 10
    assert idle_params() == (0.5, 15)
    print("[OK] 默认规则完整")


def test_mood_behavior():
    """心情行为与硬编码时代完全一致。"""
    # 标签
    assert mood_label(50)[0] == "慵懒"
    assert mood_label(90)[0] == "雀跃"
    assert mood_label(10)[0] == "低落"
    # 天气基线
    assert weather_baseline("晴") == 75
    assert weather_baseline("阴天") == 52
    assert weather_baseline("台风") == 58
    # 互动
    assert mood_delta_from_text("傻逼") == -12
    assert mood_delta_from_text("哈哈笑死") == 4
    assert mood_delta_from_text("辛苦了") == 3
    assert mood_delta_from_text("成功") == 5
    assert mood_delta_from_text("你好") == 0
    # 冷落衰减
    assert idle_decay(0) == 0
    assert idle_decay(24) == -12
    assert idle_decay(100) == -15  # 封顶
    # 好感度倍率
    assert mood_bonus_multiplier(90) == 1.5
    assert mood_bonus_multiplier(50) == 1.0
    assert mood_bonus_multiplier(10) == 0.6
    print("[OK] 心情行为与硬编码一致")


def test_file_override():
    """data/mood_rules.json 覆盖：未覆盖项保留默认。"""
    rules_path = config.data_dir / "mood_rules.json"
    rules_path.write_text(json.dumps({
        "deltas": {"bad": -20, "fun": 10},
        "weather_base": {"晴": 99, "自定义": 42},
        "special_day_bonus": 15,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    invalidate_cache()

    try:
        r = load_rules()
        # 覆盖生效
        assert delta_bad() == -20, f"bad 应覆盖为 -20: {delta_bad()}"
        assert delta_fun() == 10
        assert weather_base()["晴"] == 99
        assert weather_base()["自定义"] == 42
        assert special_day_bonus() == 15
        # 未覆盖项保留默认
        assert delta_care() == 3
        assert delta_good_news() == 5
        assert idle_params() == (0.5, 15)
        assert len(mood_levels()) == 5
        print("[OK] 文件覆盖：覆盖项生效、未覆盖项保留默认")
    finally:
        rules_path.unlink(missing_ok=True)
        invalidate_cache()


def test_regex_cache():
    """正则编译缓存：规则不变时复用同一对象。"""
    p1 = compiled_pattern("fun")
    p2 = compiled_pattern("fun")
    assert p1 is p2, "正则应缓存复用"
    b1 = bad_re()
    b2 = bad_re()
    assert b1 is b2
    assert bad_re().search("你就是个废物")
    print("[OK] 正则缓存复用 + 冒犯词命中")


def main():
    test_default_rules()
    test_mood_behavior()
    test_file_override()
    test_regex_cache()
    print("\n=== 心情规则可配置化: 4 项全部通过 ===")


main()
