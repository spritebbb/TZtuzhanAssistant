# -*- coding: utf-8 -*-
"""每日批处理触发回归：连续聊天时昨天的批次必须被调度（2026-09-06 off-by-one 修复）。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_daily_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import affection  # noqa: E402
from backend.core.userdb import db  # noqa: E402

UID = "assistant-main"


def _insert_message(uid: str, day: date, content: str) -> None:
    with db._lock:
        db.conn.execute(
            "INSERT INTO messages (user_id, role, content, ts) VALUES (?, 'user', ?, ?)",
            (uid, content, f"{day.isoformat()}T12:00:00"),
        )
        db.conn.commit()


async def _test_consecutive_days_trigger() -> None:
    db.ensure_user(UID)
    today = date.today()
    yesterday = today - timedelta(days=1)
    _insert_message(UID, yesterday, "昨天聊了很多")
    db.set_chat_date(UID, yesterday.isoformat())  # 昨天聊过，今天还没记录

    scheduled: list[str] = []
    with patch("backend.core.affection.schedule", new=lambda key, fn: scheduled.append(key)):
        await affection.on_message(UID, "今天的第一句")

    assert f"daily:{UID}:{yesterday.isoformat()}" in scheduled, (
        f"连续聊天时昨天的每日总结必须被调度，实际调度了 {scheduled}"
    )
    assert db.get_user(UID)["last_chat_date"] == today.isoformat()
    print("[OK] 连续聊天：昨天的每日批处理被调度（off-by-one 修复）")


async def _test_multi_day_catchup_and_empty_days() -> None:
    uid = "catchup-user"
    db.ensure_user(uid)
    today = date.today()
    # 四天前和昨天聊过，中间两天沉默
    d4 = today - timedelta(days=4)
    d1 = today - timedelta(days=1)
    _insert_message(uid, d4, "四天前的话")
    _insert_message(uid, d1, "昨天的话")
    db.set_chat_date(uid, d1.isoformat())

    scheduled: list[str] = []
    with patch("backend.core.affection.schedule", new=lambda key, fn: scheduled.append(key)):
        await affection.on_message(uid, "回来了")

    assert f"daily:{uid}:{d4.isoformat()}" in scheduled, "四天前的批次应被补跑"
    assert f"daily:{uid}:{d1.isoformat()}" in scheduled, "昨天的批次应被补跑"
    # 中间空日只推进 last_batch_date 标记，不调度
    d2 = (today - timedelta(days=2)).isoformat()
    assert db.get_user(uid)["last_batch_date"] == d2, (
        f"空日应推进 last_batch_date 到 {d2}，实际 {db.get_user(uid)['last_batch_date']}"
    )
    assert not any("daily:" in key for key in scheduled if key.startswith(f"daily:{uid}:")
                   and key != f"daily:{uid}:{d4.isoformat()}" and key != f"daily:{uid}:{d1.isoformat()}"), (
        "空日不应调度批处理"
    )
    print("[OK] 多日补跑：有消息的日子补批处理，空日只推进标记")


async def _test_batch_marker_advances() -> None:
    uid = "marker-user"
    db.ensure_user(uid)
    today = date.today()
    yesterday = today - timedelta(days=1)
    _insert_message(uid, yesterday, "昨天的话")
    db.set_chat_date(uid, yesterday.isoformat())
    # 真实契约：batch 完成后 last_batch_date 与 kv done-key 同日推进
    db.set_batch_date(uid, yesterday.isoformat())
    with db._lock:
        db.conn.execute(
            "INSERT INTO kv_store (user_id, key, value) VALUES (?, ?, '1')",
            (uid, f"daily_batch:{yesterday.isoformat()}"),
        )
        db.conn.commit()

    scheduled: list[str] = []
    with patch("backend.core.affection.schedule", new=lambda key, fn: scheduled.append(key)):
        await affection.on_message(uid, "今天第一句")

    assert not scheduled, f"昨天已处理过（kv 标记在），不应重复调度：{scheduled}"
    print("[OK] 幂等：已处理过的日子不重复调度")


async def main() -> None:
    await _test_consecutive_days_trigger()
    await _test_multi_day_catchup_and_empty_days()
    await _test_batch_marker_advances()
    print("每日批处理触发回归通过")


if __name__ == "__main__":
    asyncio.run(main())
