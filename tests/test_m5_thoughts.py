# -*- coding: utf-8 -*-
"""M5 未完成心事：确定性生产者、Planner 门控、每日一条表达、用户主权与可观测。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_m5_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import pending_thoughts as thoughts  # noqa: E402
from backend.core.narrative_planner import plan_next  # noqa: E402
from backend.core.userdb import db, save_promise  # noqa: E402

from backend.core import features  # noqa: E402
# D12：本组断言「投递成功」，意愿骰子按分钟播种会随机假红；骰子另有专测，这里关掉。
features.set_flag("willingness_enabled", False)

UID = "assistant-main"
NOW = datetime.now()


def _old_updated(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat(timespec="seconds")


def _test_producers_and_idempotency() -> None:
    # 共读搁置 5 天 → resume_reading 心事；同源幂等
    with db._lock:
        now = NOW.isoformat(timespec="seconds")
        cur = db.conn.execute(
            "INSERT INTO activities (user_id, kind, document_id, title, status, position, created_at, updated_at) "
            "VALUES (?, 'reading', 1, '共读《温室手记》', 'paused', 2, ?, ?)",
            (UID, now, _old_updated(5)),
        )
        activity_id = int(cur.lastrowid)
        db.conn.execute(
            "INSERT INTO kb_documents (user_id, filename, stored_path, format, size_bytes, chunk_count, ts) "
            "VALUES (?, '温室手记.txt', '', 'txt', 10, 3, ?)",
            (UID, now),
        )
        db.conn.commit()
    added = thoughts.sync_pending_thoughts(UID)
    assert added >= 1
    assert thoughts.sync_pending_thoughts(UID) == 0, "同一来源一生只挂一次心事"

    found = [t for t in thoughts.due_thoughts(UID) if t["kind"] == "resume_reading"]
    assert found and "温室手记" in found[0]["content"]

    # 刚开始的共读不挂心事（未到 3 天）
    with db._lock:
        db.conn.execute(
            "INSERT INTO activities (user_id, kind, document_id, title, status, position, created_at, updated_at) "
            "VALUES (?, 'reading', 1, '共读《新书》', 'paused', 0, ?, ?)",
            (UID, now, now),
        )
        db.conn.commit()
    assert not any("新书" in t["content"] for t in thoughts.due_thoughts(UID))
    print("[OK] 生产者：搁置共读挂心事且幂等；未到时机的不挂")


def _test_planner_gating() -> None:
    # 初识阶段：只允许 resume_reading
    thought = plan_next(UID, "初识")
    assert thought is not None and thought["kind"] == "resume_reading"

    # 最早可表达时间未到 → 不出现
    with db._lock:
        db.conn.execute(
            "INSERT INTO pending_thoughts (user_id, kind, source_type, source_id, content, "
            "earliest_at, expires_at, priority, created_at) "
            "VALUES (?, 'confirm_memory', 'fact', 999, '想确认记忆', ?, ?, 1, ?)",
            (UID, (NOW + timedelta(hours=3)).isoformat(timespec="seconds"),
             (NOW + timedelta(days=2)).isoformat(timespec="seconds"), now_iso := NOW.isoformat(timespec="seconds")),
        )
        db.conn.commit()
    future = [t for t in thoughts.due_thoughts(UID) if t["content"] == "想确认记忆"]
    assert not future, "未到 earliest_at 的心事不能表达"

    # 熟悉阶段：可表达 confirm_memory（构造一条已到点的）
    with db._lock:
        db.conn.execute("DELETE FROM pending_thoughts WHERE source_id = 999")
        db.conn.commit()
    from backend.core.relationship_events import record_memory_corrected

    record_memory_corrected(UID, 42, action="rewrite", old_content="旧", new_content="新")
    thoughts.sync_pending_thoughts(UID)
    familiar = plan_next(UID, "熟悉")
    assert familiar is not None
    print("[OK] Planner：初识只挂非私人化心事；earliest_at 门控生效")


async def _test_express_flow() -> None:
    from backend.core import initiative

    db.set_affection_absolute(UID, 60)  # 熟悉
    captured: dict = {}

    async def fake_chat(messages, **kwargs):
        captured["messages"] = messages
        return "那本温室手记，你后来读到哪儿了"

    original_chat = initiative.chat
    initiative.chat = fake_chat
    try:
        text = await initiative.maybe_express_pending_thoughts(UID)
    finally:
        initiative.chat = original_chat
    assert text and "温室手记" in text
    assert captured["messages"][-1]["role"] == "user"
    stats = thoughts.stats(UID)
    assert stats["expressed"] >= 1 and stats["pending"] >= 0

    # 每日最多一条：同日再调用不再表达
    text2 = await initiative.maybe_express_pending_thoughts(UID)
    assert text2 is None
    print("[OK] 表达链路：走主动队列、每日一条、状态落账（可观测）")


def _test_dismiss_and_cascade() -> None:
    # 用户主权：放下一条 pending 心事
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO pending_thoughts (user_id, kind, source_type, source_id, content, "
            "earliest_at, created_at) VALUES (?, 'confirm_memory', 'fact', 777, '想确认', ?, ?)",
            (UID, NOW.isoformat(timespec="seconds"), NOW.isoformat(timespec="seconds")),
        )
        thought_id = int(cur.lastrowid)
        db.conn.commit()
    assert thoughts.dismiss_thought(UID, thought_id)
    assert thoughts.dismiss_thought(UID, thought_id) is False  # 已处理不可重复

    # 来源消失 → 心事作废（不留幽灵惦记）
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO pending_thoughts (user_id, kind, source_type, source_id, content, "
            "earliest_at, created_at) VALUES (?, 'resume_reading', 'activity', 555, '惦记书', ?, ?)",
            (UID, NOW.isoformat(timespec="seconds"), NOW.isoformat(timespec="seconds")),
        )
        cascade_id = int(cur.lastrowid)
        db.conn.commit()
    thoughts.forget_thoughts_for_source(UID, "activity", 555)
    with db._lock:
        status = db.conn.execute(
            "SELECT status FROM pending_thoughts WHERE id = ?", (cascade_id,)
        ).fetchone()["status"]
    assert status == "dismissed"
    print("[OK] 用户主权：可放下心事；来源删除级联作废")


async def main() -> None:
    _test_producers_and_idempotency()
    _test_planner_gating()
    await _test_express_flow()
    _test_dismiss_and_cascade()
    print("\n=== M5 未完成心事：全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
