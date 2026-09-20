# -*- coding: utf-8 -*-
"""§17.1 表达必要性 + D12 主动意愿：necessity 门与 willingness 掷骰。

契约（docs/Zcode技术指导.md §17.1 + docs/D9-D12-DESIGN-2026-09-21.md §1）：

- necessity = relevance + novelty + relationship_value - repetition
  - interruption_cost，各项 0..1、规则版本化（RULE_VERSION）；总分 clamp 0..1；
- 评分只用于**主动候选**和可省略的装饰性句子；用户发起的一对一消息永远
  有答复，绝不能被评分门控为沉默；
- 主动候选默认 necessity ≥ 0.6 才进入既有 arbiter（initiative）；
  阈值是离线校准的工程默认，不在线自调；
- 输入由调用方给确定性信号（来源优先级/近期相似度/关系值），本模块不做
  LLM 调用、不读聊天正文。

D12 意愿 roll（2026-09-21）：

- necessity 回答「这事值得说吗」，willingness 回答「她此刻想不想说」——
  达标候选再掷一次骰，两个都过才出牌；
- p = 阶段基线 + 好感项 − 压抑项（张力/专注安静），clamp 到 [floor, ceil]；
- 掷骰用可复现种子（user+source+分钟），事后可解释、可回放；
- 硬规则源（约定到点跟进等）与用户直接对话**永不进骰子**；
- 连续 _MISSES_COMPENSATE_AFTER 次未中 → p 临时 +补偿（想念补偿），
  防概率化把人静默一整周。
"""
from __future__ import annotations

import random
from datetime import datetime

RULE_VERSION = 1
NECESSITY_THRESHOLD = 0.6  # 离线校准默认；不在线自调

WILLINGNESS_RULE_VERSION = 1
_STAGE_BASE = {"初识": 0.15, "熟悉": 0.30, "亲密": 0.45, "恋人": 0.55}
# 硬规则源：不掷骰直接放行（点名/直接对话不经本模块；确认安全类走各自投递通道）
_HARD_SOURCES = {"initiative:promise_followup"}
_MISSES_KEY = "proactive:willingness"  # kv runtime：连续未中计数（想念补偿输入）
_MISSES_COMPENSATE_AFTER = 3


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def score_necessity(*, relevance: float, novelty: float,
                    relationship_value: float, repetition: float = 0.0,
                    interruption_cost: float = 0.0) -> dict:
    """按版本化公式给一条候选打分。输入应已被调用方 clamp 到 0..1。"""
    n = (
        _clamp01(relevance)
        + _clamp01(novelty)
        + _clamp01(relationship_value)
        - _clamp01(repetition)
        - _clamp01(interruption_cost)
    )
    return {
        "necessity": _clamp01(n),
        "rule_version": RULE_VERSION,
    }


def gate_proactive_candidate(score: dict, *, threshold: float = NECESSITY_THRESHOLD) -> bool:
    """主动候选门：necessity 达标才进 arbiter；阈值离线校准，不在线自调。"""
    return float(score["necessity"]) >= float(threshold)


# 各主动来源的基线画像 (relevance, novelty)。关系价值与重复/打断成本由
# 真实状态计算——基线取「正常可发」，只有重复与近期打扰才会把分数压到线下。
_SOURCE_PROFILE: dict[str, tuple[float, float]] = {
    "initiative:promise_followup": (0.30, 0.30),
    "initiative:rhythm_followup": (0.26, 0.30),
    "initiative:pending_thought": (0.28, 0.30),
    "initiative:archive_suggest": (0.22, 0.30),
    "initiative:outing_note": (0.24, 0.30),
    "initiative:watch_change": (0.35, 0.32),
    "initiative:companion_request": (0.30, 0.30),
    "initiative:surprise": (0.26, 0.28),
    "initiative-loop": (0.24, 0.28),
}
_DEFAULT_PROFILE = (0.24, 0.28)
_REPEAT_WINDOW_HOURS = 24   # 同源 24h 内再次投递 → 重复惩罚
_SOURCE_LAST_KEY = "proactive:source_last:{source}"


def _relationship_value(user_id: str) -> float:
    """关系价值 0.20~0.35：由亲密度线性映射（关系越深，她的话越值得说）。"""
    try:
        from .affection import dimensions_of

        intimacy = float(dimensions_of(user_id)[1])
    except Exception:
        intimacy = 0.0
    return 0.20 + 0.15 * max(0.0, min(1.0, intimacy / 100.0))


def _initiative_weight(user_id: str) -> float:
    """P3-05 initiative_template_weight 作为主动强度系数（默认 0.5 → 1.0 倍）。"""
    try:
        from .persona_evolution import behavior_hints

        return 0.5 + float(behavior_hints(user_id).get("initiative_template_weight", 0.5))
    except Exception:
        return 1.0


def _source_last_at(user_id: str, source: str):
    from .userdb import kv_get

    raw = kv_get(user_id, _SOURCE_LAST_KEY.format(source=source))
    if not raw:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def note_source_delivered(user_id: str, source: str, *, now=None) -> None:
    """投递成功后登记来源时间（§17.1 重复惩罚的输入；kv runtime，已登记）。"""
    from datetime import datetime

    from .userdb import kv_set

    moment = now or datetime.now()
    kv_set(user_id, _SOURCE_LAST_KEY.format(source=source),
           moment.isoformat(timespec="seconds"))


def necessity_for_source(user_id: str, source: str, *, idle_minutes: int,
                         now=None) -> dict:
    """按真实状态给一条主动候选打分（调用方未显式给分时的默认口径）。

    - relevance/novelty：来源基线；24h 内同源已投递 → 新意降低
    - relationship_value：亲密度映射 × P3-05 主动权重系数
    - repetition：24h 内同源重复 → 0.4
    - interruption_cost：打扰基础成本 0.05，距上次真实聊天不足 10 分钟再加 0.05
      （更近的插话更扰人；空闲阈值本身已由 arbiter 前置把关）
    """
    from datetime import datetime, timedelta

    relevance, novelty = _SOURCE_PROFILE.get(source, _DEFAULT_PROFILE)
    moment = now or datetime.now()
    last = _source_last_at(user_id, source)
    if last is not None and moment - last < timedelta(hours=_REPEAT_WINDOW_HOURS):
        novelty = 0.05
        repetition = 0.4
    else:
        repetition = 0.0
    relationship_value = _relationship_value(user_id) * _initiative_weight(user_id)
    interruption_cost = 0.05 + (0.05 if float(idle_minutes) < 10 else 0.0)
    return score_necessity(
        relevance=relevance, novelty=novelty,
        relationship_value=relationship_value,
        repetition=repetition, interruption_cost=interruption_cost,
    )


# ---------------------------------------------------------------------------
# D12 意愿 roll
# ---------------------------------------------------------------------------

def _stage_of(user_id: str) -> str:
    try:
        from .affection import dimensions_of, dimensions_stage

        trust, intimacy = dimensions_of(user_id)
        return dimensions_stage(int(trust), int(intimacy))
    except Exception:
        return "熟悉"


def _miss_streak(user_id: str) -> int:
    from .userdb import kv_get

    try:
        return max(0, int(kv_get(user_id, _MISSES_KEY) or 0))
    except Exception:
        return 0


def note_willingness_outcome(user_id: str, decision: dict) -> None:
    """出牌后清零、未中后 +1 的想念补偿计数（kv runtime，已登记）。"""
    from .userdb import kv_set

    try:
        if decision.get("speak") or decision.get("hard_rule") or decision.get("disabled"):
            kv_set(user_id, _MISSES_KEY, 0)
        else:
            kv_set(user_id, _MISSES_KEY, _miss_streak(user_id) + 1)
    except Exception:
        pass


def willingness(user_id: str, *, source: str = "", now=None) -> dict:
    """她此刻想不想主动说话：p 与一次可复现的掷骰。不做 LLM 调用。"""
    from .config import config

    moment = now or datetime.now()
    base = _STAGE_BASE.get(_stage_of(user_id), 0.30)
    try:
        from .affection import dimensions_of

        _trust, intimacy = dimensions_of(user_id)
        affection_term = (
            max(0.0, min(1.0, float(intimacy) / 100.0))
            * float(config.willingness_affection_weight)
        )
    except Exception:
        affection_term = 0.0
    suppression = 0.0
    try:
        from .state import _load_tension

        tension = max(0.0, min(100.0, float(_load_tension(user_id).get("level", 0) or 0)))
        suppression += tension / 100.0 * float(config.willingness_tension_weight)
    except Exception:
        pass
    try:
        from .focus import focus_in_progress

        if focus_in_progress(user_id):
            suppression += float(config.willingness_focus_penalty)
    except Exception:
        pass
    misses = _miss_streak(user_id)
    compensate = (
        float(config.willingness_miss_compensate)
        if misses >= _MISSES_COMPENSATE_AFTER else 0.0
    )
    floor, ceil = float(config.willingness_floor), float(config.willingness_ceil)
    p = max(floor, min(ceil, base + affection_term - suppression + compensate))
    seed = f"{user_id}:{source}:{moment.strftime('%Y%m%d%H%M')}"
    roll = random.Random(seed).random()
    return {
        "p": round(p, 4),
        "base": round(base, 4),
        "affection_term": round(affection_term, 4),
        "suppression": round(suppression, 4),
        "miss_streak": misses,
        "compensate": round(compensate, 4),
        "roll": round(roll, 4),
        "speak": roll < p,
        "seed_minute": moment.strftime("%Y%m%d%H%M"),
        "rule_version": WILLINGNESS_RULE_VERSION,
    }


def willingness_decision(user_id: str, *, source: str = "", now=None) -> tuple[bool, dict]:
    """roll 层判定（necessity 已由调用方把关）。返回 (是否开口, 判定详情)。

    flag 关闭 → 不掷直接放行；硬规则源 → 不掷直接放行。
    """
    try:
        from .features import flag

        if not flag("willingness_enabled"):
            return True, {"disabled": True}
    except Exception:
        pass  # flag 系统异常时保守放行（不因开关故障静默她）
    if source in _HARD_SOURCES:
        return True, {"hard_rule": True}
    decision = willingness(user_id, source=source, now=now)
    return bool(decision["speak"]), decision
