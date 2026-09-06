# -*- coding: utf-8 -*-
"""M8「写给未来的我们」：用户亲手写给未来双方的信。

设计边界（TECH-PLAN M8 第一垂直切片）：
- 正文只来自用户显式输入：不调用 LLM、不让菟菚代写、不生成或编造共同经历。
- 锁定态绝不泄露：列表对未打开的信只返回元数据与条件，body 不出后端。
- 三类解锁：date（到点）/ goal（共同目标完成）/ event（写信之后发生的真实
  关系事件，只认完成/修复类白名单，不接受任意字符串）。
- ready 是查询时的确定性计算态；条件达成允许惰性落账，但必须幂等。
- 拆信是显式动作：open 时写 opened_at，并幂等创建 future_letter artifact
  （信在「我们的角落」的可追溯形态）。故意不写关系事件：事件→信、信的
  unlocked_by_event_id→事件会构成导出恢复不支持的跨表引用环。
- 删除是真删除：信与其 artifact 一并清除，并作废关联关系事件（当前无注册
  事件时是防御性空操作），不留幽灵数据。
"""
from __future__ import annotations

from datetime import datetime

from . import relationship_events
from .userdb import db

_MAX_TITLE = 60
_MAX_BODY = 4000
_UNLOCK_TYPES = {"date", "goal", "event"}

# 事件解锁白名单：从 EVENT_TYPES 里挑的完成/修复类。日期提醒类（important_date）
# 与本功能自身产生的 future_letter_opened 不在列——前者不是完成/修复，
# 后者会造成「等另一封信被拆」的递归等待。
EVENT_UNLOCK_TYPES: dict[str, str] = {
    "reading_finished": "一起读完一份文档",
    "promise_completed": "一条约定被完成",
    "focus_finished": "一段专注陪伴完成",
    "goal_completed": "一个共同目标完成",
    "story_finished": "一个共同故事收笔",
    "list_completed": "一份共同清单收列",
    "memory_corrected": "一段记忆被纠正或确认",
}


class FutureLetterError(ValueError):
    """未来信件的预期业务错误。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _clean(value: str, maximum: int, label: str, *, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise FutureLetterError(f"{label}不能为空")
    if len(result) > maximum:
        raise FutureLetterError(f"{label}最多 {maximum} 字")
    return result


def _goal_locked(user_id: str, goal_id: int) -> dict | None:
    row = db.conn.execute(
        "SELECT id, title, status FROM activities "
        "WHERE id = ? AND user_id = ? AND kind = 'goal'",
        (int(goal_id), user_id),
    ).fetchone()
    return dict(row) if row else None


def _condition_met_locked(user_id: str, row) -> tuple[bool, int | None]:
    """确定性判定信是否可拆；event 类型同时返回首次匹配的事件 id。"""
    if row["unlock_type"] == "date":
        return bool(row["unlock_at"]) and str(row["unlock_at"]) <= _now(), None
    if row["unlock_type"] == "goal":
        goal = _goal_locked(user_id, int(row["goal_id"]))
        return goal is not None and goal["status"] == "completed", None
    if row["unlock_type"] == "event":
        hit = db.conn.execute(
            "SELECT id FROM relationship_events WHERE user_id = ? AND event_type = ? "
            "AND status = 'active' AND occurred_at > ? "
            "ORDER BY occurred_at ASC, id ASC LIMIT 1",
            (user_id, row["event_type"], row["created_at"]),
        ).fetchone()
        return hit is not None, (int(hit["id"]) if hit else None)
    return False, None


def _settle_locked(user_id: str, row) -> bool:
    """条件已达成时惰性落账（unlocked_at / 首次匹配事件 id）；幂等。返回是否有写入。"""
    if row["status"] != "sealed" or row["unlocked_at"]:
        return False
    met, event_id = _condition_met_locked(user_id, row)
    if not met:
        return False
    now = _now()
    db.conn.execute(
        "UPDATE future_letters SET unlocked_at = ?, unlocked_by_event_id = ?, updated_at = ? "
        "WHERE id = ? AND user_id = ? AND unlocked_at IS NULL",
        (now, event_id, now, int(row["id"]), user_id),
    )
    return True


def _view_locked(row, *, include_body: bool) -> dict:
    opened = row["status"] == "opened"
    goal_title = ""
    if row["goal_id"] is not None:
        goal = _goal_locked(_row_user(row), int(row["goal_id"]))
        goal_title = str(goal["title"]) if goal else ""
    view: dict = {
        "id": int(row["id"]),
        "title": row["title"] or "",
        "unlock_type": row["unlock_type"],
        "unlock_at": row["unlock_at"],
        "goal_id": int(row["goal_id"]) if row["goal_id"] is not None else None,
        "goal_title": goal_title,
        "event_type": row["event_type"],
        "status": "opened" if opened else ("ready" if row["unlocked_at"] else "sealed"),
        "unlocked_at": row["unlocked_at"],
        "unlocked_by_event_id": int(row["unlocked_by_event_id"])
        if row["unlocked_by_event_id"] is not None else None,
        "created_at": row["created_at"],
        "opened_at": row["opened_at"],
    }
    if include_body:
        view["body"] = row["body"]
    return view


def _letter_row_locked(user_id: str, letter_id: int):
    return db.conn.execute(
        "SELECT * FROM future_letters WHERE id = ? AND user_id = ?",
        (int(letter_id), user_id),
    ).fetchone()


def _row_user(row) -> str:
    return str(row["user_id"])


def create_letter(
    user_id: str,
    body: str,
    unlock_type: str,
    *,
    title: str = "",
    unlock_at: str | None = None,
    goal_id: int | None = None,
    event_type: str | None = None,
) -> dict:
    """用户亲手写一封信。正文不做任何改写，原样保存。"""
    body = _clean(body, _MAX_BODY, "正文", required=True)
    title = _clean(title, _MAX_TITLE, "标题")
    with db._lock:
        if unlock_type not in _UNLOCK_TYPES:
            raise FutureLetterError("解锁条件类型只能是 date / goal / event")
        if unlock_type == "date":
            if not unlock_at:
                raise FutureLetterError("请选择解锁时间")
            try:
                parsed = datetime.fromisoformat(str(unlock_at))
            except ValueError as exc:
                raise FutureLetterError("解锁时间格式不正确") from exc
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                raise FutureLetterError("解锁时间请使用本地时间，不要附带时区")
            if parsed <= datetime.now():
                raise FutureLetterError("解锁时间需要晚于现在")
            unlock_at = parsed.isoformat(timespec="seconds")
            goal_id = None
            event_type = None
        elif unlock_type == "goal":
            if not goal_id:
                raise FutureLetterError("请选择一个共同目标")
            goal = _goal_locked(user_id, int(goal_id))
            if goal is None:
                raise FutureLetterError("共同目标不存在")
            if goal["status"] not in ("active", "paused"):
                raise FutureLetterError("这个目标已经结束，解锁会立刻发生，请选一个还在进行的目标")
            goal_id = int(goal_id)
            unlock_at = None
            event_type = None
        else:
            if event_type not in EVENT_UNLOCK_TYPES:
                raise FutureLetterError("解锁事件类型不在可选范围内")
            goal_id = None
            unlock_at = None
        now = _now()
        cur = db.conn.execute(
            "INSERT INTO future_letters "
            "(user_id, title, body, unlock_type, unlock_at, goal_id, event_type, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'sealed', ?, ?)",
            (user_id, title, body, unlock_type, unlock_at, goal_id, event_type, now, now),
        )
        letter_id = int(cur.lastrowid)
        row = _letter_row_locked(user_id, letter_id)
        db.conn.commit()
        return _view_locked(row, include_body=False)


def list_letters(user_id: str) -> list[dict]:
    """列出全部信件（含锁定态元数据）。锁定信绝不返回正文。"""
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM future_letters WHERE user_id = ? "
            "ORDER BY created_at DESC, id DESC",
            (user_id,),
        ).fetchall()
        views = []
        settled = False
        for row in rows:
            if row["status"] == "sealed":
                settled = _settle_locked(user_id, row) or settled
            row = _letter_row_locked(user_id, int(row["id"])) or row
            views.append(_view_locked(row, include_body=False))
        if settled:
            db.conn.commit()
    return views


def open_letter(user_id: str, letter_id: int) -> dict:
    """显式拆信：条件达成才能拆；幂等；拆开后正文可见并落 artifact 与事件。"""
    with db._lock:
        row = _letter_row_locked(user_id, letter_id)
        if row is None:
            raise FutureLetterError("这封信不存在")
        if row["status"] != "opened":
            _settle_locked(user_id, row)
            fresh = _letter_row_locked(user_id, letter_id)
            if fresh is None or not fresh["unlocked_at"]:
                raise FutureLetterError("解锁条件还没满足，这封信还打不开")
            now = _now()
            db.conn.execute(
                "UPDATE future_letters SET status = 'opened', opened_at = ?, updated_at = ? "
                "WHERE id = ? AND user_id = ? AND status = 'sealed'",
                (now, now, int(letter_id), user_id),
            )
            title = fresh["title"] or "一封写给未来的信"
            db.conn.execute(
                "INSERT OR IGNORE INTO artifacts "
                "(user_id, artifact_type, source_type, source_id, title, content, version, "
                "created_at, updated_at) VALUES (?, 'future_letter', 'future_letter', ?, ?, ?, 1, ?, ?)",
                (user_id, int(letter_id), title, fresh["body"], now, now),
            )
            db.conn.commit()
        row = _letter_row_locked(user_id, letter_id)
        return _view_locked(row, include_body=True)


def delete_letter(user_id: str, letter_id: int) -> bool:
    """真删除：信与拆信 artifact 一并清除，关联关系事件作废。"""
    with db._lock:
        cur = db.conn.execute(
            "DELETE FROM future_letters WHERE id = ? AND user_id = ?",
            (int(letter_id), user_id),
        )
        if not cur.rowcount:
            db.conn.commit()
            return False
        db.conn.execute(
            "DELETE FROM artifacts WHERE user_id = ? AND artifact_type = 'future_letter' "
            "AND source_type = 'future_letter' AND source_id = ?",
            (user_id, int(letter_id)),
        )
        relationship_events.invalidate_for_source(
            user_id, "future_letter", int(letter_id), commit=False
        )
        db.conn.commit()
        return True


def letter_options(user_id: str) -> dict:
    """写信表单的候选：还在进行的共同目标 + 事件解锁白名单（带中文说明）。"""
    with db._lock:
        rows = db.conn.execute(
            "SELECT id, title, status FROM activities WHERE user_id = ? AND kind = 'goal' "
            "AND status IN ('active', 'paused') ORDER BY updated_at DESC, id DESC LIMIT 50",
            (user_id,),
        ).fetchall()
    return {
        "goal_options": [
            {"id": int(r["id"]), "title": r["title"], "status": r["status"]} for r in rows
        ],
        "event_types": [
            {"type": event_type, "label": label}
            for event_type, label in EVENT_UNLOCK_TYPES.items()
        ],
    }
