# -*- coding: utf-8 -*-
"""M5 未完成心事（pending_thoughts）：想问但时机不对的事，等一个自然的时机。

设计约束（docs/TECH-PLAN.md M2/M5）：
- 只携带叙事素材（她惦记的事），不携带可执行指令。
- 每条心事可追溯来源（activity / fact），来源消失即作废。
- Narrative Planner 决定"现在表达 / 延迟 / 放弃"：earliest_at 未到不表达、
  尝试次数用尽放弃、过期作废；表达永远走既有主动队列（额度/冷却/勿扰不变）。
- 克制：同一来源的心事一生只挂一次，不反复盘问用户。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .log import logger
from .userdb import db

_THOUGHT_KINDS = ("resume_reading", "confirm_memory", "goal_checkin")
_PAUSED_READING_DAYS = 3
_CONFIRM_MEMORY_HOURS = 2
_THOUGHT_TTL_DAYS = 7


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _expire_at() -> str:
    return (datetime.now() + timedelta(days=_THOUGHT_TTL_DAYS)).isoformat(timespec="seconds")


def _add(
    user_id: str,
    kind: str,
    source_type: str,
    source_id: int,
    content: str,
    *,
    earliest_at: str | None = None,
    priority: int = 5,
    commit: bool = True,
) -> int | None:
    """幂等写入：同一来源一生只挂一次心事。返回新挂上的 id，已存在返回 None。"""
    if kind not in _THOUGHT_KINDS:
        raise ValueError(f"未注册的心事类型：{kind}")
    with db._lock:
        cur = db.conn.execute(
            "INSERT OR IGNORE INTO pending_thoughts "
            "(user_id, kind, source_type, source_id, content, earliest_at, expires_at, "
            "priority, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, kind, source_type, int(source_id), content,
             earliest_at or _now(), _expire_at(), int(priority), _now()),
        )
        if commit:
            db.conn.commit()
    return int(cur.lastrowid) if cur.rowcount else None


def sync_pending_thoughts(user_id: str) -> int:
    """从真实信号同步心事（确定性生产者，幂等），返回新增条数。

    - resume_reading：有共读搁置超过 3 天 → 她惦记那本书。
    - confirm_memory：一天内纠偏过记忆 → 她想找时机确认现在记对了没。
    """
    added = 0
    now = _now()
    with db._lock:
        paused = db.conn.execute(
            "SELECT a.id, a.updated_at, d.filename FROM activities a "
            "JOIN kb_documents d ON d.id = a.document_id AND d.user_id = a.user_id "
            "WHERE a.user_id = ? AND a.kind = 'reading' AND a.status = 'paused' "
            "AND a.updated_at <= ?",
            (user_id, (datetime.now() - timedelta(days=_PAUSED_READING_DAYS)).isoformat(timespec="seconds")),
        ).fetchall()
        corrected = db.conn.execute(
            "SELECT source_id, payload_json, occurred_at FROM relationship_events "
            "WHERE user_id = ? AND event_type = 'memory_corrected' AND status = 'active' "
            "AND occurred_at >= ?",
            (user_id, (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")),
        ).fetchall()
        due_goals = db.conn.execute(
            "SELECT a.id, a.title, g.next_step, g.reminder_at FROM activities a "
            "JOIN activity_goals g ON g.activity_id = a.id AND g.user_id = a.user_id "
            "WHERE a.user_id = ? AND a.kind = 'goal' AND a.status IN ('active', 'paused') "
            "AND g.support_mode = 'reminder' AND g.reminder_at IS NOT NULL AND g.reminder_at <= ?",
            (user_id, now),
        ).fetchall()
    for row in paused:
        earliest = (
            datetime.fromisoformat(row["updated_at"]) + timedelta(days=_PAUSED_READING_DAYS)
        ).isoformat(timespec="seconds")
        if _add(
            user_id, "resume_reading", "activity", int(row["id"]),
            f"你们有一本读到一半搁下的《{row['filename']}》，她有点想知道后来读到哪儿了",
            earliest_at=earliest, priority=4, commit=False,
        ):
            added += 1
    for row in corrected:
        earliest = (
            datetime.fromisoformat(row["occurred_at"]) + timedelta(hours=_CONFIRM_MEMORY_HOURS)
        ).isoformat(timespec="seconds")
        if _add(
            user_id, "confirm_memory", "fact", int(row["source_id"]),
            "她刚改过一条记你的事，想找个自然的时机确认这次记对了没",
            earliest_at=earliest, priority=3, commit=False,
        ):
            added += 1
    for row in due_goals:
        if _add(
            user_id, "goal_checkin", "activity", int(row["id"]),
            f"对方请你在合适时轻轻问一次共同目标「{row['title']}」；下一小步是「{row['next_step']}」",
            earliest_at=row["reminder_at"], priority=5, commit=False,
        ):
            added += 1
    if added:
        db.conn.commit()
        logger.info("[心事] 挂上 {} 条未完成心事：{}", added, user_id)
    return added


def due_thoughts(user_id: str, limit: int = 3) -> list[dict]:
    """到点、未过期、还有尝试余地的 pending 心事，按优先级排序。"""
    now = _now()
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM pending_thoughts WHERE user_id = ? AND status = 'pending' "
            "AND earliest_at <= ? AND (expires_at IS NULL OR expires_at > ?) "
            "AND attempts < max_attempts "
            "ORDER BY priority, earliest_at, id LIMIT ?",
            (user_id, now, now, max(1, min(10, int(limit)))),
        ).fetchall()
    return [dict(row) for row in rows]


def next_thought_for_stage(user_id: str, stage: str) -> dict | None:
    """Narrative Planner 的阶段门控：初识只允许读书跟进这类非私人化心事。"""
    sync_pending_thoughts(user_id)
    allowed = {"resume_reading"} if stage == "初识" else set(_THOUGHT_KINDS)
    for thought in due_thoughts(user_id):
        if thought["kind"] in allowed:
            return thought
    return None


def record_attempt(thought_id: int, *, commit: bool = True) -> None:
    with db._lock:
        db.conn.execute(
            "UPDATE pending_thoughts SET attempts = attempts + 1, last_attempt_at = ? "
            "WHERE id = ?",
            (_now(), int(thought_id)),
        )
        if commit:
            db.conn.commit()


def mark_expressed(thought_id: int, *, commit: bool = True) -> None:
    with db._lock:
        db.conn.execute(
            "UPDATE pending_thoughts SET status = 'expressed' WHERE id = ?",
            (int(thought_id),),
        )
        if commit:
            db.conn.commit()


def dismiss_thought(user_id: str, thought_id: int) -> bool:
    """用户主权：不想让她惦记这件事，直接放下。"""
    with db._lock:
        cur = db.conn.execute(
            "UPDATE pending_thoughts SET status = 'dismissed' "
            "WHERE user_id = ? AND id = ? AND status = 'pending'",
            (user_id, int(thought_id)),
        )
        db.conn.commit()
    return bool(cur.rowcount)


def stats(user_id: str) -> dict:
    """可观测性：心事池的状态分布（M5 退出标准：可观测）。"""
    with db._lock:
        rows = db.conn.execute(
            "SELECT status, COUNT(*) AS n FROM pending_thoughts WHERE user_id = ? "
            "GROUP BY status",
            (user_id,),
        ).fetchall()
        total = db.conn.execute(
            "SELECT COUNT(*) FROM pending_thoughts WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
    counts = {row["status"]: int(row["n"]) for row in rows}
    return {
        "total": int(total),
        "pending": counts.get("pending", 0),
        "expressed": counts.get("expressed", 0),
        "dismissed": counts.get("dismissed", 0),
    }


def forget_thoughts_for_source(user_id: str, source_type: str, source_id: int) -> None:
    """来源消失 → 心事作废（不留幽灵惦记）。"""
    with db._lock:
        db.conn.execute(
            "UPDATE pending_thoughts SET status = 'dismissed' "
            "WHERE user_id = ? AND source_type = ? AND source_id = ? AND status = 'pending'",
            (user_id, source_type, int(source_id)),
        )
        db.conn.commit()
