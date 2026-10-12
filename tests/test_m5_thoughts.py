# -*- coding: utf-8 -*-
"""M5 未完成心事：确定性生产者、用户主权与可观测。"""
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
    _test_dismiss_and_cascade()
    print("\n=== M5 未完成心事：全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
