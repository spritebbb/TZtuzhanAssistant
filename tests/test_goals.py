# -*- coding: utf-8 -*-
"""M3.3 共同目标：生命周期、真实进展、提醒克制、事件与可选产物。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_goals_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import goals, pending_thoughts  # noqa: E402
from backend.core.activities import ActivityError  # noqa: E402
from backend.core.userdb import db  # noqa: E402

UID = "assistant-main"


def _test_lifecycle_and_progress() -> None:
    goal = goals.start_goal(
        UID,
        "整理第一版作品集",
        "把做过的事讲清楚",
        "先挑出三个项目",
        "companion",
    )
    assert goal["status"] == "active"
    assert goal["next_step"] == "先挑出三个项目"
    assert goal["reminder_at"] is None
    assert goals.goal_context(UID, "今天天气不错") == ""
    assert "先挑出三个项目" in goals.goal_context(UID, "我们的目标下一步做什么")

    progressed = goals.add_progress(
        UID,
        goal["id"],
        "挑出了登录页、知识库和共读三个项目",
        percent=35,
        next_step="给每个项目写一句价值说明",
    )
    assert progressed["progress_entries"][0]["percent"] == 35
    assert progressed["next_step"] == "给每个项目写一句价值说明"
    assert goals.pause_goal(UID, goal["id"])["status"] == "paused"
    try:
        goals.pause_goal(UID, goal["id"])
        raise AssertionError("已暂停目标不应再次暂停")
    except ActivityError:
        pass
    assert goals.resume_goal(UID, goal["id"])["status"] == "active"

    done = goals.complete_goal(UID, goal["id"], create_artifact=True)
    assert done["status"] == "completed"
    assert "挑出了登录页" in done["review"]
    assert "# 整理第一版作品集" in goals.export_markdown(UID, goal["id"])
    with db._lock:
        event_count = db.conn.execute(
            "SELECT COUNT(*) FROM relationship_events WHERE user_id = ? "
            "AND event_type = 'goal_completed' AND source_id = ? AND status = 'active'",
            (UID, goal["id"]),
        ).fetchone()[0]
        artifact_count = db.conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE user_id = ? "
            "AND artifact_type = 'goal_review' AND source_id = ? AND status = 'active'",
            (UID, goal["id"]),
        ).fetchone()[0]
    assert event_count == 1
    assert artifact_count == 1
    try:
        goals.add_progress(UID, goal["id"], "不该写入")
        raise AssertionError("完成后不应还能追加进展")
    except ActivityError:
        pass
    print("[OK] 共同目标：完整状态机、真实进展、相关语境、事件、回顾与导出")


def _test_reminder_once_and_optional_artifact() -> None:
    reminder = goals.start_goal(
        UID,
        "练完第一首曲子",
        "想完整弹下来",
        "先练前八小节",
        "reminder",
        (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds"),
    )
    with db._lock:
        db.conn.execute(
            "UPDATE activity_goals SET reminder_at = ? WHERE activity_id = ?",
            ((datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds"), reminder["id"]),
        )
        db.conn.commit()
    assert pending_thoughts.sync_pending_thoughts(UID) == 1
    assert pending_thoughts.sync_pending_thoughts(UID) == 0
    thoughts = [item for item in pending_thoughts.due_thoughts(UID, 10) if item["kind"] == "goal_checkin"]
    assert len(thoughts) == 1, "同一目标只能产生一次轻提醒，不连续催促"

    done = goals.complete_goal(UID, reminder["id"], create_artifact=False)
    assert done["review"] == ""
    assert not any(
        item["kind"] == "goal_checkin" for item in pending_thoughts.due_thoughts(UID, 10)
    ), "完成目标后提醒心事应作废"

    cancelled = goals.start_goal(UID, "试一周早起", "看看感受", "明早提前十分钟起床")
    assert goals.cancel_goal(UID, cancelled["id"])["status"] == "cancelled"
    print("[OK] 陪伴偏好：提醒只挂一次，完成后作废；过程回顾可选择不生成")


def main() -> None:
    _test_lifecycle_and_progress()
    _test_reminder_once_and_optional_artifact()
    print("共同目标 M3.3 测试通过")


if __name__ == "__main__":
    main()
