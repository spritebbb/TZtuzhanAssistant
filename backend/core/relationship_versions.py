# -*- coding: utf-8 -*-
"""M8.7「不同版本的我们」：用户显式创建的关系版本检查点。

设计边界（产品语义已拍板，确定性比较，不调用 LLM）：
- 检查点由用户显式创建并起标签（如「升级前」）；一经创建不可修改，只可删除。
- 只保存当时的结构化状态、行为帧白名单字段与记录计数（format_version=1），
  绝不保存消息原文、事实正文、事件 object/payload、日记/产物内容或隐含推理；
  行为帧只存六个确定性字段，reaction/archive/event/tension 线与 season reason
  可能携带用户文本或修复提示，一律不落盘。
- 比较是确定性的：只列数值增减与分类字段变化，不评价「变好/变坏」，不生成
  verdict/score/trend 等判断字段。
- 绝不自动创建；list/compare/delete 均为只读或删除，不隐式建档（capture 必须
  先 ensure_user，因为那是用户显式写操作）。
- 跨人格隔离：所有 SQL 带 user_id；compare 两端都必须属于当前人格。
- 恢复后 snapshot_json 原样保留（导出走 relationship_export，无外键引用、
  无需 id 重映射），不用目标当前状态重算，证明检查点不可变。
"""
from __future__ import annotations

import json
from datetime import datetime

from . import behavior, seasons
from . import state as state_module
from .userdb import _SCHEMA_VERSION, db

_FORMAT_VERSION = 1
_LABEL_MAX = 60

# 行为帧白名单：六个确定性字段。严禁加入 reaction_line / archive_line /
# event_line / tension_line（可能引用真实用户文本）与 frame.compose()。
_BEHAVIOR_FIELDS: tuple[str, ...] = (
    "mood_line", "stage_line", "texture_line", "initiative", "rest_line", "season_line",
)
_STATE_FIELDS: tuple[str, ...] = (
    "affection", "mood", "mood_label", "energy", "tension", "stage", "resting",
)
_COUNT_KEYS: tuple[str, ...] = (
    "messages", "facts_active", "long_memory", "events_active",
    "artifacts_real", "artifacts_fiction",
    "activities_active", "activities_completed",
    "promises_pending", "promises_completed",
    "diary", "future_letters", "dual_perspectives", "relationship_snapshots",
)
_NUMERIC_PATHS: tuple[str, ...] = (
    "state.affection", "state.mood", "state.energy", "state.tension",
)
_CHANGE_PATHS: tuple[str, ...] = (
    "state.stage", "state.mood_label", "state.resting", "season.code", "season.label",
) + tuple(f"behavior.{field}" for field in _BEHAVIOR_FIELDS)


class RelationshipVersionError(ValueError):
    """关系版本检查点的预期业务错误。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _counts_locked(user_id: str) -> dict[str, int]:
    """记录计数：全部按 user_id 严格过滤，不读取任何正文。"""

    def count(sql: str) -> int:
        return int(db.conn.execute(sql, (user_id,)).fetchone()[0])

    return {
        "messages": count("SELECT COUNT(*) FROM messages WHERE user_id = ?"),
        "facts_active": count(
            "SELECT COUNT(*) FROM facts WHERE user_id = ? AND status = 'active'"
        ),
        "long_memory": count("SELECT COUNT(*) FROM long_memory WHERE user_id = ?"),
        "events_active": count(
            "SELECT COUNT(*) FROM relationship_events "
            "WHERE user_id = ? AND status = 'active' AND privacy = 'normal'"
        ),
        "artifacts_real": count(
            "SELECT COUNT(*) FROM artifacts "
            "WHERE user_id = ? AND status = 'active' AND source_type != 'fiction'"
        ),
        "artifacts_fiction": count(
            "SELECT COUNT(*) FROM artifacts "
            "WHERE user_id = ? AND status = 'active' AND source_type = 'fiction'"
        ),
        "activities_active": count(
            "SELECT COUNT(*) FROM activities WHERE user_id = ? AND status = 'active'"
        ),
        "activities_completed": count(
            "SELECT COUNT(*) FROM activities WHERE user_id = ? AND status = 'completed'"
        ),
        "promises_pending": count(
            "SELECT COUNT(*) FROM promises WHERE user_id = ? AND status = 'pending'"
        ),
        "promises_completed": count(
            "SELECT COUNT(*) FROM promises WHERE user_id = ? AND status = 'done'"
        ),
        "diary": count("SELECT COUNT(*) FROM diary WHERE user_id = ?"),
        "future_letters": count("SELECT COUNT(*) FROM future_letters WHERE user_id = ?"),
        "dual_perspectives": count("SELECT COUNT(*) FROM dual_perspectives WHERE user_id = ?"),
        "relationship_snapshots": count(
            "SELECT COUNT(*) FROM relationship_snapshots WHERE user_id = ?"
        ),
    }


def _build_snapshot(user_id: str) -> str:
    """构建检查点 JSON（ensure_ascii=False、sort_keys=True）。调用方需持有 db._lock。"""
    snapshot_state = state_module.load_state(user_id)
    season = seasons.current_season(user_id, snapshot_state)
    frame = behavior.build_behavior_frame(
        snapshot_state, season_line=str(season.get("line") or "")
    )
    snapshot = {
        "format_version": _FORMAT_VERSION,
        "state": {
            "affection": int(snapshot_state.affection),
            "mood": int(snapshot_state.emotion),
            "mood_label": str(snapshot_state.emotion_name),
            "energy": int(snapshot_state.energy),
            "tension": int(snapshot_state.tension),
            "stage": str(snapshot_state.stage),
            "resting": bool(snapshot_state.resting),
        },
        # 只存 code/label；reason 可能带用户内容（如纪念日名），绝不落盘。
        "season": {
            "code": str(season.get("season") or "quiet"),
            "label": str(season.get("label") or "沉淀期"),
        },
        "behavior": {field: str(getattr(frame, field) or "") for field in _BEHAVIOR_FIELDS},
        "counts": _counts_locked(user_id),
    }
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True)


def _get_locked(user_id: str, version_id: int):
    return db.conn.execute(
        "SELECT * FROM relationship_versions WHERE id = ? AND user_id = ?",
        (int(version_id), user_id),
    ).fetchone()


def _parse_snapshot(row) -> dict:
    try:
        snapshot = json.loads(row["snapshot_json"])
    except (json.JSONDecodeError, TypeError) as exc:
        raise RelationshipVersionError("这个版本的存档数据损坏了") from exc
    if not isinstance(snapshot, dict) or int(snapshot.get("format_version") or 0) != _FORMAT_VERSION:
        raise RelationshipVersionError("这个版本的存档数据损坏了")
    return snapshot


def _row_view(row) -> dict:
    return {
        "id": int(row["id"]),
        "label": str(row["label"]),
        "captured_at": str(row["captured_at"]),
        "schema_version": int(row["schema_version"]),
        "created_at": str(row["created_at"]),
        "snapshot": _parse_snapshot(row),
    }


def capture_version(user_id: str, label: str) -> dict:
    """用户显式留下一个版本检查点。创建后不可修改；不可变性由「只存不读算」保证。"""
    label = str(label or "").strip()
    if not label:
        raise RelationshipVersionError("先给这个版本起个名字")
    if len(label) > _LABEL_MAX:
        raise RelationshipVersionError("版本名字太长了（最多 60 个字）")
    # 显式写操作：先确保人格存在（读路径不做这件事，避免隐式建档）。
    db.ensure_user(user_id)
    now = _now()
    with db._lock:
        snapshot_json = _build_snapshot(user_id)
        cur = db.conn.execute(
            "INSERT INTO relationship_versions "
            "(user_id, label, captured_at, schema_version, snapshot_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, label, now, int(_SCHEMA_VERSION), snapshot_json, now),
        )
        version_id = int(cur.lastrowid)
        db.conn.commit()
        row = _get_locked(user_id, version_id)
    return _row_view(row)


def list_versions(user_id: str, limit: int = 50) -> list[dict]:
    """列出本人格的检查点；返回落库快照，不重算，证明不可变。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(100, limit))
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM relationship_versions WHERE user_id = ? "
            "ORDER BY captured_at DESC, id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [_row_view(row) for row in rows]


def _value_at(snapshot: dict, path: str):
    section, key = path.split(".", 1)
    return (snapshot.get(section) or {}).get(key)


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def compare_versions(user_id: str, before_id: int, after_id: int) -> dict:
    """确定性比较两个检查点：只列数值增减与分类变化，无任何价值判断字段。"""
    try:
        before_id = int(before_id)
        after_id = int(after_id)
    except (TypeError, ValueError) as exc:
        raise RelationshipVersionError("版本 id 不正确") from exc
    if before_id == after_id:
        raise RelationshipVersionError("要选两个不同的版本才能比较")
    with db._lock:
        before_row = _get_locked(user_id, before_id)
        after_row = _get_locked(user_id, after_id)
    if before_row is None or after_row is None:
        raise RelationshipVersionError("要比较的版本不存在（或属于别的人格）")
    before_view = _row_view(before_row)
    after_view = _row_view(after_row)
    before_snap = before_view["snapshot"]
    after_snap = after_view["snapshot"]

    numeric_deltas: dict[str, int] = {
        path: _as_int(_value_at(after_snap, path)) - _as_int(_value_at(before_snap, path))
        for path in _NUMERIC_PATHS
    }
    for key in _COUNT_KEYS:
        path = f"counts.{key}"
        numeric_deltas[path] = _as_int(_value_at(after_snap, path)) - _as_int(
            _value_at(before_snap, path)
        )
    changes = [
        {"key": path, "before": _value_at(before_snap, path), "after": _value_at(after_snap, path)}
        for path in _CHANGE_PATHS
        if _value_at(before_snap, path) != _value_at(after_snap, path)
    ]
    return {
        "ok": True,
        "before": before_view,
        "after": after_view,
        "numeric_deltas": numeric_deltas,
        "changes": changes,
    }


def delete_version(user_id: str, version_id: int) -> bool:
    """真删除本人格的指定检查点；不存在/越权返回 False。"""
    try:
        version_id = int(version_id)
    except (TypeError, ValueError):
        return False
    with db._lock:
        cur = db.conn.execute(
            "DELETE FROM relationship_versions WHERE id = ? AND user_id = ?",
            (version_id, user_id),
        )
        db.conn.commit()
    return cur.rowcount > 0
