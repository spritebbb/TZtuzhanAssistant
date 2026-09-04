# -*- coding: utf-8 -*-
"""C5 养成仪表盘：把分散的关系、状态、聊天与用量数据聚合成日序列。"""
from __future__ import annotations

from datetime import date, timedelta

from .affection import display as affection_display
from .config import config
from .state import load_state
from .unlock import list_slots
from .userdb import db


def _cost(prompt: int, completion: int) -> float:
    return round(
        prompt * config.llm_price_input_per_mtok / 1_000_000
        + completion * config.llm_price_output_per_mtok / 1_000_000,
        4,
    )


def dashboard_summary(user_id: str, days: int = 30) -> dict:
    """返回最近 ``days`` 天的养成总览；所有趋势按本地自然日聚合。"""
    days = max(7, min(90, int(days)))
    today = date.today()
    start = today - timedelta(days=days - 1)
    since = start.isoformat()
    until = (today + timedelta(days=1)).isoformat()
    db.ensure_user(user_id)

    with db._lock:
        affection_base_row = db.conn.execute(
            "SELECT value FROM affection_log "
            "WHERE user_id = ? AND ts < ? AND value IS NOT NULL "
            "ORDER BY ts DESC, id DESC LIMIT 1",
            (user_id, since),
        ).fetchone()
        affection_rows = db.conn.execute(
            "SELECT value, delta, reason, ts FROM affection_log "
            "WHERE user_id = ? AND ts >= ? AND ts < ? "
            "ORDER BY ts, id",
            (user_id, since, until),
        ).fetchall()

        mood_base_row = db.conn.execute(
            "SELECT value FROM mood_log WHERE user_id = ? AND ts < ? "
            "ORDER BY ts DESC, id DESC LIMIT 1",
            (user_id, since),
        ).fetchone()
        mood_rows = db.conn.execute(
            "SELECT value, ts FROM mood_log WHERE user_id = ? AND ts >= ? AND ts < ? "
            "ORDER BY ts, id",
            (user_id, since, until),
        ).fetchall()

        message_rows = db.conn.execute(
            "SELECT substr(ts, 1, 10) AS day, COUNT(*) AS messages, "
            "SUM(CASE WHEN role = 'user' THEN 1 ELSE 0 END) AS user_messages "
            "FROM messages WHERE user_id = ? AND ts >= ? AND ts < ? "
            "GROUP BY substr(ts, 1, 10)",
            (user_id, since, until),
        ).fetchall()
        usage_rows = db.conn.execute(
            "SELECT substr(ts, 1, 10) AS day, "
            "COALESCE(SUM(prompt_tokens), 0) AS prompt, "
            "COALESCE(SUM(completion_tokens), 0) AS completion, COUNT(*) AS calls "
            "FROM usage_log WHERE user_id = ? AND ts >= ? AND ts < ? "
            "GROUP BY substr(ts, 1, 10)",
            (user_id, since, until),
        ).fetchall()
        promises = [dict(row) for row in db.conn.execute(
            "SELECT id, content, follow_up, created_at FROM promises "
            "WHERE user_id = ? AND status = 'pending' "
            "ORDER BY CASE WHEN follow_up = '' THEN 1 ELSE 0 END, follow_up, id LIMIT 5",
            (user_id,),
        ).fetchall()]
        pending_promise_count = int(db.conn.execute(
            "SELECT COUNT(*) FROM promises WHERE user_id = ? AND status = 'pending'",
            (user_id,),
        ).fetchone()[0])
        diary_count = int(db.conn.execute(
            "SELECT COUNT(*) FROM diary WHERE user_id = ? AND date >= ? AND date < ?",
            (user_id, since, until),
        ).fetchone()[0])

    affection_by_day: dict[str, list] = {}
    for row in affection_rows:
        affection_by_day.setdefault(str(row["ts"])[:10], []).append(row)
    mood_by_day: dict[str, list] = {}
    for row in mood_rows:
        mood_by_day.setdefault(str(row["ts"])[:10], []).append(row)
    messages_by_day = {row["day"]: row for row in message_rows}
    usage_by_day = {row["day"]: row for row in usage_rows}

    affection_value = int(affection_base_row["value"]) if affection_base_row else 0
    mood_value = int(mood_base_row["value"]) if mood_base_row else 60
    timeline = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).isoformat()
        if affection_by_day.get(day):
            affection_value = int(affection_by_day[day][-1]["value"])
        if mood_by_day.get(day):
            mood_value = int(mood_by_day[day][-1]["value"])
        message_row = messages_by_day.get(day)
        usage_row = usage_by_day.get(day)
        prompt = int(usage_row["prompt"]) if usage_row else 0
        completion = int(usage_row["completion"]) if usage_row else 0
        timeline.append({
            "date": day,
            "affection": affection_value,
            "mood": mood_value,
            "messages": int(message_row["messages"]) if message_row else 0,
            "user_messages": int(message_row["user_messages"] or 0) if message_row else 0,
            "tokens": prompt + completion,
            "calls": int(usage_row["calls"]) if usage_row else 0,
        })

    state = load_state(user_id)
    relationship = affection_display(user_id)
    slots = list_slots(user_id)
    total_messages = sum(point["messages"] for point in timeline)
    total_tokens = sum(point["tokens"] for point in timeline)
    total_prompt = sum(int(row["prompt"]) for row in usage_rows)
    total_completion = sum(int(row["completion"]) for row in usage_rows)
    delivered = sum(slot["status"] == "delivered" for slot in slots)

    return {
        "days": days,
        "current": {
            "affection": relationship,
            "mood": {"value": state.emotion, "label": state.emotion_name},
            "energy": state.energy,
            "resting": state.resting,
            "pending_promises": pending_promise_count,
        },
        "timeline": timeline,
        "stats": {
            "active_days": sum(point["messages"] > 0 for point in timeline),
            "messages": total_messages,
            "user_messages": sum(point["user_messages"] for point in timeline),
            "tokens": total_tokens,
            "cost": _cost(total_prompt, total_completion),
            "diaries": diary_count,
            "unlocks": delivered,
            "unlock_total": len(slots),
        },
        "promises": promises,
        "recent_affection": [
            {
                "value": int(row["value"]),
                "delta": int(row["delta"]),
                "reason": row["reason"],
                "ts": row["ts"],
            }
            for row in reversed(affection_rows[-5:])
        ],
    }
