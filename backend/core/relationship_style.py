# -*- coding: utf-8 -*-
"""L03 关系分支与可解释形成原因：长期关系气质（不替代阶段/二维）。

契约（docs/Zcode技术指导.md §16 L03 + 调度文档批次2）：
- 新表 ``relationship_style_evidence``（唯一 user/event/style），只吃明确事件：
  共同活动→growth、明确互相理解→confidant、用户确认喜欢的梗→playful、
  romantic 额外受双维阶段约束（恋人门控）；
- 90 天窗口，权重按 30 天半衰期指数衰减；至少 3 个不同日期的有效事件才
  显示总结，否则「还在慢慢形成」；最高两类权重差 <10% 可并存；
- ``derive_style`` 返回 style_ids、最多 2 条来源简述、valid_until；
  **不向 UI 返回分数**；
- 自动倾向对 behavior 单轴最大 ±0.1（clamp，不覆盖显式用户偏好与阶段边界）；
- 手工「不要这种气质」走 P2 偏好屏蔽（user_preferences 新 category=style）；
- 与旧 seasons（当前时期）/style（长期气质）语义分离；
- reset 删除证据；源删后即时重算（事件删除走既有级联）；
- 开关 relationship_style_enabled（默认 True，设置页可关）。
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta

from .log import logger

STYLES: tuple[str, ...] = ("companion", "playful", "confidant", "growth", "romantic")

_STYLE_LABELS = {
    "companion": "陪伴型",
    "playful": "玩闹型",
    "confidant": "知心型",
    "growth": "共同成长型",
    "romantic": "浪漫型",
}

_HALF_LIFE_DAYS = 30.0
_WINDOW_DAYS = 90
_MIN_DISTINCT_DAYS = 3
_COEXIST_RATIO = 0.10          # 最高两类权重差 <10% 时并存
_BEHAVIOR_DELTA_MAX = 0.1      # 自动倾向单轴最大 ±0.1

# 事件类型 → 气质映射（L03 设计原文；romantic 额外受阶段约束）
_EVENT_STYLE_MAP = {
    "reading_finished": ("growth",),
    "goal_completed": ("growth",),
    "story_finished": ("companion",),
    "list_completed": ("companion",),
    "focus_finished": ("companion",),
    "memory_corrected": ("confidant",),
    "important_date": ("romantic",),
}

_ROMANTIC_MIN_STAGE = "恋人"
_STAGE_ORDER = {"初识": 0, "熟悉": 1, "亲密": 2, "恋人": 3}


class RelationshipStyleError(ValueError):
    """关系气质的预期业务错误。"""


def style_label(style: str) -> str:
    return _STYLE_LABELS.get(style, style)


# ---- 证据入账（唯一 reducer 入口，幂等 user/event/style） ----

def record_evidence(user_id: str, event_id: int, event_type: str, *,
                    occurred_at: str | None = None,
                    stage: str | None = None) -> list[str]:
    """按事件类型登记气质证据；返回本次新登记的 style 列表。

    同一 (user, event, style) 幂等；romantic 在非恋人阶段不登记
    （低亲密没有 romantic——L03 验收条款）。
    """
    styles = _EVENT_STYLE_MAP.get(event_type)
    if not styles:
        return []
    if any(s == "romantic" for s in styles):
        order = _STAGE_ORDER.get(stage or "", 0)
        if stage is None:
            # 未显式给阶段时读当前阶段
            try:
                from .affection import display

                stage = str(display(user_id).get("stage") or "初识")
            except Exception:
                stage = "初识"
            order = _STAGE_ORDER.get(stage, 0)
        if order < _STAGE_ORDER[_ROMANTIC_MIN_STAGE]:
            styles = tuple(s for s in styles if s != "romantic")
            if not styles:
                return []
    occurred = occurred_at or datetime.now().isoformat(timespec="seconds")
    recorded: list[str] = []
    with _db()._lock:
        for style in styles:
            cur = _db().conn.execute(
                "INSERT OR IGNORE INTO relationship_style_evidence "
                "(user_id, event_id, style, weight, occurred_at) VALUES (?, ?, ?, 1.0, ?)",
                (user_id, int(event_id), style, occurred),
            )
            if cur.rowcount:
                recorded.append(style)
        _db().conn.commit()
    return recorded


def _db():
    from .userdb import db

    return db


# ---- 推导 ----

def _decay(event_date: datetime, now: datetime) -> float:
    """30 天半衰期指数权重（确定性）。"""
    age_days = max(0.0, (now - event_date).total_seconds() / 86400.0)
    return math.pow(0.5, age_days / _HALF_LIFE_DAYS)


def derive_style(user_id: str, now: datetime | None = None) -> dict:
    """推导长期关系气质：只回 style_ids/来源简述/valid_until，不回分数。

    - 90 天窗口；至少 3 个不同日期的有效事件才显示，否则 forming=True；
    - 最高两类权重差 <10% 可并存（顺序按权重，分数不出 UI）；
    - 屏蔽（user_preferences category=style）的气质直接排除。
    """
    from .features import flag

    if not flag("relationship_style_enabled"):
        return {"style_ids": [], "forming": True, "reasons": [], "valid_until": ""}
    now = now or datetime.now()
    cutoff = (now - timedelta(days=_WINDOW_DAYS)).isoformat(timespec="seconds")
    blocked = _blocked_styles(user_id)

    rows = _db().conn.execute(
        "SELECT id, style, occurred_at FROM relationship_style_evidence "
        "WHERE user_id = ? AND occurred_at >= ?",
        (user_id, cutoff),
    ).fetchall()

    weights: dict[str, float] = {}
    days_by_style: dict[str, set[str]] = {}
    reasons: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        style = str(row["style"])
        if style in blocked:
            continue
        try:
            event_dt = datetime.fromisoformat(str(row["occurred_at"]))
        except ValueError:
            continue
        w = _decay(event_dt, now)
        weights[style] = weights.get(style, 0.0) + w
        days_by_style.setdefault(style, set()).add(str(row["occurred_at"])[:10])
        reasons.setdefault(style, []).append((str(row["occurred_at"]), str(row["id"])))

    valid = {s: w for s, w in weights.items()
             if len(days_by_style.get(s, set())) >= _MIN_DISTINCT_DAYS}
    if not valid:
        return {"style_ids": [], "forming": True, "reasons": [], "valid_until": ""}

    ranked = sorted(valid.items(), key=lambda kv: kv[1], reverse=True)
    top_style, top_weight = ranked[0]
    style_ids = [top_style]
    if len(ranked) > 1:
        second_style, second_weight = ranked[1]
        if (top_weight - second_weight) / max(top_weight, 1e-9) < _COEXIST_RATIO:
            style_ids.append(second_style)

    # 最多 2 条来源简述（最新事件的 occurred_at + 简短说明；不含分数）
    short_reasons: list[str] = []
    for style in style_ids:
        label = style_label(style)
        count = len(days_by_style.get(style, set()))
        short_reasons.append(f"{label}：最近 {count} 个不同日子都有对应的共同经历")
        if len(short_reasons) >= 2:
            break

    valid_until = (now + timedelta(days=_WINDOW_DAYS)).date().isoformat()
    return {
        "style_ids": style_ids,
        "forming": False,
        "reasons": short_reasons[:2],
        "valid_until": valid_until,
    }


def _blocked_styles(user_id: str) -> set[str]:
    try:
        from .user_preferences import list_preferences

        rows = list_preferences(user_id)
        blocked = set()
        for r in rows:
            if r.get("category") != "style" or r.get("status") != "active":
                continue
            value = r.get("value") or {}
            if isinstance(value, dict) and value.get("style"):
                blocked.add(str(value["style"]))
        return blocked
    except Exception:
        return set()


def block_style(user_id: str, style: str, source_message_id: int | None = None) -> bool:
    """「不要这种气质」：走 P2-02 偏好屏蔽（category=style，直接 active——明确指令）。"""
    if style not in STYLES:
        raise RelationshipStyleError(f"未知气质: {style}")
    from .user_preferences import _upsert

    row = _upsert(
        user_id, category="style", value={"style": style},
        origin="user_teaching", source_message_id=source_message_id,
        status="active", confidence=1.0,
    )
    if row is not None:
        logger.info("[关系气质] {} 屏蔽 {}", user_id, style)
    return row is not None


def unblock_style(user_id: str, style: str) -> bool:
    """撤销屏蔽：找 active 的该气质屏蔽行，revoke。"""
    from .user_preferences import list_preferences, revoke_preference

    for row in list_preferences(user_id):
        value = row.get("value") or {}
        if (row.get("category") == "style" and row.get("status") == "active"
                and isinstance(value, dict) and value.get("style") == style):
            revoke_preference(user_id, int(row["id"]))
            return True
    return False


# ---- behavior 接线：自动倾向单轴 ±0.1（不覆盖显式偏好与阶段边界） ----

# 气质 → behavior 单轴修正（设计原文：自动倾向对单轴最大 ±0.1）
_STYLE_BEHAVIOR_HINTS = {
    "companion": {"patience": +0.1},
    "playful": {"humor": +0.1},
    "confidant": {"probing": +0.1},
    "growth": {"directness": +0.05, "patience": +0.05},
    "romantic": {"defense": -0.1},
}


def behavior_hint(style_ids: list[str]) -> dict[str, float]:
    """把气质转成 behavior 轴的有限修正（合计 clamp ±0.1）。"""
    axes: dict[str, float] = {}
    for style in style_ids:
        for axis, delta in _STYLE_BEHAVIOR_HINTS.get(style, {}).items():
            axes[axis] = axes.get(axis, 0.0) + delta
    return {axis: max(-_BEHAVIOR_DELTA_MAX, min(_BEHAVIOR_DELTA_MAX, delta))
            for axis, delta in axes.items()}


def forget_for_source(user_id: str, event_id: int) -> int:
    """源事件删除时清对应证据（即时重算：derive_style 每次现算，无需缓存失效）。"""
    with _db()._lock:
        cur = _db().conn.execute(
            "DELETE FROM relationship_style_evidence WHERE user_id=? AND event_id=?",
            (user_id, int(event_id)),
        )
        _db().conn.commit()
    return cur.rowcount
