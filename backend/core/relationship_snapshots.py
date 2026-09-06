# -*- coding: utf-8 -*-
"""M8 第二垂直切片：30/100/365 天关系快照（「我们的纪念页」）。

设计边界（TECH-PLAN M8，产品边界已拍板，不调用 LLM）：
- 关系起点 = 当前人格命名空间最早一条持久化 user 消息的本地日期；没有 user
  消息就没有资格。与 last_chat_date / last_batch_date / 连续聊天无关。
- 里程碑按自然日包含起始日计算：today - start_date + 1 >= milestone 才有资格。
- GET 只读：返回既有快照与当前可创建的里程碑，绝不在读取路径创建。
- 快照完全确定性汇编真实持久数据，不编造情节/观点；每个收录来源都留
  type/id/date 清单（source_manifest_json），截断显式计数（count/omitted）。
- 来源资格按 cutoff（start_date + days - 1）定格：
  * relationship_events：status='active'、privacy='normal'、发生不晚于 cutoff，
    且在 cutoff 当天尚未失效（expires_at 晚于 cutoff 日，过期事件不算 active）；
  * artifacts：active、创建不晚于 cutoff，排除 relationship_snapshot 自身；
  * activity_viewpoints：保存时间不晚于 cutoff；
  * user_terms：count>=2 且首次/最近更新时间均不晚于 cutoff；
  * diary：日期不晚于 cutoff（既有 daily 产物，标明来源，只引用不二次编写）。
- 创建/删除都是显式动作：POST 幂等（已存在原样返回，不重写内容）；DELETE
  真删除快照、级联 artifact 并防御性作废来源事件；删除后 milestone 回到
  eligible，可显式重建，绝不自动再生。
- 落独立 relationship_snapshots 表，UNIQUE(user_id, snapshot_days)。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from . import relationship_events
from .userdb import db

MILESTONE_DAYS: tuple[int, ...] = (30, 100, 365)

# 每类来源的最大收录条数；超出的显式计入 omitted，不暗示完整。
_CAPS: dict[str, int] = {
    "relationship_events": 30,
    "artifacts": 20,
    "diary": 12,
    "user_terms": 20,
    "activity_viewpoints": 20,
}
_DIARY_EXCERPT_CHARS = 160

_EVENT_SHORT: dict[str, str] = {
    "reading_finished": "一起读完了一份文档",
    "promise_completed": "完成了一个约定",
    "important_date": "一个特殊日子到来了",
    "memory_corrected": "一段记忆被确认或纠正",
    "focus_finished": "一段专注陪伴完成",
    "goal_completed": "一个共同目标完成",
    "story_finished": "一个共同故事收笔",
    "list_completed": "一份共同清单收列",
}

_SECTION_TITLES: dict[str, str] = {
    "relationship_events": "## 这些日子真实发生的事",
    "artifacts": "## 一起留下的东西",
    "diary": "## 那些天的日记（每日记录原文节选）",
    "user_terms": "## 你的口头禅与黑话",
    "activity_viewpoints": "## 我们各自写下的想法",
}

_SOURCE_LABELS: dict[str, str] = {
    "relationship_events": "真实发生的事",
    "artifacts": "共同产物",
    "diary": "日记",
    "user_terms": "口头禅",
    "activity_viewpoints": "各自的想法",
}

_VIEWPOINT_ROLES: dict[str, str] = {"user": "你", "tuzhan": "菟菚", "shared": "共同"}


class RelationshipSnapshotError(ValueError):
    """关系快照的预期业务错误。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---- 资格计算（纯确定性）----


def _start_date_locked(user_id: str) -> str | None:
    """最早一条持久化 user 消息的本地日期；没有则 None。"""
    row = db.conn.execute(
        "SELECT date(ts) AS d FROM messages WHERE user_id = ? AND role = 'user' "
        "ORDER BY ts ASC, id ASC LIMIT 1",
        (user_id,),
    ).fetchone()
    return str(row["d"]) if row is not None and row["d"] else None


def days_reached(start_date: str, today: date | None = None) -> int:
    """自然日包含起始日的「第几天」：today - start + 1。"""
    today = today or date.today()
    return (today - date.fromisoformat(str(start_date))).days + 1


def cutoff_for(start_date: str, snapshot_days: int) -> str:
    """里程碑达成日：start_date + days - 1（当天达到 days 天）。"""
    return (
        date.fromisoformat(str(start_date)) + timedelta(days=int(snapshot_days) - 1)
    ).isoformat()


# ---- 确定性汇编（每个 builder 返回 key / 计数 / markdown 行 / manifest 项）----


def _split_cap(rows: list, key: str) -> tuple[list, dict]:
    cap = _CAPS[key]
    included = rows[:cap]
    total = int(rows[0]["total_count"]) if rows else 0
    return included, {
        "count": len(included),
        "omitted": max(0, total - len(included)),
        "cap": cap,
    }


def _section_events_locked(user_id: str, cutoff_date: str):
    rows = db.conn.execute(
        "SELECT id, event_type, object, occurred_at, COUNT(*) OVER() AS total_count "
        "FROM relationship_events "
        "WHERE user_id = ? AND status = 'active' AND privacy = 'normal' "
        "AND date(occurred_at) <= ? AND (expires_at IS NULL OR date(expires_at) > ?) "
        "ORDER BY occurred_at ASC, id ASC LIMIT ?",
        (user_id, cutoff_date, cutoff_date, _CAPS["relationship_events"]),
    ).fetchall()
    included, meta = _split_cap(list(rows), "relationship_events")
    lines: list[str] = []
    items: list[dict] = []
    for row in included:
        day = str(row["occurred_at"])[:10]
        obj = str(row["object"] or "").strip()
        desc = _EVENT_SHORT.get(str(row["event_type"]), "一件真实的事")
        lines.append(f"- {day} {desc}" + (f"：{obj}" if obj else ""))
        items.append({"type": "relationship_event", "id": int(row["id"]), "date": day})
    return "relationship_events", meta, lines, items


def _section_artifacts_locked(user_id: str, cutoff_date: str):
    rows = db.conn.execute(
        "SELECT id, title, created_at, COUNT(*) OVER() AS total_count FROM artifacts "
        "WHERE user_id = ? AND status = 'active' AND artifact_type != 'relationship_snapshot' "
        "AND date(created_at) <= ? "
        "ORDER BY created_at ASC, id ASC LIMIT ?",
        (user_id, cutoff_date, _CAPS["artifacts"]),
    ).fetchall()
    included, meta = _split_cap(list(rows), "artifacts")
    lines: list[str] = []
    items: list[dict] = []
    for row in included:
        day = str(row["created_at"])[:10]
        lines.append(f"- {day} {row['title']}")
        items.append({"type": "artifact", "id": int(row["id"]), "date": day})
    return "artifacts", meta, lines, items


def _section_diary_locked(user_id: str, cutoff_date: str):
    rows = db.conn.execute(
        "SELECT id, date, content, COUNT(*) OVER() AS total_count FROM diary "
        "WHERE user_id = ? AND date <= ? "
        "ORDER BY date ASC, id ASC LIMIT ?",
        (user_id, cutoff_date, _CAPS["diary"]),
    ).fetchall()
    included, meta = _split_cap(list(rows), "diary")
    lines: list[str] = []
    items: list[dict] = []
    for row in included:
        content = str(row["content"] or "").strip()
        if len(content) > _DIARY_EXCERPT_CHARS:
            content = content[:_DIARY_EXCERPT_CHARS] + "……（节选）"
        lines.append(f"- {row['date']} {content}")
        items.append({"type": "diary", "id": int(row["id"]), "date": str(row["date"])})
    return "diary", meta, lines, items


def _section_terms_locked(user_id: str, cutoff_date: str):
    rows = db.conn.execute(
        "SELECT id, term, meaning, count, first_seen, COUNT(*) OVER() AS total_count "
        "FROM user_terms "
        "WHERE user_id = ? AND count >= 2 "
        "AND date(first_seen) <= ? AND date(last_seen) <= ? "
        "ORDER BY first_seen ASC, id ASC LIMIT ?",
        (user_id, cutoff_date, cutoff_date, _CAPS["user_terms"]),
    ).fetchall()
    included, meta = _split_cap(list(rows), "user_terms")
    lines: list[str] = []
    items: list[dict] = []
    for row in included:
        meaning = str(row["meaning"] or "").strip()
        line = f"- 「{row['term']}」（出现 {row['count']} 次，首次 {str(row['first_seen'])[:10]}）"
        if meaning:
            line += f"（含义：{meaning}）"
        lines.append(line)
        items.append({
            "type": "user_term", "id": int(row["id"]), "date": str(row["first_seen"])[:10],
        })
    return "user_terms", meta, lines, items


def _section_viewpoints_locked(user_id: str, cutoff_date: str):
    rows = db.conn.execute(
        "SELECT id, role, content, ts, COUNT(*) OVER() AS total_count "
        "FROM activity_viewpoints "
        "WHERE user_id = ? AND date(ts) <= ? "
        "ORDER BY ts ASC, id ASC LIMIT ?",
        (user_id, cutoff_date, _CAPS["activity_viewpoints"]),
    ).fetchall()
    included, meta = _split_cap(list(rows), "activity_viewpoints")
    lines: list[str] = []
    items: list[dict] = []
    for row in included:
        day = str(row["ts"])[:10]
        role = _VIEWPOINT_ROLES.get(str(row["role"]), str(row["role"]))
        lines.append(f"- {day}（{role}）：{row['content']}")
        items.append({"type": "activity_viewpoint", "id": int(row["id"]), "date": day})
    return "activity_viewpoints", meta, lines, items


def _assemble_locked(
    user_id: str, snapshot_days: int, start_date: str, cutoff_date: str
) -> tuple[str, dict]:
    builders = (
        _section_events_locked,
        _section_artifacts_locked,
        _section_diary_locked,
        _section_terms_locked,
        _section_viewpoints_locked,
    )
    sections: dict[str, dict] = {}
    items: list[dict] = []
    body: list[str] = []
    for builder in builders:
        key, meta, lines, section_items = builder(user_id, cutoff_date)
        sections[key] = meta
        items.extend(section_items)
        if lines:
            body.append(_SECTION_TITLES[key])
            body.extend(lines)
            body.append("")

    summary = "、".join(
        f"{_SOURCE_LABELS[key]} {meta['count']}"
        + (f"（另有 {meta['omitted']} 条未列入）" if meta["omitted"] else "")
        for key, meta in sections.items()
    )
    head = [
        f"# 我们的第 {snapshot_days} 天",
        "",
        f"关系从 {start_date} 开始，到 {cutoff_date}，我们一起走过了 {snapshot_days} 天。",
        "",
        "这一页只收录这之前真实留下的记录，按时间排列；没做过的事不会写在这里。",
        "",
    ]
    tail = ["---", f"整理于 {_now()}。收录计数：{summary}。"]
    markdown = "\n".join(head + body + tail)
    manifest = {"sections": sections, "items": items}
    return markdown, manifest


# ---- 读取 / 创建 / 删除 ----


def _view_locked(row, *, include_content: bool) -> dict:
    try:
        manifest = json.loads(row["source_manifest_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        manifest = {}
    view: dict = {
        "id": int(row["id"]),
        "snapshot_days": int(row["snapshot_days"]),
        "start_date": row["start_date"],
        "cutoff_date": row["cutoff_date"],
        "generated_at": row["generated_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "source_counts": manifest.get("sections") or {},
    }
    if include_content:
        view["rendered_markdown"] = row["rendered_markdown"]
        view["source_manifest"] = manifest
    return view


def snapshot_state(user_id: str) -> dict:
    """只读：既有快照（含内容）+ 当前可创建的里程碑；绝不在这里创建任何东西。"""
    with db._lock:
        start = _start_date_locked(user_id)
        rows = db.conn.execute(
            "SELECT * FROM relationship_snapshots WHERE user_id = ? "
            "ORDER BY snapshot_days ASC",
            (user_id,),
        ).fetchall()
    views = [_view_locked(row, include_content=True) for row in rows]
    reached = days_reached(start) if start else 0
    existing_days = {view["snapshot_days"] for view in views}
    milestones = [
        {
            "days": days,
            "eligible": bool(start) and reached >= days,
            "has_snapshot": days in existing_days,
            "snapshot_id": next(
                (view["id"] for view in views if view["snapshot_days"] == days), None
            ),
        }
        for days in MILESTONE_DAYS
    ]
    return {
        "ok": True,
        "start_date": start,
        "today": date.today().isoformat(),
        "days_since": reached if start else None,
        "milestones": milestones,
        "snapshots": views,
    }


def create_snapshot(user_id: str, snapshot_days: int) -> dict:
    """用户显式整理某一天的纪念页。幂等：已存在则原样返回，不重写内容。"""
    try:
        days = int(snapshot_days)
    except (TypeError, ValueError) as exc:
        raise RelationshipSnapshotError("天数不正确") from exc
    if days not in MILESTONE_DAYS:
        raise RelationshipSnapshotError("纪念页只有 30 / 100 / 365 天三种")
    with db._lock:
        row = db.conn.execute(
            "SELECT * FROM relationship_snapshots WHERE user_id = ? AND snapshot_days = ?",
            (user_id, days),
        ).fetchone()
        if row is not None:
            return _view_locked(row, include_content=True)
        start = _start_date_locked(user_id)
        if start is None:
            raise RelationshipSnapshotError("还没有留下第一句对话，暂时没有可整理的日子")
        reached = days_reached(start)
        if reached < days:
            raise RelationshipSnapshotError(f"现在才第 {reached} 天，第 {days} 天还没到")
        cutoff = cutoff_for(start, days)
        markdown, manifest = _assemble_locked(user_id, days, start, cutoff)
        now = _now()
        cur = db.conn.execute(
            "INSERT INTO relationship_snapshots "
            "(user_id, snapshot_days, start_date, cutoff_date, generated_at, "
            "source_manifest_json, rendered_markdown, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, days, start, cutoff, now,
             json.dumps(manifest, ensure_ascii=False), markdown, now, now),
        )
        snapshot_id = int(cur.lastrowid)
        # 幂等 upsert 对应 artifact；UNIQUE(user_id, artifact_type, source_id) 兜底。
        db.conn.execute(
            "INSERT OR IGNORE INTO artifacts "
            "(user_id, artifact_type, source_type, source_id, title, content, version, "
            "created_at, updated_at) "
            "VALUES (?, 'relationship_snapshot', 'relationship_snapshot', ?, ?, ?, 1, ?, ?)",
            (user_id, snapshot_id, f"我们的第 {days} 天", markdown, now, now),
        )
        db.conn.commit()
        row = db.conn.execute(
            "SELECT * FROM relationship_snapshots WHERE id = ? AND user_id = ?",
            (snapshot_id, user_id),
        ).fetchone()
        return _view_locked(row, include_content=True)


def delete_snapshot(user_id: str, snapshot_days: int) -> bool:
    """真删除：快照、对应 artifact 一并清除，并防御性作废来源事件。"""
    try:
        days = int(snapshot_days)
    except (TypeError, ValueError):
        return False
    with db._lock:
        row = db.conn.execute(
            "SELECT id FROM relationship_snapshots WHERE user_id = ? AND snapshot_days = ?",
            (user_id, days),
        ).fetchone()
        if row is None:
            db.conn.commit()
            return False
        snapshot_id = int(row["id"])
        db.conn.execute(
            "DELETE FROM relationship_snapshots WHERE id = ? AND user_id = ?",
            (snapshot_id, user_id),
        )
        db.conn.execute(
            "DELETE FROM artifacts WHERE user_id = ? AND artifact_type = 'relationship_snapshot' "
            "AND source_type = 'relationship_snapshot' AND source_id = ?",
            (user_id, snapshot_id),
        )
        relationship_events.invalidate_for_source(
            user_id, "relationship_snapshot", snapshot_id, commit=False
        )
        db.conn.commit()
        return True
