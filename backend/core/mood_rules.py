# -*- coding: utf-8 -*-
"""心情规则配置中心：集中管理心情系统的全部可调参数。

所有规则默认内置（与历史行为一致）；用户可通过 `data/mood_rules.json`
覆盖任意一项，无需改代码。文件不存在时全部走内置默认。

支持覆盖的规则：
- mood_levels: 心情阈值 → 情绪状态 [(阈值, 名, 描述), ...]
- weather_base: 天气关键词 → 心情基线 {kw: base}
- deltas: 互动增减量 {bad, fun, good_news, care}
- idle: 冷落衰减 {per_hour, cap}
- special_day_bonus: 特殊日子当日加成
- bonus_multiplier: 心情 → 好感度倍率 [(最低心情, 倍率), ...]
- patterns: 触发正则 {fun, care, good_news}
- bad_words: 冒犯词列表

读取规则用 `load_rules()`，实时读文件（带短缓存），热改立即生效。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .config import config

# 规则文件：项目 data/mood_rules.json（相对 data_dir 固定名）
_RULES_PATH: Path = config.data_dir / "mood_rules.json"

# ---- 内置默认（与历史行为完全一致）----
_DEFAULT_RULES: dict[str, Any] = {
    "mood_levels": [
        [0, "低落", "心情很差，有点烦闷，说话会短、直接，容易不耐烦"],
        [25, "平淡", "心情一般，不悲不喜，说话平静、有条理"],
        [45, "慵懒", "整个人懒懒的，提不起劲，说话简短随意"],
        [65, "开心", "心情不错，说话轻快，偶尔俏皮、爱开玩笑"],
        [85, "雀跃", "心情非常好，活跃、爱说话，想找人分享开心的事"],
    ],
    "weather_base": {
        "晴": 75, "多云": 68, "阴": 52, "雨": 45, "雪": 62,
        "风": 58, "雾": 50, "雷": 40, "沙尘": 38, "霾": 42,
    },
    "deltas": {"bad": -12, "fun": 4, "good_news": 5, "care": 3},
    "idle": {"per_hour": 0.5, "cap": 15},
    "special_day_bonus": 10,
    "bonus_multiplier": [[85, 1.5], [65, 1.2], [45, 1.0], [25, 0.8], [0, 0.6]],
    "patterns": {
        "fun": r"(好笑|哈哈|笑死|太逗|有趣|好玩|梗|笑不活|绷不住|乐了|笑鼠|整活)",
        "care": r"(你还好吗|你没事吧|累不累|辛苦了|你也要休息|照顾好自己|别太累|担心你|想你|想你了|抱抱|摸摸)",
        "good_news": r"(升职|加薪|考上了|成功|中了|赢|过啦|通过了|第一次|今天好开心|超喜欢|好高兴|太好啦|太好了)",
    },
    # 冒犯词：只用多字词（单字会误伤「爬山/翻滚/垃圾分类」等）
    "bad_words": [
        "傻逼", "煞笔", "沙比", "废物", "去死", "贱人", "畜生",
        "脑残", "智障", "cnm", "草泥马", "真没意思", "无聊死了", "恶心死了", "滚蛋",
    ],
}

# 读盘短缓存：规则文件 mtime 变化才重读
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _read_file() -> dict[str, Any]:
    """读规则文件；不存在/解析失败返回空 dict（全部走默认）。"""
    try:
        if _RULES_PATH.exists():
            return json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def load_rules() -> dict[str, Any]:
    """读取规则（内置默认 + 文件覆盖，文件优先）。带短缓存。"""
    try:
        mtime = _RULES_PATH.stat().st_mtime if _RULES_PATH.exists() else 0
    except OSError:
        mtime = 0
    cached = _cache.get("rules")
    if cached and cached[0] == mtime:
        return cached[1]

    merged = {k: v for k, v in _DEFAULT_RULES.items()}
    merged.update(_read_file())
    _cache["rules"] = (mtime, merged)
    return merged


def invalidate_cache() -> None:
    """清空缓存（写规则文件后调用，或调试用）。"""
    _cache.pop("rules", None)


# ---- 便捷读取 ----
def mood_levels() -> list[list]:
    return list(load_rules().get("mood_levels", _DEFAULT_RULES["mood_levels"]))


def weather_base() -> dict[str, int]:
    return dict(load_rules().get("weather_base", _DEFAULT_RULES["weather_base"]))


def delta_bad() -> int:
    return int(load_rules().get("deltas", _DEFAULT_RULES["deltas"]).get("bad", -12))


def delta_fun() -> int:
    return int(load_rules().get("deltas", _DEFAULT_RULES["deltas"]).get("fun", 4))


def delta_good_news() -> int:
    return int(load_rules().get("deltas", _DEFAULT_RULES["deltas"]).get("good_news", 5))


def delta_care() -> int:
    return int(load_rules().get("deltas", _DEFAULT_RULES["deltas"]).get("care", 3))


def idle_params() -> tuple[float, int]:
    idle = load_rules().get("idle", _DEFAULT_RULES["idle"])
    return float(idle.get("per_hour", 0.5)), int(idle.get("cap", 15))


def special_day_bonus() -> int:
    return int(load_rules().get("special_day_bonus", 10))


def bonus_multiplier() -> list[list]:
    return list(load_rules().get("bonus_multiplier", _DEFAULT_RULES["bonus_multiplier"]))


def patterns() -> dict[str, str]:
    return dict(load_rules().get("patterns", _DEFAULT_RULES["patterns"]))


def bad_words() -> list[str]:
    return list(load_rules().get("bad_words", _DEFAULT_RULES["bad_words"]))


# ---- 编译正则（按需缓存）----
_re_cache: dict[str, re.Pattern] = {}


def compiled_pattern(key: str) -> re.Pattern:
    """编译某类触发正则（按规则版本缓存）。"""
    src = patterns().get(key, "")
    if key in _re_cache and _re_cache[key].pattern == src:
        return _re_cache[key]
    p = re.compile(src) if src else re.compile(r"(?!)")  # 空 → 永不匹配
    _re_cache[key] = p
    return p


def bad_re() -> re.Pattern:
    """编译冒犯词正则。"""
    words = bad_words()
    src = "|".join(re.escape(w) for w in words)
    if "bad" in _re_cache and _re_cache["bad"].pattern == src:
        return _re_cache["bad"]
    p = re.compile(src) if src else re.compile(r"(?!)")
    _re_cache["bad"] = p
    return p
