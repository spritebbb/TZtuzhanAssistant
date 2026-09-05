# -*- coding: utf-8 -*-
"""M3.3 共同目标：把动机、下一小步和真实进展留在可恢复的活动里。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .activities import ActivityError, pause_all_active_locked
from .userdb import db

_MAX_TITLE = 120
_MAX_MOTIVATION = 1_000
_MAX_STEP = 500
_MAX_PROGRESS = 1_000
_SUPPORT_MODES = {"companion", "reminder"}
_GOAL_CUE_RE = re.compile(r"目标|计划|进展|下一步|做到|完成|坚持|打卡|陪我|提醒我|一起做")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _clean(value: str, maximum: int, label: str, *, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise ActivityError(f"{label}不能为空")
    if len(result) > maximum:
        raise ActivityError(f"{label}最多 {maximum} 字")
    return result


def _reminder_value(mode: str, value: str | None) -> str | None:
    if mode == "companion":
        return None
    if not value:
        return (datetime.now() + timedelta(days=3)).isoformat(timespec="seconds")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ActivityError("提醒时间格式不正确") from exc
    if parsed <= datetime.now():
        raise ActivityError("提醒时间需要晚于现在")
    return parsed.isoformat(timespec="seconds")


def _progress_locked(user_id: str, activity_id: int) -> list[dict]:
    rows = db.conn.execute(
        "SELECT id, content, percent, next_step, ts FROM goal_progress "
        "WHERE user_id = ? AND activity_id = ? ORDER BY ts, id",
        (user_id, activity_id),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "content": row["content"],
            "percent": row["percent"],
            "next_step": row["next_step"],
            "ts": row["ts"],
        }
        for row in rows
    ]


def _review_locked(user_id: str, activity_id: int) -> str:
    row = db.conn.execute(
        "SELECT content FROM artifacts WHERE user_id = ? AND artifact_type = 'goal_review' "
        "AND source_type = 'activity' AND source_id = ? AND status = 'active'",
        (user_id, activity_id),
    ).fetchone()
    return str(row["content"]) if row else ""


def _detail_locked(user_id: str, activity_id: int) -> dict | None:
    row = db.conn.execute(
        "SELECT a.id, a.kind, a.title, a.status, a.created_at, a.updated_at, a.completed_at, "
        "g.motivation, g.next_step, g.support_mode, g.reminder_at "
        "FROM activities a JOIN activity_goals g ON g.activity_id = a.id AND g.user_id = a.user_id "
        "WHERE a.user_id = ? AND a.id = ? AND a.kind = 'goal'",
        (user_id, activity_id),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["progress_entries"] = _progress_locked(user_id, activity_id)
    result["review"] = _review_locked(user_id, activity_id)
    return result


def get_goal(user_id: str, activity_id: int) -> dict | None:
    with db._lock:
        return _detail_locked(user_id, activity_id)


def list_goals(user_id: str, limit: int = 30) -> list[dict]:
    with db._lock:
        ids = [
            int(row["id"])
            for row in db.conn.execute(
                "SELECT id FROM activities WHERE user_id = ? AND kind = 'goal' "
                "ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END, "
                "updated_at DESC, id DESC LIMIT ?",
                (user_id, max(1, min(50, int(limit)))),
            ).fetchall()
        ]
        return [item for goal_id in ids if (item := _detail_locked(user_id, goal_id))]


def start_goal(
    user_id: str,
    title: str,
    motivation: str,
    next_step: str,
    support_mode: str = "companion",
    reminder_at: str | None = None,
) -> dict:
    title = _clean(title, _MAX_TITLE, "目标", required=True)
    motivation = _clean(motivation, _MAX_MOTIVATION, "动机")
    next_step = _clean(next_step, _MAX_STEP, "下一小步", required=True)
    if support_mode not in _SUPPORT_MODES:
        raise ActivityError("陪伴方式只能是 companion 或 reminder")
    reminder_at = _reminder_value(support_mode, reminder_at)
    now = _now()
    with db._lock:
        pause_all_active_locked(user_id, now)
        cur = db.conn.execute(
            "INSERT INTO activities "
            "(user_id, kind, document_id, title, status, position, created_at, updated_at) "
            "VALUES (?, 'goal', 0, ?, 'active', 0, ?, ?)",
            (user_id, title, now, now),
        )
        activity_id = int(cur.lastrowid)
        db.conn.execute(
            "INSERT INTO activity_goals "
            "(activity_id, user_id, motivation, next_step, support_mode, reminder_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (activity_id, user_id, motivation, next_step, support_mode, reminder_at, now, now),
        )
        db.conn.commit()
        return _detail_locked(user_id, activity_id)  # type: ignore[return-value]


def update_goal(
    user_id: str,
    activity_id: int,
    *,
    motivation: str | None = None,
    next_step: str | None = None,
    support_mode: str | None = None,
    reminder_at: str | None = None,
) -> dict:
    with db._lock:
        detail = _detail_locked(user_id, activity_id)
        if detail is None:
            raise ActivityError("共同目标不存在")
        if detail["status"] in {"completed", "cancelled"}:
            raise ActivityError("已经结束的目标不能再修改")
        mode = support_mode or detail["support_mode"]
        if mode not in _SUPPORT_MODES:
            raise ActivityError("陪伴方式只能是 companion 或 reminder")
        if mode == "companion":
            resolved_reminder = None
        elif reminder_at is not None:
            resolved_reminder = _reminder_value(mode, reminder_at)
        elif support_mode == "reminder" and detail["support_mode"] != "reminder":
            resolved_reminder = _reminder_value(mode, None)
        else:
            resolved_reminder = detail["reminder_at"]
        values = (
            _clean(motivation, _MAX_MOTIVATION, "动机") if motivation is not None else detail["motivation"],
            _clean(next_step, _MAX_STEP, "下一小步", required=True) if next_step is not None else detail["next_step"],
            mode,
            resolved_reminder,
            _now(),
            activity_id,
            user_id,
        )
        db.conn.execute(
            "UPDATE activity_goals SET motivation = ?, next_step = ?, support_mode = ?, "
            "reminder_at = ?, updated_at = ? WHERE activity_id = ? AND user_id = ?",
            values,
        )
        db.conn.execute(
            "UPDATE activities SET updated_at = ? WHERE id = ? AND user_id = ?",
            (values[4], activity_id, user_id),
        )
        db.conn.commit()
        return _detail_locked(user_id, activity_id)  # type: ignore[return-value]


def add_progress(
    user_id: str,
    activity_id: int,
    content: str,
    *,
    percent: int | None = None,
    next_step: str = "",
) -> dict:
    content = _clean(content, _MAX_PROGRESS, "实际进展", required=True)
    next_step = _clean(next_step, _MAX_STEP, "下一小步")
    if percent is not None and not 0 <= int(percent) <= 100:
        raise ActivityError("进度百分比需要在 0–100 之间")
    now = _now()
    with db._lock:
        detail = _detail_locked(user_id, activity_id)
        if detail is None:
            raise ActivityError("共同目标不存在")
        if detail["status"] in {"completed", "cancelled"}:
            raise ActivityError("已经结束的目标不能再记录进展")
        db.conn.execute(
            "INSERT INTO goal_progress (user_id, activity_id, content, percent, next_step, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, activity_id, content, percent, next_step, now),
        )
        if next_step:
            db.conn.execute(
                "UPDATE activity_goals SET next_step = ?, updated_at = ? "
                "WHERE activity_id = ? AND user_id = ?",
                (next_step, now, activity_id, user_id),
            )
        db.conn.execute(
            "UPDATE activities SET updated_at = ? WHERE id = ? AND user_id = ?",
            (now, activity_id, user_id),
        )
        db.conn.commit()
        return _detail_locked(user_id, activity_id)  # type: ignore[return-value]


def _change_status(user_id: str, activity_id: int, target: str) -> dict:
    now = _now()
    with db._lock:
        detail = _detail_locked(user_id, activity_id)
        if detail is None:
            raise ActivityError("共同目标不存在")
        if detail["status"] in {"completed", "cancelled"}:
            raise ActivityError("这个目标已经结束")
        expected = "paused" if target == "active" else "active"
        if target in {"active", "paused"} and detail["status"] != expected:
            action = "继续" if target == "active" else "暂停"
            raise ActivityError(f"当前状态不能{action}")
        if target == "active":
            pause_all_active_locked(user_id, now)
        db.conn.execute(
            "UPDATE activities SET status = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (target, now, activity_id, user_id),
        )
        db.conn.commit()
        return _detail_locked(user_id, activity_id)  # type: ignore[return-value]


def pause_goal(user_id: str, activity_id: int) -> dict:
    return _change_status(user_id, activity_id, "paused")


def resume_goal(user_id: str, activity_id: int) -> dict:
    return _change_status(user_id, activity_id, "active")


def cancel_goal(user_id: str, activity_id: int) -> dict:
    result = _change_status(user_id, activity_id, "cancelled")
    from .pending_thoughts import forget_thoughts_for_source

    forget_thoughts_for_source(user_id, "activity", activity_id)
    return result


def _compile_review(detail: dict) -> str:
    lines = [f"《{detail['title']}》的过程回顾"]
    if detail["motivation"]:
        lines.append(f"动机：{detail['motivation']}")
    entries = detail["progress_entries"]
    if entries:
        lines.append("实际留下的进展：")
        for item in entries:
            percent = f"（{item['percent']}%）" if item["percent"] is not None else ""
            lines.append(f"- {item['ts'][:10]}{percent}：{item['content']}")
    else:
        lines.append("实际留下的进展：没有单独记录；完成本身是唯一确定的事实。")
    return "\n".join(lines)


def complete_goal(user_id: str, activity_id: int, *, create_artifact: bool = True) -> dict:
    now = _now()
    with db._lock:
        detail = _detail_locked(user_id, activity_id)
        if detail is None:
            raise ActivityError("共同目标不存在")
        if detail["status"] in {"completed", "cancelled"}:
            raise ActivityError("已经结束的目标不能再次完成")
        db.conn.execute(
            "UPDATE activities SET status = 'completed', updated_at = ?, completed_at = ? "
            "WHERE id = ? AND user_id = ?",
            (now, now, activity_id, user_id),
        )
        if create_artifact:
            review = _compile_review(detail)
            db.conn.execute(
                "INSERT INTO artifacts "
                "(user_id, artifact_type, source_type, source_id, title, content, version, created_at, updated_at) "
                "VALUES (?, 'goal_review', 'activity', ?, ?, ?, 1, ?, ?) "
                "ON CONFLICT(user_id, artifact_type, source_id) DO UPDATE SET "
                "content = excluded.content, version = artifacts.version + 1, updated_at = excluded.updated_at",
                (user_id, activity_id, f"《{detail['title']}》过程回顾", review, now, now),
            )
        from .relationship_events import record

        record(
            user_id,
            "goal_completed",
            "activity",
            activity_id,
            subject=user_id,
            obj=detail["title"],
            payload={"title": detail["title"], "progress_count": len(detail["progress_entries"])},
            occurred_at=now,
            commit=False,
        )
        db.conn.commit()
    from .pending_thoughts import forget_thoughts_for_source

    forget_thoughts_for_source(user_id, "activity", activity_id)
    return get_goal(user_id, activity_id)  # type: ignore[return-value]


def export_markdown(user_id: str, activity_id: int) -> str:
    detail = get_goal(user_id, activity_id)
    if detail is None:
        raise ActivityError("共同目标不存在")
    status = {"active": "进行中", "paused": "已暂停", "completed": "已完成", "cancelled": "已取消"}.get(
        detail["status"], detail["status"]
    )
    lines = [f"# {detail['title']}", "", f"- 状态：{status}", f"- 动机：{detail['motivation'] or '未填写'}", f"- 下一小步：{detail['next_step']}", "", "## 实际进展", ""]
    if detail["progress_entries"]:
        for item in detail["progress_entries"]:
            percent = f" · {item['percent']}%" if item["percent"] is not None else ""
            lines.append(f"- {item['ts'][:10]}{percent}：{item['content']}")
    else:
        lines.append("- 暂无进展记录")
    if detail["review"]:
        lines.extend(["", "## 过程回顾", "", detail["review"]])
    return "\n".join(lines) + "\n"


def goal_context(user_id: str, query: str) -> str:
    """只有用户在谈目标/计划时才注入，普通聊天零污染。"""
    if not query or not _GOAL_CUE_RE.search(query):
        return ""
    with db._lock:
        row = db.conn.execute(
            "SELECT id FROM activities WHERE user_id = ? AND kind = 'goal' "
            "AND status IN ('active', 'paused') ORDER BY updated_at DESC, id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        detail = _detail_locked(user_id, int(row["id"])) if row else None
    if not detail:
        return ""
    latest = detail["progress_entries"][-1]["content"] if detail["progress_entries"] else "还没有进展记录"
    boundary = (
        "对方选择的是陪做模式：只回应当下请求，不主动催促。"
        if detail["support_mode"] == "companion"
        else "对方选择过一次提醒；提醒仍由统一主动策略控制，此处不要因为看到目标而催促。"
    )
    return (
        "你们有一个真实存在的共同目标：\n"
        f"- 目标：{detail['title']}\n- 动机：{detail['motivation'] or '未填写'}\n"
        f"- 下一小步：{detail['next_step']}\n- 最近进展：{latest}\n"
        f"{boundary} 不评价效率，不虚构未记录的进展；只在当前话题相关时自然回应。"
    )
