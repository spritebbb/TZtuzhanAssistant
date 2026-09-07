"""P2-06 久别重逢三段式：真实离线来源、可跳过回应、日记收束。"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from .userdb import db

NARRATIVE_VERSION = 1
ARC_TTL_DAYS = 7
RATE_LIMIT_DAYS = 7

_NEW_TOPIC_RE = re.compile(
    r"^(?:对了|另外|换个话题|说正事|帮我|请问|我想问|接下来|现在帮我)"
)


def _now() -> datetime:
    return datetime.now()


def _as_dict(row) -> dict | None:
    return dict(row) if row is not None else None


def _bucket(gap_hours: float) -> str:
    if gap_hours < 24:
        return "same_day"
    if gap_hours < 72:
        return "few_days"
    if gap_hours < 24 * 14:
        return "weeks"
    return "long_absence"


def _expire_due(user_id: str, now: datetime) -> None:
    stamp = now.isoformat(timespec="seconds")
    with db._lock:
        db.conn.execute(
            "UPDATE reunion_arcs SET state='closed',updated_at=? WHERE user_id=? "
            "AND state IN ('pending','offered') AND NOT EXISTS ("
            "SELECT 1 FROM character_life_events e "
            "WHERE e.id=reunion_arcs.source_snapshot_id AND e.user_id=reunion_arcs.user_id)",
            (stamp, user_id),
        )
        db.conn.execute(
            "UPDATE reunion_arcs SET state='expired', updated_at=? "
            "WHERE user_id=? AND state IN ('pending','offered') AND expires_at<=?",
            (stamp, user_id, stamp),
        )
        db.conn.commit()


def prepare_reunion(
    user_id: str,
    gap_hours: float,
    *,
    absent_since: datetime,
    now: datetime | None = None,
) -> dict | None:
    """为久别窗口建立唯一 pending 弧；没有真实离线生活事件就不建立。"""
    now = now or _now()
    _expire_due(user_id, now)
    cutoff = (now - timedelta(days=RATE_LIMIT_DAYS)).isoformat(timespec="seconds")
    with db._lock:
        recent = db.conn.execute(
            "SELECT 1 FROM reunion_arcs WHERE user_id=? AND created_at>=? LIMIT 1",
            (user_id, cutoff),
        ).fetchone()
        if recent is not None:
            return None
        source = db.conn.execute(
            "SELECT id,payload_json FROM character_life_events WHERE user_id=? "
            "AND namespace='character_fiction' AND occurred_at>=? AND occurred_at<=? "
            "AND computed_at<=? ORDER BY occurred_at DESC,id DESC LIMIT 1",
            (
                user_id,
                absent_since.isoformat(timespec="seconds"),
                now.isoformat(timespec="seconds"),
                now.isoformat(timespec="seconds"),
            ),
        ).fetchone()
        if source is None:
            return None
        try:
            payload = json.loads(source["payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        if not str(payload.get("description") or "").strip():
            return None
        stamp = now.isoformat(timespec="seconds")
        expires = (now + timedelta(days=ARC_TTL_DAYS)).isoformat(timespec="seconds")
        cur = db.conn.execute(
            "INSERT OR IGNORE INTO reunion_arcs "
            "(user_id,absence_bucket,source_snapshot_id,state,narrative_version,"
            "created_at,updated_at,expires_at) VALUES (?,?,?,'pending',?,?,?,?)",
            (user_id, _bucket(gap_hours), int(source["id"]), NARRATIVE_VERSION,
             stamp, stamp, expires),
        )
        db.conn.commit()
        if not cur.rowcount:
            return None
        return _as_dict(db.conn.execute(
            "SELECT * FROM reunion_arcs WHERE id=?", (int(cur.lastrowid),)
        ).fetchone())


def prompt_hint(user_id: str, arc_id: int) -> str:
    """从弧引用的结构化生活事件生成受约束提示，不读取用户离线经历。"""
    with db._lock:
        row = db.conn.execute(
            "SELECT r.state,e.payload_json FROM reunion_arcs r "
            "JOIN character_life_events e ON e.id=r.source_snapshot_id AND e.user_id=r.user_id "
            "WHERE r.id=? AND r.user_id=?",
            (int(arc_id), user_id),
        ).fetchone()
    if row is None or row["state"] != "pending":
        return ""
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    detail = str(payload.get("description") or "").strip()[:160]
    if not detail:
        return ""
    return (
        "久别期间确实留下的一条角色生活记录：" + detail + "。"
        "开场最多180字，只从她自己的视角自然带过；不要追问对方去了哪里或为什么没来，"
        "不要制造负罪感，也不要把角色生活说成现实世界可验证的经历。"
    )


def mark_offered(user_id: str, arc_id: int, text: str, *, now: datetime | None = None) -> int | None:
    """问候实际投递后，原子写入真实消息并将 pending 推进到 offered。"""
    now = now or _now()
    stamp = now.isoformat(timespec="seconds")
    with db._lock:
        row = db.conn.execute(
            "SELECT state,source_snapshot_id FROM reunion_arcs WHERE id=? AND user_id=?",
            (int(arc_id), user_id),
        ).fetchone()
        if row is None or row["state"] != "pending":
            return None
        source = db.conn.execute(
            "SELECT 1 FROM character_life_events WHERE id=? AND user_id=?",
            (int(row["source_snapshot_id"]), user_id),
        ).fetchone()
        if source is None:
            db.conn.execute(
                "UPDATE reunion_arcs SET state='closed',updated_at=? WHERE id=?",
                (stamp, int(arc_id)),
            )
            db.conn.commit()
            return None
        cur = db.conn.execute(
            "INSERT INTO messages(user_id,role,content,ts) VALUES (?,'assistant',?,?)",
            (user_id, str(text)[:1000], stamp),
        )
        message_id = int(cur.lastrowid)
        changed = db.conn.execute(
            "UPDATE reunion_arcs SET state='offered',offered_message_id=?,updated_at=? "
            "WHERE id=? AND user_id=? AND state='pending'",
            (message_id, stamp, int(arc_id), user_id),
        )
        if changed.rowcount != 1:
            db.conn.rollback()
            return None
        db.conn.commit()
        return message_id


def observe_user_turn(user_id: str, message_id: int, text: str, *, now: datetime | None = None) -> str | None:
    """第一条后续用户消息决定 responded 或 closed；永不阻塞正常对话。"""
    now = now or _now()
    _expire_due(user_id, now)
    with db._lock:
        arc = db.conn.execute(
            "SELECT r.* FROM reunion_arcs r JOIN character_life_events e "
            "ON e.id=r.source_snapshot_id AND e.user_id=r.user_id "
            "WHERE r.user_id=? AND r.state='offered' AND r.expires_at>? "
            "ORDER BY r.id DESC LIMIT 1",
            (user_id, now.isoformat(timespec="seconds")),
        ).fetchone()
        if arc is None:
            return None
        state = "closed" if _NEW_TOPIC_RE.search((text or "").strip()) else "responded"
        db.conn.execute(
            "UPDATE reunion_arcs SET state=?,response_message_id=?,updated_at=? "
            "WHERE id=? AND state='offered'",
            (state, int(message_id), now.isoformat(timespec="seconds"), int(arc["id"])),
        )
        db.conn.commit()
        return state


def close_after_daily(user_id: str, day: date, *, now: datetime | None = None) -> int:
    """日记成功消费当天真实回应后收束弧，不额外触发研究生成。"""
    now = now or _now()
    stamp = now.isoformat(timespec="seconds")
    with db._lock:
        cur = db.conn.execute(
            "UPDATE reunion_arcs SET state='closed',updated_at=? "
            "WHERE user_id=? AND state='responded' AND response_message_id IN "
            "(SELECT id FROM messages WHERE user_id=? AND date(ts)=?)",
            (stamp, user_id, user_id, day.isoformat()),
        )
        db.conn.commit()
        return int(cur.rowcount)


def close_arc(user_id: str, arc_id: int, *, now: datetime | None = None) -> bool:
    """生成过时、投递失败或来源失效时安静关闭候选。"""
    now = now or _now()
    with db._lock:
        cur = db.conn.execute(
            "UPDATE reunion_arcs SET state='closed',updated_at=? "
            "WHERE id=? AND user_id=? AND state IN ('pending','offered')",
            (now.isoformat(timespec="seconds"), int(arc_id), user_id),
        )
        db.conn.commit()
        return cur.rowcount == 1


def get_arc(user_id: str, arc_id: int) -> dict | None:
    with db._lock:
        return _as_dict(db.conn.execute(
            "SELECT * FROM reunion_arcs WHERE id=? AND user_id=?",
            (int(arc_id), user_id),
        ).fetchone())
