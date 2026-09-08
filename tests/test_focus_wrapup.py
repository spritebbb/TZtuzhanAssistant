# -*- coding: utf-8 -*-
"""F04 专注收尾人格化与可重试投递：素材、投递箱、重试与取消语义。

验收锚点（docs/Zcode技术指导.md F04 + 调度文档批次 8）：
- build_wrapup_material 只取真实已用时长/中断/用户显式目标/行为帧，≤100 字，
  不评分、不声称完成目标；
- wrapup_outbox 唯一 user/activity；完成事件先写、收尾同事务入箱；
- 重试 ≤2 次，用尽后用确定性短回顾；投递成功置 sent（delivery_id 去重语义）；
- 取消的活动永不发收尾；API 超时不回滚已完成的专注。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_f04_"))

from backend.core import focus
from backend.core.userdb import db

NOW = datetime(2026, 9, 8, 14, 0)


def _focus_activity(uid: str, *, planned: int = 25, elapsed: int = 1500,
                    status: str = "active") -> int:
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO activities (user_id, kind, document_id, title, status, position, "
            "planned_minutes, remaining_seconds, created_at, updated_at) "
            "VALUES (?, 'focus', 0, '写方案', ?, 0, ?, 0, ?, ?)",
            (uid, status, planned, NOW.isoformat(timespec="seconds"),
             NOW.isoformat(timespec="seconds")),
        )
        activity_id = int(cur.lastrowid)
        if status == "completed":
            db.conn.execute(
                "UPDATE activities SET completed_at=? WHERE id=?",
                ((NOW + timedelta(minutes=planned)).isoformat(timespec="seconds"), activity_id),
            )
        db.conn.commit()
    return activity_id


def test_material_is_real_only() -> int:
    detail = {
        "id": 1, "status": "completed", "elapsed_seconds": 1500,
        "planned_minutes": 25, "goal": "把方案写完",
    }
    text = focus.build_wrapup_material(detail)
    assert "25 分钟" in text and "把方案写完" in text, text
    assert len(text) <= focus._WRAPUP_MATERIAL_MAX
    # 不评分、不声称完成
    assert "完成" not in text.replace("把方案写完", ""), text
    empty = focus.build_wrapup_material({"status": "completed", "elapsed_seconds": 0})
    assert empty == ""
    print("[OK] 素材只取真实时长/目标，≤100 字且不评分")
    return 0


def test_outbox_on_complete_and_cancel() -> int:
    uid = "f04-outbox"
    db.ensure_user(uid)
    aid = _focus_activity(uid, status="active")
    with db._lock:
        db.conn.execute(
            "UPDATE activities SET ends_at=? WHERE id=?",
            ((NOW + timedelta(minutes=10)).isoformat(timespec="seconds"), aid))
        db.conn.commit()
    detail = focus.complete_focus(uid, aid)
    assert detail["status"] == "completed"
    row = focus.wrapup_pending(uid, aid)
    assert row is not None and row["status"] == "pending"
    assert row["delivery_id"] == f"wrapup:{uid}:{aid}"
    assert row["attempt"] == 0
    # 幂等：再次入箱不新增（唯一 user/activity）
    with db._lock:
        focus._enqueue_wrapup_locked(uid, aid, detail, focus._now())
    assert db.conn.execute(
        "SELECT COUNT(*) n FROM wrapup_outbox WHERE user_id=?", (uid,)).fetchone()["n"] == 1

    # 取消的活动不入箱/不投递
    uid2 = "f04-cancel"
    db.ensure_user(uid2)
    aid2 = _focus_activity(uid2, status="active")
    focus.cancel_focus(uid2, aid2)
    assert focus.wrapup_pending(uid2, aid2) is None
    print("[OK] 完成即入箱且唯一；取消的活动不产生收尾")
    return 0


def test_retry_budget_and_fallback() -> int:
    uid = "f04-retry"
    db.ensure_user(uid)
    aid = _focus_activity(uid)
    detail = focus.complete_focus(uid, aid)
    assert focus.wrapup_pending(uid, aid)["status"] == "pending"
    # 第一次失败：保持 pending 等重试
    with patch("backend.core.llm.chat", new=AsyncMock(return_value="")), \
         patch("backend.core.initiative.enqueue_proactive", new=AsyncMock(return_value=True)):
        assert asyncio.run(focus.maybe_send_wrapup(uid, aid)) is False
    assert focus.wrapup_pending(uid, aid)["status"] == "pending"
    # 第二次（用尽预算）：确定性短回顾并投递成功
    sent: list[str] = []

    async def _capture(_uid, text, **kwargs):
        sent.append(text)
        return True

    with patch("backend.core.llm.chat", new=AsyncMock(return_value="")), \
         patch("backend.core.initiative.enqueue_proactive", new=_capture):
        assert asyncio.run(focus.maybe_send_wrapup(uid, aid)) is True
    assert sent and "起来动一动" in sent[0], sent
    assert focus.wrapup_pending(uid, aid)["status"] == "sent"
    # 已投递：不再重复
    with patch("backend.core.initiative.enqueue_proactive", new=AsyncMock(return_value=True)):
        assert asyncio.run(focus.maybe_send_wrapup(uid, aid)) is False
    print("[OK] 重试 ≤2 次 + 确定性短回顾；投递后不重复")
    return 0


def test_completed_focus_survives_api_timeout() -> int:
    """API 超时不回滚已完成的专注（完成与入箱先提交）。"""
    uid = "f04-timeout"
    db.ensure_user(uid)
    aid = _focus_activity(uid)
    focus.complete_focus(uid, aid)
    with patch("backend.core.llm.chat", new=AsyncMock(side_effect=RuntimeError("timeout"))):
        asyncio.run(focus.maybe_send_wrapup(uid, aid))  # 投递失败不影响完成态
    row = db.conn.execute(
        "SELECT status FROM activities WHERE id=?", (aid,)).fetchone()
    assert row["status"] == "completed"
    assert focus.wrapup_pending(uid, aid)["status"] in {"pending", "failed"}
    print("[OK] 投递失败不回滚已完成的专注")
    return 0


def main() -> int:
    failed = (
        test_material_is_real_only()
        + test_outbox_on_complete_and_cancel()
        + test_retry_budget_and_fallback()
        + test_completed_focus_survives_api_timeout()
    )
    if failed:
        print(f"\n=== F04 专注收尾：{failed} 项失败 ===")
        return 1
    print("\n=== F04 专注收尾：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
