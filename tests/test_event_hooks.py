# -*- coding: utf-8 -*-
"""P1-5 回归：commit=False 生产者的关系事件下游钩子必须真正执行。

此前五个生产者（共读/专注/共创/目标/清单）以 commit=False 随外层事务提交，
record() 把「是否自己 commit」当成「事务是否真正提交」，导致 L03 气质证据 /
G01 初历标记在真实落库路径上永不执行。修复后生产者在外层 commit 之后显式
调用 register_event_hooks。

运行：python -m tests.test_event_hooks
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_event_hooks_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import focus  # noqa: E402
from backend.core.userdb import db  # noqa: E402

UID = "assistant-main"


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _evidence_count(event_id: int) -> int:
    with db._lock:
        return int(db.conn.execute(
            "SELECT COUNT(*) FROM relationship_style_evidence WHERE event_id=?",
            (event_id,),
        ).fetchone()[0])


def _first_occurrence_count(event_id: int) -> int:
    with db._lock:
        return int(db.conn.execute(
            "SELECT COUNT(*) FROM first_occurrences WHERE source_event_id=?",
            (event_id,),
        ).fetchone()[0])


def _seed_overdue_focus(title: str) -> int:
    """造一段「已开始但已超时」的专注（读取时惰性自然到点）。"""
    with db._lock:
        now = datetime.now()
        cur = db.conn.execute(
            "INSERT INTO activities "
            "(user_id, kind, document_id, title, status, position, "
            "planned_minutes, remaining_seconds, ends_at, created_at, updated_at) "
            "VALUES (?, 'focus', 0, ?, 'active', 0, 25, 1500, ?, ?, ?)",
            (
                UID,
                title,
                _iso(now - timedelta(minutes=1)),
                _iso(now - timedelta(minutes=26)),
                _iso(now - timedelta(minutes=26)),
            ),
        )
        db.conn.commit()
        return int(cur.lastrowid)


def _event_id(activity_id: int) -> int | None:
    with db._lock:
        row = db.conn.execute(
            "SELECT id FROM relationship_events "
            "WHERE user_id=? AND event_type='focus_finished' "
            "AND source_type='activity' AND source_id=? AND status='active'",
            (UID, activity_id),
        ).fetchone()
    return int(row["id"]) if row else None


def _test_natural_completion_hooks() -> None:
    activity_id = _seed_overdue_focus("钩子回归·自然到点")
    detail, just_finished = focus.current_focus(UID)
    assert just_finished is True and detail["status"] == "completed"
    event_id = _event_id(activity_id)
    assert event_id is not None, "自然到点应落 focus_finished 事件"
    assert _evidence_count(event_id) >= 1, "L03 气质证据应随事件登记（P1-5）"
    assert _first_occurrence_count(event_id) >= 1, "G01 初历应随事件登记（P1-5）"
    print("[OK] 自然到点：事件落库后气质证据与初历登记齐备")


def _test_manual_completion_hooks() -> None:
    session = focus.start_focus(UID, 25)
    with db._lock:
        db.conn.execute(
            "UPDATE activities SET ends_at=?, created_at=? WHERE id=?",
            (
                _iso(datetime.now() + timedelta(minutes=23)),
                _iso(datetime.now() - timedelta(minutes=10)),
                session["id"],
            ),
        )
        db.conn.commit()
    done = focus.complete_focus(UID, session["id"])
    assert done["status"] == "completed"
    event_id = _event_id(session["id"])
    assert event_id is not None, "手动完成应落 focus_finished 事件"
    assert _evidence_count(event_id) >= 1, "L03 气质证据应随事件登记（P1-5）"
    assert _first_occurrence_count(event_id) >= 1, "G01 初历应随事件登记（P1-5）"
    print("[OK] 手动完成：事件落库后气质证据与初历登记齐备")


def main() -> None:
    _test_natural_completion_hooks()
    _test_manual_completion_hooks()
    print("=== 事件钩子回归：全部通过 ===")


if __name__ == "__main__":
    main()
