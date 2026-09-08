# -*- coding: utf-8 -*-
"""L02 观察日志：真实生活的观察条目，来源可追溯、不无源补成纪实。

契约（docs/Zcode技术指导.md L02 + 调度文档批次 10）：

- 复用 ``activities(kind='observation')`` 壳，侧表 ``observation_entries``；
- observer = user / assistant；来源 ``source_type``/``source_id`` 可追溯，
  无真实观察不允许 LLM 补成纪实日志；
- 完成时产出 ``observation_log`` 产物（版本化），条目来源一并保留；
- 活动壳互斥与既有 pause_all_active 语义一致；源删/取消按壳清理。
"""
from __future__ import annotations

from datetime import datetime

from .activities import ActivityError, pause_all_active_locked
from .log import logger
from .userdb import db

OBSERVERS: tuple[str, ...] = ("user", "assistant")
SOURCE_TYPES: tuple[str, ...] = ("event", "activity", "fact", "character_life")
_MAX_TITLE = 60
_MAX_CONTENT = 400


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _clean(text: str, limit: int, label: str, *, required: bool = False) -> str:
    value = " ".join(str(text or "").split()).strip()
    if required and not value:
        raise ActivityError(f"{label}不能为空")
    return value[:limit]


def _detail_locked(user_id: str, activity_id: int) -> dict | None:
    row = db.conn.execute(
        "SELECT id, title, status, created_at, updated_at, completed_at FROM activities "
        "WHERE user_id=? AND id=? AND kind='observation'",
        (user_id, activity_id),
    ).fetchone()
    if row is None:
        return None
    detail = dict(row)
    entries = db.conn.execute(
        "SELECT * FROM observation_entries WHERE user_id=? AND activity_id=? "
        "ORDER BY observed_at, id",
        (user_id, activity_id),
    ).fetchall()
    detail["entries"] = [dict(r) for r in entries]
    return detail


def start_observation(user_id: str, title: str) -> dict:
    """开一本观察日志；同时只活跃一场（与共读/专注/目标/创作互斥）。"""
    title = _clean(title, _MAX_TITLE, "日志名", required=True)
    now = _now()
    with db._lock:
        pause_all_active_locked(user_id, now)
        cur = db.conn.execute(
            "INSERT INTO activities "
            "(user_id, kind, document_id, title, status, position, created_at, updated_at) "
            "VALUES (?, 'observation', 0, ?, 'active', 0, ?, ?)",
            (user_id, title, now, now),
        )
        activity_id = int(cur.lastrowid)
        db.conn.commit()
        return _detail_locked(user_id, activity_id)  # type: ignore[return-value]


def add_entry(user_id: str, activity_id: int, content: str, *, observer: str = "user",
              source_type: str = "", source_id: int | None = None,
              confidence: float = 1.0, observed_at: str | None = None) -> dict:
    """记一条观察；observer/来源类型都在白名单内，来源缺失就是无源观察。"""
    if observer not in OBSERVERS:
        raise ActivityError(f"未知观察者：{observer}")
    if source_type and source_type not in SOURCE_TYPES:
        raise ActivityError(f"未知来源类型：{source_type}")
    content = _clean(content, _MAX_CONTENT, "观察内容", required=True)
    now = _now()
    with db._lock:
        detail = _detail_locked(user_id, activity_id)
        if detail is None:
            raise ActivityError("这本观察日志不存在")
        if detail["status"] in {"completed", "cancelled"}:
            raise ActivityError("已经结束的日志不能再记")
        db.conn.execute(
            "INSERT INTO observation_entries "
            "(user_id, activity_id, observed_at, observer, content, source_type, source_id, "
            "confidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, activity_id, observed_at or now, observer, content,
             source_type, None if source_id is None else int(source_id),
             max(0.0, min(1.0, float(confidence))), now),
        )
        db.conn.execute(
            "UPDATE activities SET updated_at=? WHERE user_id=? AND id=?",
            (now, user_id, activity_id),
        )
        db.conn.commit()
        return _detail_locked(user_id, activity_id)  # type: ignore[return-value]


def _set_status(user_id: str, activity_id: int, status: str) -> dict:
    now = _now()
    with db._lock:
        detail = _detail_locked(user_id, activity_id)
        if detail is None:
            raise ActivityError("这本观察日志不存在")
        if detail["status"] in {"completed", "cancelled"}:
            raise ActivityError("这本观察日志已经结束了")
        db.conn.execute(
            "UPDATE activities SET status=?, updated_at=?, completed_at=? WHERE user_id=? AND id=?",
            (status, now, now if status == "completed" else None, user_id, activity_id),
        )
        db.conn.commit()
        return _detail_locked(user_id, activity_id)  # type: ignore[return-value]


def pause_observation(user_id: str, activity_id: int) -> dict:
    return _set_status(user_id, activity_id, "paused")


def resume_observation(user_id: str, activity_id: int) -> dict:
    return _set_status(user_id, activity_id, "active")


def cancel_observation(user_id: str, activity_id: int) -> dict:
    return _set_status(user_id, activity_id, "cancelled")


def complete_observation(user_id: str, activity_id: int) -> dict:
    """收尾：产出 observation_log 产物（只汇编真实条目，不补写）。"""
    detail = _set_status(user_id, activity_id, "completed")
    content = compile_log(detail)
    now = _now()
    with db._lock:
        db.conn.execute(
            "INSERT INTO artifacts "
            "(user_id, artifact_type, source_type, source_id, title, content, version, "
            "created_at, updated_at) "
            "VALUES (?, 'observation_log', 'activity', ?, ?, ?, 1, ?, ?) "
            "ON CONFLICT(user_id, artifact_type, source_id) DO UPDATE SET "
            "content = excluded.content, version = artifacts.version + 1, updated_at = excluded.updated_at",
            (user_id, activity_id, f"《{detail['title']}》观察日志", content, now, now),
        )
        db.conn.commit()
    logger.info("[观察日志] 收尾 #{}（{} 条）", activity_id, len(detail["entries"]))
    detail["log"] = content
    return detail


def compile_log(detail: dict) -> str:
    """确定性汇编：只组装真实条目与来源，缺来源就如实标注。"""
    lines = [f"《{detail['title']}》观察记录："]
    for entry in detail.get("entries", []):
        who = "我" if entry["observer"] == "user" else "她"
        source = (
            f"（来源：{entry['source_type']}#{entry['source_id']}）"
            if entry.get("source_type") and entry.get("source_id") else "（无外部来源）"
        )
        lines.append(f"- {entry['observed_at'][:10]} {who}：{entry['content']}{source}")
    if len(lines) == 1:
        lines.append("（还没有记下任何观察。）")
    return "\n".join(lines)


def list_observations(user_id: str, *, limit: int = 20) -> list[dict]:
    with db._lock:
        rows = db.conn.execute(
            "SELECT id, title, status, created_at, updated_at, completed_at FROM activities "
            "WHERE user_id=? AND kind='observation' ORDER BY id DESC LIMIT ?",
            (user_id, max(1, min(100, int(limit)))),
        ).fetchall()
    return [dict(r) for r in rows]


def get_observation(user_id: str, activity_id: int) -> dict | None:
    with db._lock:
        return _detail_locked(user_id, activity_id)


def forget_for_activity(user_id: str, activity_id: int) -> int:
    """活动取消/删除：清条目（产物由调用方按删除契约处理）。"""
    with db._lock:
        cur = db.conn.execute(
            "DELETE FROM observation_entries WHERE user_id=? AND activity_id=?",
            (user_id, int(activity_id)),
        )
        db.conn.commit()
    return int(cur.rowcount)
