# -*- coding: utf-8 -*-
"""M2 关系事件内核：过去真实发生的事，变成可追溯、可过期、可作废的事件。

设计约束（docs/TECH-PLAN.md）：
- 事件先于文案：这里只记录确定性发生的事，是否表达由语境门控决定。
- 同一 (user, event_type, source) 只保留一条 active 事件（部分唯一索引作幂等键）。
- 删除来源记录会使关联事件失效，不留幽灵回忆。
- 只支持聊天内自然回忆，不做新增主动推送。
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from .log import logger
from .userdb import db

# 首批 4 类事件；新类型必须先在这里注册才能落库。
EVENT_TYPES: dict[str, str] = {
    "reading_finished": "一起读完了一份文档（来源：activities）",
    "promise_completed": "一条约定到了跟进点并被标记完成（来源：promises）",
    "important_date": "一个特殊日子到来了（来源：important_dates）",
    "memory_corrected": "用户改写或确认了一条记忆（来源：facts）",
    "focus_finished": "一段专注陪伴完成（来源：activities）",
}

_MAX_PAYLOAD_EVENTS = 2
_EVENT_HORIZON_DAYS = 60
_DATE_EVENT_EXPIRY_DAYS = 7

_PROMISE_CUE_RE = re.compile(r"约定|答应|说好|讲好|承诺|说定|上周说|上次说|上次聊|后来那件事")


class RelationshipEventError(ValueError):
    """关系事件的预期业务错误。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def record(
    user_id: str,
    event_type: str,
    source_type: str,
    source_id: int,
    *,
    subject: str = "",
    obj: str = "",
    payload: dict | None = None,
    confidence: float = 1.0,
    privacy: str = "normal",
    occurred_at: str | None = None,
    expires_at: str | None = None,
    commit: bool = True,
    refresh_on_conflict: bool = False,
) -> int | None:
    """幂等写入一条事件。返回事件 id；重复且不刷新时返回 None。

    refresh_on_conflict 用于按年重复的日子：同一条 active 事件刷新发生时间与有效期。
    """
    if event_type not in EVENT_TYPES:
        raise RelationshipEventError(f"未注册的关系事件类型：{event_type}")
    now = _now()
    occurred = occurred_at or now
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    with db._lock:
        cur = db.conn.execute(
            "INSERT OR IGNORE INTO relationship_events "
            "(user_id, event_type, source_type, source_id, subject, object, payload_json, "
            "confidence, privacy, occurred_at, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, event_type, source_type, int(source_id), subject, obj,
             payload_json, float(confidence), privacy, occurred, expires_at, now),
        )
        if cur.rowcount:
            event_id = int(cur.lastrowid)
        elif refresh_on_conflict:
            row = db.conn.execute(
                "SELECT id FROM relationship_events WHERE user_id = ? AND event_type = ? "
                "AND source_type = ? AND source_id = ? AND status = 'active'",
                (user_id, event_type, source_type, int(source_id)),
            ).fetchone()
            if row is None:
                return None
            event_id = int(row["id"])
            db.conn.execute(
                "UPDATE relationship_events SET occurred_at = ?, payload_json = ?, "
                "expires_at = ? WHERE id = ?",
                (occurred, payload_json, expires_at, event_id),
            )
        else:
            return None
        if commit:
            db.conn.commit()
    return event_id


def active_events(
    user_id: str,
    *,
    event_type: str | None = None,
    limit: int = 5,
    within_days: int | None = None,
) -> list[dict]:
    """查询有效事件：active 且未过期；within_days 限定发生时间窗口。"""
    limit = max(1, min(20, int(limit)))
    sql = (
        "SELECT id, event_type, source_type, source_id, subject, object, payload_json, "
        "confidence, privacy, occurred_at, expires_at FROM relationship_events "
        "WHERE user_id = ? AND status = 'active' "
        "AND (expires_at IS NULL OR expires_at > ?)"
    )
    params: list[object] = [user_id, _now()]
    if event_type is not None:
        sql += " AND event_type = ?"
        params.append(event_type)
    if within_days is not None:
        cutoff = (datetime.now() - timedelta(days=int(within_days))).isoformat(timespec="seconds")
        sql += " AND occurred_at >= ?"
        params.append(cutoff)
    sql += " ORDER BY occurred_at DESC, id DESC LIMIT ?"
    params.append(limit)
    with db._lock:
        rows = db.conn.execute(sql, params).fetchall()
    events = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        events.append({
            "id": int(row["id"]),
            "event_type": row["event_type"],
            "source_type": row["source_type"],
            "source_id": int(row["source_id"]),
            "subject": row["subject"],
            "object": row["object"],
            "payload": payload,
            "occurred_at": row["occurred_at"],
            "expires_at": row["expires_at"],
        })
    return events


def invalidate_for_source(
    user_id: str, source_type: str, source_id: int, *, commit: bool = True
) -> int:
    """来源记录被删除时作废其事件，返回作废条数。"""
    with db._lock:
        cur = db.conn.execute(
            "UPDATE relationship_events SET status = 'forgotten' "
            "WHERE user_id = ? AND source_type = ? AND source_id = ? AND status = 'active'",
            (user_id, source_type, int(source_id)),
        )
        if commit:
            db.conn.commit()
    return cur.rowcount


def mark_corrected(user_id: str, event_id: int, *, commit: bool = True) -> bool:
    """纠正：事件确实发生过，但记录内容或状态需要修正。"""
    with db._lock:
        cur = db.conn.execute(
            "UPDATE relationship_events SET status = 'corrected' "
            "WHERE user_id = ? AND id = ? AND status = 'active'",
            (user_id, int(event_id)),
        )
        if commit:
            db.conn.commit()
    return bool(cur.rowcount)


# ---- 首批事件的生产者 ----


def record_promise_completed(user_id: str, promise: dict, *, commit: bool = True) -> int | None:
    """约定到点被跟进并标记完成（C6 → initiative.maybe_follow_up_promise）。"""
    content = str(promise.get("content") or "")[:200]
    return record(
        user_id,
        "promise_completed",
        "promise",
        int(promise["id"]),
        subject=user_id,
        obj=content,
        payload={"content": content, "follow_up": promise.get("follow_up") or ""},
        commit=commit,
    )


def refresh_important_date(user_id: str, date_row: dict, day: date, *, commit: bool = True) -> int | None:
    """特殊日子到来（每年重复的日子刷新同一条事件，不逐年堆积）。"""
    expires = (datetime.combine(day, datetime.min.time()) + timedelta(days=_DATE_EVENT_EXPIRY_DAYS)).isoformat(timespec="seconds")
    return record(
        user_id,
        "important_date",
        "important_date",
        int(date_row["id"]),
        subject=user_id,
        obj=str(date_row.get("label") or ""),
        payload={
            "label": date_row.get("label") or "",
            "kind": date_row.get("kind") or "other",
            "date": day.isoformat(),
        },
        occurred_at=f"{day.isoformat()}T00:00:00",
        expires_at=expires,
        commit=commit,
        refresh_on_conflict=True,
    )


def record_memory_corrected(
    user_id: str,
    fact_id: int,
    *,
    action: str,
    old_content: str = "",
    new_content: str = "",
    commit: bool = True,
) -> int | None:
    """用户改写或确认了一条记忆（管理页改写/冲突确认；对话内真删不在此列）。"""
    return record(
        user_id,
        "memory_corrected",
        "fact",
        int(fact_id),
        subject=user_id,
        obj=str(new_content or old_content or "")[:200],
        payload={"action": action, "old": old_content[:200], "new": new_content[:200]},
        commit=commit,
    )


# ---- 聊天内自然回忆（唯一的表达出口；不做主动推送）----


def event_recall(user_id: str, query: str) -> dict:
    """相关语境下返回已完成约定/特殊日子的自然回忆素材；无关话题返回空。

    返回 {"context": 注入文本, "sources": [解释快照用的来源描述]}。
    """
    empty = {"context": "", "sources": []}
    if not query:
        return empty
    promise_hit = bool(_PROMISE_CUE_RE.search(query))
    date_events = active_events(user_id, event_type="important_date", limit=_MAX_PAYLOAD_EVENTS)
    if not promise_hit and not date_events:
        return empty

    lines: list[str] = []
    sources: list[str] = []
    if promise_hit:
        for event in active_events(user_id, event_type="promise_completed",
                                   limit=_MAX_PAYLOAD_EVENTS, within_days=_EVENT_HORIZON_DAYS):
            content = event["payload"].get("content") or event["object"]
            if content:
                lines.append(f"- 你们完成过约定「{content}」（{event['occurred_at'][:10]}）")
                sources.append(f"约定事件：{content}")
    # 日期事件：通用日期词或直接点名某个日子，才算相关语境
    for event in date_events:
        label = event["payload"].get("label") or event["object"]
        if label and label in query:
            lines.append(f"- {label}（最近一次：{event['payload'].get('date', '')}）")
            sources.append(f"特殊日子：{label}")
    if not lines:
        return empty

    context = (
        "这些是关系事件库里真实发生过的事，可以在当下话题自然相关时提起：\n"
        + "\n".join(lines)
        + "\n只像记得这些事一样顺口带过，不要复述上面的原文，不要列清单；"
        "与当下话题无关就不要提。"
    )
    logger.debug("[关系事件] 回访素材：{} 条", len(lines))
    return {"context": context, "sources": sources}
