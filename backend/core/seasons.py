# -*- coding: utf-8 -*-
"""M4 关系季节：从真实信号确定性推导关系当下所处的「季节」。

设计约束（docs/TECH-PLAN.md M4）：
- 季节由长期真实行为形成（事件库/活动/消息频率/张力），不随机生成、不作为等级树。
- 只响应真实互动：每个季节都必须能说出「因为哪件真实发生的事」。
- 不因用户离线降好感、不制造负罪感：忙碌期是松弛描述，不是惩罚。
- 修复期的语气已由行为帧 tension_line 负责，这里只做标注，不重复注入。
- 跨人格隔离：所有查询按 user_id 命名空间。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .log import logger
from .relationship_events import active_events
from .userdb import db

# 季节 → 注入行为帧的一句自然语言指令（空串 = 只标注不注入）
_SEASON_LINES = {
    "celebrate": (
        "你们最近有件值得高兴的事，你可以带着这点高兴的余韵说话——"
        "自然一点就好，别硬嗨、别刻意点名庆祝。"
    ),
    "create": (
        "你们正一起做着点什么，聊起来可以带着这份共同的进度感——"
        "像记得你们正在一起做的事那样，但别每句话都拽回这个话题。"
    ),
    "busy": (
        "你们最近都挺忙、说得不多。语气松弛自然就好，不追问对方去哪了、"
        "不抱怨来得少，也不需要为此补偿什么热情。"
    ),
    "repair": "",   # 语气由 tension_line 负责，避免同一信号注入两遍
    "quiet": "",    # 平静陪伴，无需额外指令
}

_SEASON_LABELS = {
    "celebrate": "庆祝期",
    "create": "创作期",
    "busy": "忙碌期",
    "repair": "修复期",
    "quiet": "沉淀期",
}

_CREATE_WINDOW_DAYS = 7
_CELEBRATE_WINDOW_DAYS = 2
_BUSY_WINDOW_DAYS = 3
_BUSY_MIN_MESSAGES = 3


def _last_message_ts(user_id: str) -> str | None:
    with db._lock:
        row = db.conn.execute(
            "SELECT ts FROM messages WHERE user_id = ? AND role = 'user' "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return str(row["ts"]) if row else None


def _user_message_count_since(user_id: str, since: str) -> int:
    with db._lock:
        row = db.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE user_id = ? AND role = 'user' AND ts >= ?",
            (user_id, since),
        ).fetchone()
    return int(row[0])


def current_season(user_id: str, state=None, today: date | None = None) -> dict:
    """推导当前关系季节。返回 {"season", "label", "reason", "line"}。

    state 可选（需要 tension 字段）；today 仅测试注入用。
    按优先级：修复 > 庆祝 > 创作 > 忙碌 > 沉淀。
    """
    try:
        # 1) 修复期：存在未修复的真实张力
        tension = int(getattr(state, "tension", 0) or 0) if state is not None else 0
        if tension > 0:
            return {
                "season": "repair",
                "label": _SEASON_LABELS["repair"],
                "reason": f"还有 {tension}/100 的关系张力未修复",
                "line": _SEASON_LINES["repair"],
            }

        # 2) 庆祝期：刚过去/正在过的特殊日子，或刚完成的约定
        celebrate = active_events(
            user_id, event_type="important_date",
            limit=1, within_days=_CELEBRATE_WINDOW_DAYS,
        )
        if celebrate:
            label = celebrate[0]["payload"].get("label") or celebrate[0]["object"]
            return {
                "season": "celebrate",
                "label": _SEASON_LABELS["celebrate"],
                "reason": f"刚迎来了特殊日子「{label}」",
                "line": _SEASON_LINES["celebrate"],
            }
        promises = active_events(
            user_id, event_type="promise_completed",
            limit=1, within_days=_CELEBRATE_WINDOW_DAYS,
        )
        if promises:
            content = promises[0]["payload"].get("content") or promises[0]["object"]
            return {
                "season": "celebrate",
                "label": _SEASON_LABELS["celebrate"],
                "reason": f"刚完成了约定「{content}」",
                "line": _SEASON_LINES["celebrate"],
            }

        # 3) 创作期：最近读完或正在共读
        finished = active_events(
            user_id, event_type="reading_finished",
            limit=1, within_days=_CREATE_WINDOW_DAYS,
        )
        if finished:
            filename = finished[0]["payload"].get("filename") or finished[0]["object"]
            return {
                "season": "create",
                "label": _SEASON_LABELS["create"],
                "reason": f"{_CREATE_WINDOW_DAYS} 天内一起读完了《{filename}》",
                "line": _SEASON_LINES["create"],
            }
        with db._lock:
            reading = db.conn.execute(
                "SELECT id FROM activities WHERE user_id = ? AND kind = 'reading' "
                "AND status = 'active' LIMIT 1",
                (user_id,),
            ).fetchone()
        if reading is not None:
            return {
                "season": "create",
                "label": _SEASON_LABELS["create"],
                "reason": "你们有一场还没读完的共读",
                "line": _SEASON_LINES["create"],
            }

        # 4) 忙碌期：最近几天聊得少（描述性，不是惩罚）
        day = today or date.today()
        since = (datetime.combine(day - timedelta(days=_BUSY_WINDOW_DAYS), datetime.min.time())).isoformat(timespec="seconds")
        if _user_message_count_since(user_id, since) < _BUSY_MIN_MESSAGES:
            return {
                "season": "busy",
                "label": _SEASON_LABELS["busy"],
                "reason": f"最近 {_BUSY_WINDOW_DAYS} 天你们都说得不多",
                "line": _SEASON_LINES["busy"],
            }
    except Exception:
        logger.exception("[关系季节] 推导失败，按沉淀期继续")

    # 5) 沉淀期：没有特别的波峰，平静陪伴
    return {
        "season": "quiet",
        "label": _SEASON_LABELS["quiet"],
        "reason": "眼下没有特别的波峰，平静陪伴",
        "line": _SEASON_LINES["quiet"],
    }
