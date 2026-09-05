# -*- coding: utf-8 -*-
"""M3.2 专注陪伴：状态机、活动互斥、惰性到点结算、主动静默、复盘资格、事件幂等。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_focus_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import focus  # noqa: E402
from backend.core.activities import ActivityError, start_reading  # noqa: E402
from backend.core.userdb import db  # noqa: E402

UID = "assistant-main"


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _event_count(activity_id: int) -> int:
    with db._lock:
        return int(
            db.conn.execute(
                "SELECT COUNT(*) FROM relationship_events "
                "WHERE user_id = ? AND event_type = 'focus_finished' "
                "AND source_type = 'activity' AND source_id = ? AND status = 'active'",
                (UID, activity_id),
            ).fetchone()[0]
        )


def _seed_document() -> int:
    with db._lock:
        now = _iso(datetime.now())
        cur = db.conn.execute(
            "INSERT INTO kb_documents (user_id, filename, stored_path, format, size_bytes, chunk_count, ts) "
            "VALUES (?, '温室手记.txt', '', 'txt', 10, 3, ?)",
            (UID, now),
        )
        doc_id = int(cur.lastrowid)
        for seq in range(3):
            db.conn.execute(
                "INSERT INTO kb_chunks (user_id, doc_id, seq, text, ts) VALUES (?, ?, ?, ?, ?)",
                (UID, doc_id, seq, f"第{seq}段", now),
            )
        db.conn.commit()
    return doc_id


def _test_state_machine() -> None:
    # 非法时长
    try:
        focus.start_focus(UID, 30)
        raise AssertionError("30 分钟不应被接受")
    except ActivityError:
        pass

    detail = focus.start_focus(UID, 25)
    assert detail["status"] == "active"
    assert detail["planned_minutes"] == 25
    assert 1490 <= detail["remaining_seconds"] <= 1500
    assert detail["ends_at"]
    activity_id = detail["id"]

    # 暂停：剩余时间被结算冻结，ends_at 清空
    paused = focus.pause_focus(UID, activity_id)
    assert paused["status"] == "paused"
    assert paused["ends_at"] is None
    frozen = paused["remaining_seconds"]
    assert 1400 <= frozen <= 1500

    # 非法迁移：暂停的不能再暂停；进行中的不能恢复
    try:
        focus.pause_focus(UID, activity_id)
        raise AssertionError("重复暂停不应成功")
    except ActivityError:
        pass

    # 恢复：ends_at 按冻结剩余时间重算
    resumed = focus.resume_focus(UID, activity_id)
    assert resumed["status"] == "active"
    assert abs(resumed["remaining_seconds"] - frozen) <= 2
    assert resumed["ends_at"]

    # 完成：事件幂等（同一活动只留一条 active 事件）
    done = focus.complete_focus(UID, activity_id)
    assert done["status"] == "completed"
    assert done["completed_at"]
    assert _event_count(activity_id) == 1
    try:
        focus.complete_focus(UID, activity_id)
        raise AssertionError("重复完成不应成功")
    except ActivityError:
        pass
    assert _event_count(activity_id) == 1

    # 取消：只留状态，不产生事件
    again = focus.start_focus(UID, 50)
    cancelled = focus.cancel_focus(UID, again["id"])
    assert cancelled["status"] == "cancelled"
    assert _event_count(again["id"]) == 0
    print("[OK] 状态机：开始/暂停/恢复/完成/取消，非法迁移拒绝，事件幂等，取消无事件")


def _test_mutual_exclusion() -> None:
    # 专注进行中开共读 → 专注被暂停且剩余时间已结算（不被旧 ends_at 吃掉）
    doc_id = _seed_document()
    session = focus.start_focus(UID, 25)
    start_reading(UID, doc_id)
    after = focus.get_focus(UID, session["id"])
    assert after["status"] == "paused"
    assert after["ends_at"] is None
    assert after["remaining_seconds"] > 1400, "共读抢场时专注剩余时间必须结算保留"
    focus.cancel_focus(UID, session["id"])  # 收尾，避免影响后续用例

    # 共读进行中开专注 → 共读被暂停
    reading = start_reading(UID, doc_id)
    assert reading["status"] == "active"
    session2 = focus.start_focus(UID, 25)
    from backend.core.activities import get_activity

    assert get_activity(UID, reading["id"])["status"] == "paused"
    focus.cancel_focus(UID, session2["id"])  # 收尾，避免影响后续用例
    print("[OK] 互斥：同时只活跃一场，focus 被抢场时剩余时间正确结算")


def _test_lazy_completion_and_silence() -> None:
    # 手工造一段「已开始但已超时」的专注（模拟应用关闭期间到点）
    with db._lock:
        now = datetime.now()
        cur = db.conn.execute(
            "INSERT INTO activities "
            "(user_id, kind, document_id, title, status, position, "
            "planned_minutes, remaining_seconds, ends_at, created_at, updated_at) "
            "VALUES (?, 'focus', 0, '专注 25 分钟', 'active', 0, 25, 1500, ?, ?, ?)",
            (
                UID,
                _iso(now - timedelta(minutes=1)),
                _iso(now - timedelta(minutes=26)),
                _iso(now - timedelta(minutes=26)),
            ),
        )
        overdue_id = int(cur.lastrowid)
        db.conn.commit()

    # 超时但仍是 active：不静默（专注已事实结束），读取时惰性完成
    assert focus.focus_in_progress(UID) is False
    detail, just_finished = focus.current_focus(UID)
    assert just_finished is True
    assert detail["status"] == "completed"
    assert focus.wrapup_eligible(detail) is True  # 自然到点必然够格复盘
    assert _event_count(overdue_id) == 1

    # 进行中的专注静默；暂停后不静默
    session = focus.start_focus(UID, 25)
    assert focus.focus_in_progress(UID) is True
    focus.pause_focus(UID, session["id"])
    assert focus.focus_in_progress(UID) is False
    focus.cancel_focus(UID, session["id"])
    assert focus.focus_in_progress(UID) is False
    print("[OK] 惰性到点：读取即结算+事件一次；静默只在真进行中生效")


def _test_wrapup_eligibility() -> None:
    # 秒开秒关（实际专注 < 5 分钟）不触发复盘
    session = focus.start_focus(UID, 25)
    with db._lock:
        # 把开始时间拉回 2 分钟前，模拟短暂专注后手动结束
        db.conn.execute(
            "UPDATE activities SET ends_at = ?, created_at = ? WHERE id = ?",
            (
                _iso(datetime.now() + timedelta(minutes=23)),
                _iso(datetime.now() - timedelta(minutes=2)),
                session["id"],
            ),
        )
        db.conn.commit()
    done = focus.complete_focus(UID, session["id"])
    assert done["status"] == "completed"
    assert 100 <= done["elapsed_seconds"] <= 180
    assert focus.wrapup_eligible(done) is False, "短暂专注不该凑一句复盘"
    print("[OK] 复盘资格：自然到点/足量专注才复盘，秒开秒关不触发")


def _test_focus_context_gating() -> None:
    # 普通闲聊零注入
    assert focus.focus_context(UID, "今天天气怎么样") == ""
    # 没有专注时，谈到专注也不注入
    assert focus.focus_context(UID, "我想专注一会儿") == ""
    # 有进行中的专注 + 相关语境 → 注入并要求简短安静
    session = focus.start_focus(UID, 25)
    ctx = focus.focus_context(UID, "陪我学习，专注中别聊太远")
    assert "专注" in ctx and "简短" in ctx
    focus.cancel_focus(UID, session["id"])
    print("[OK] 语境门控：只在谈到专注时注入，普通闲聊零污染")


async def main() -> None:
    _test_state_machine()
    _test_mutual_exclusion()
    _test_lazy_completion_and_silence()
    _test_wrapup_eligibility()
    _test_focus_context_gating()
    print("\n=== M3.2 专注陪伴：全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
