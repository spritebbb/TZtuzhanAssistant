"""LLM 语义感知：让菟菚「读懂」每一句话，而非靠关键词打点。

旧的 affection.check_* / mood.mood_delta_from_text 用几百个硬编码关键词判断
「你在关心我 / 你在骂我 / 你在夸我」，导致模型说一句没匹配到词的真情实感时系统无感，
或「想你」同时躺在关心和表白两个词表里逻辑打架。

本模块用一次 LLM 调用对用户消息做语义判断，输出结构化结果：
- emotion_delta：这句话对菟菚情绪的影响（正=开心，负=低落）
- affection_delta：对好感度的影响
- emotional_hit：是否有明显情绪冲击（被夸/被冒犯/被感动/被冷落…），供情绪记忆
- flags：结构化信号（care / abuse / apology / sharing / compliment / confession / bad_address）

失败降级：LLM 调用失败/超时/无 key 时，回退到旧的关键词规则（affection/mood 的
check_* 函数），保证对话不卡、行为不退化。降级结果质量较低但绝不断链。
"""
from __future__ import annotations

import json
import re
from typing import Any

from .log import logger

# ---- LLM 感知 prompt ----
_PERCEPTION_SYSTEM = (
    "你是菟菚的「感知层」，负责判断用户刚说的这句话，对菟菚（一个坚强、腹黑毒舌、"
    "独立但有分寸的女孩子，与用户是隔着屏幕的网友）会产生什么样的情绪和好感度影响。\n\n"
    "只输出一个 JSON 对象，不要输出任何解释、不要用 markdown 代码块。字段如下：\n"
    "{\n"
    '  "emotion_delta": 整数，范围 -15 到 +15，这句话让菟菚心情变好(正)还是变差(负)，0 表示无明显影响,\n'
    '  "affection_delta": 整数，范围 -8 到 +5，这句话让菟菚对用户的好感度增减，0 表示无影响,\n'
    '  "emotional_hit": 字符串，若这句话有明显情绪冲击（被夸/被冒犯/被真诚关心/被分享心事/被冷落/被告白等）就简短概括（如"被真诚关心了""被冒犯了"），没有就输出空字符串,\n'
    '  "hit_weight": 小数 0-1，情绪冲击的强度（1=很强烈，0.3=轻微），无冲击时 0,\n'
    '  "care": 布尔，用户是否在关心/问候菟菚,\n'
    '  "abuse": 布尔，用户是否在辱骂/冒犯菟菚（注意：骂别人不算）,\n'
    '  "apology": 布尔，用户是否在道歉/求原谅,\n'
    '  "sharing": 布尔，用户是否在分享心事/秘密/烦恼,\n'
    '  "compliment": 布尔，用户是否在夸菟菚,\n'
    '  "confession": 布尔，用户是否在表白/求婚/说喜欢,\n'
    '  "bad_address": 布尔，用户是否在要求菟菚用侮辱性/失当的称呼叫他,\n'
    '  "dismiss": 布尔，用户是否在轻视/敷衍/不尊重菟菚\n'
    "}\n\n"
    "判断要贴合菟菚的性格：她坚强、有尊严、不吃套路。对方善意玩笑用腹黑化解，恶意挑衅才真生气；"
    "过早表白会让她反感（扣分），真诚关心会让她心里一暖（加分）。"
)

# 感知超时与最大 token
_PERCEPTION_TIMEOUT = 20
_PERCEPTION_MAX_TOKENS = 200


def _parse_json(text: str) -> dict[str, Any] | None:
    """从 LLM 输出里稳健提取 JSON（容忍 ```json 包裹、前后杂字）。"""
    if not text:
        return None
    # 去掉 markdown 代码块
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _clamp_int(v: Any, lo: int, hi: int, default: int = 0) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except Exception:
        return default


def _normalize(result: dict[str, Any]) -> dict[str, Any]:
    """把 LLM 返回的字典规整成确定 schema，缺字段补默认、越界收敛。"""
    def flag(k: str) -> bool:
        return bool(result.get(k, False))

    return {
        "emotion_delta": _clamp_int(result.get("emotion_delta"), -15, 15),
        "affection_delta": _clamp_int(result.get("affection_delta"), -8, 5),
        "emotional_hit": str(result.get("emotional_hit") or "").strip()[:40],
        "hit_weight": max(0.0, min(1.0, float(result.get("hit_weight", 0) or 0))),
        "care": flag("care"),
        "abuse": flag("abuse"),
        "apology": flag("apology"),
        "sharing": flag("sharing"),
        "compliment": flag("compliment"),
        "confession": flag("confession"),
        "bad_address": flag("bad_address"),
        "dismiss": flag("dismiss"),
        # 是否走的是关键词降级路径（供 pipeline 判断是否还要补关键词每日奖励）
        "degraded": bool(result.get("degraded", False)),
    }


async def perceive(text: str, *, mock: bool = False) -> dict[str, Any]:
    """对用户消息做 LLM 语义感知，返回规整后的感知结果 dict。

    mock=True 或 LLM 不可用/失败时，降级到关键词规则（见 _fallback_rule）。
    """
    if not text or not text.strip():
        return _empty_result()

    if mock:
        return _fallback_rule(text)
    try:
        from .llm import chat

        raw = await chat(
            [
                {"role": "system", "content": _PERCEPTION_SYSTEM},
                {"role": "user", "content": text},
            ],
            temperature=0.1,
            max_tokens=_PERCEPTION_MAX_TOKENS,
            perception=True,  # 走感知层独立小模型（未配置则复用主 LLM）
        )
        result = _parse_json(raw)
        if result is None:
            logger.warning("[perception] LLM 输出非 JSON，降级关键词规则: %r", raw[:80])
            return _fallback_rule(text)
        return _normalize(result)
    except Exception:
        # LLM 不可用（无 key / 网络失败 / 超时）→ 降级，绝不阻塞对话
        logger.debug("[perception] LLM 感知失败，降级关键词规则")
        return _fallback_rule(text)


def _empty_result() -> dict[str, Any]:
    return _normalize({})


# ---- 关键词规则降级（沿用旧逻辑，质量较低但零成本、不断链）----
def _fallback_rule(text: str) -> dict[str, Any]:
    """旧规则降级：复用 affection/mood 的关键词判断，拼出同 schema 结果。"""
    try:
        from . import affection, mood

        result = {
            "emotion_delta": mood.mood_delta_from_text(text),
            "affection_delta": 0,
            "emotional_hit": "",
            "hit_weight": 0.0,
            "care": affection.check_care(text),
            "abuse": affection.check_abuse(text),
            "apology": affection.check_apology(text),
            "sharing": affection.check_sharing(text),
            "compliment": affection.check_compliment(text),
            "confession": affection.check_early_confession(text),
            "bad_address": False,
            "dismiss": False,
            "degraded": True,
        }
        # 好感度 delta 的降级映射：正向信号保留（care/apology/sharing 等语义感知
        # 覆盖不到的部分），但「辱骂」不在这里设 affection_delta——因为降级结果
        # degraded=True，pipeline 会走关键词兜底 apply_abuse_penalty 统一扣分；
        # 若这里也设 -5，会导致「apply_impulse -5 + apply_abuse_penalty -5」双扣。
        # 因此辱骂只标记 emotional_hit（情绪冲击），好感度交给兜底通道。
        if result["abuse"]:
            result["affection_delta"] = 0
            result["emotional_hit"] = "被冒犯了"
            result["hit_weight"] = 0.9
        elif result["care"]:
            result["affection_delta"] = 1
            result["emotional_hit"] = "被关心了"
            result["hit_weight"] = 0.6
        elif result["compliment"]:
            result["affection_delta"] = 1
            result["emotional_hit"] = "被夸了"
            result["hit_weight"] = 0.6
        elif result["apology"]:
            result["affection_delta"] = 2
        elif result["sharing"]:
            result["affection_delta"] = 1
            result["emotional_hit"] = "被分享心事"
            result["hit_weight"] = 0.7
        elif result["confession"]:
            result["affection_delta"] = -1
            result["emotional_hit"] = "被过早表白"
            result["hit_weight"] = 0.5
        return result
    except Exception:
        return _empty_result()
