# -*- coding: utf-8 -*-
"""M1 事实自然衰减：到期清理、pinned 永存、幂等和跨日触发。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["TZTUZHAN_DATA_DIR"] = tempfile.mkdtemp(prefix="tztuzhan_test_decay_")
os.environ["MEMORY_V2"] = "0"

from backend.core import affection, daily  # noqa: E402
from backend.core.fact_decay import decay_expired_facts  # noqa: E402
from backend.core.relationship_events import record_memory_corrected  # noqa: E402
from backend.core.userdb import db  # noqa: E402

UID = "assistant-main"


def _seed(content: str, *, expires_at: str | None = None, pinned: bool = False) -> int:
    fact_id = db.add_fact(UID, content, expires_at=expires_at, pinned=pinned)
    assert fact_id is not None
    return fact_id


def _exists(fact_id: int) -> bool:
    with db._lock:
        return db.conn.execute("SELECT 1 FROM facts WHERE id = ?", (fact_id,)).fetchone() is not None


def _test_cascade_and_retention_boundaries() -> None:
    now = datetime.now().replace(microsecond=0)
    past = (now - timedelta(days=1)).isoformat(timespec="seconds")
    future = (now + timedelta(days=1)).isoformat(timespec="seconds")
    expired = _seed("用户最近改成了夜跑", expires_at=past)
    candidate = db.add_fact(
        UID, "用户不再夜跑", confidence=0.7, conflicts_with_fact_id=expired
    )
    assert candidate is not None
    record_memory_corrected(
        UID, expired, action="rewrite", old_content="夜跑", new_content="不夜跑"
    )
    pinned = _seed("用户对花生严重过敏", expires_at=past, pinned=True)
    future_fact = _seed("用户下周要去杭州", expires_at=future)
    permanent = _seed("用户喜欢秋天")
    boundary = _seed("用户今天临时换班", expires_at=now.isoformat(timespec="seconds"))

    with patch("backend.core.vector_store.delete") as vector_delete:
        deleted = decay_expired_facts(UID, now=now)

    assert deleted == [expired, boundary]
    assert not _exists(expired) and not _exists(candidate) and not _exists(boundary)
    assert _exists(pinned) and _exists(future_fact) and _exists(permanent)
    assert vector_delete.call_count == 3, "根事实、冲突候选和边界事实的向量都应删除"
    with db._lock:
        event = db.conn.execute(
            "SELECT status FROM relationship_events WHERE user_id = ? "
            "AND source_type = 'fact' AND source_id = ?",
            (UID, expired),
        ).fetchone()
    assert event is not None and event["status"] == "forgotten"
    print("[OK] 衰减边界：到期物理清理，冲突/事件/向量级联；pinned 与未到期保留")


def _test_idempotent() -> None:
    past = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    fact_id = _seed("用户最近在尝试早起", expires_at=past)
    with patch("backend.core.vector_store.delete") as vector_delete:
        assert decay_expired_facts(UID) == [fact_id]
        assert decay_expired_facts(UID) == []
    vector_delete.assert_called_once()
    print("[OK] 衰减幂等：重复运行不重复删向量")


async def _test_empty_batch_and_two_day_trigger() -> None:
    empty_uid = "decay-empty-day"
    db.ensure_user(empty_uid)
    past = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    fact_id = db.add_fact(empty_uid, "用户临时换了手机壳", expires_at=past)
    assert fact_id is not None
    await daily.run_daily_batch(empty_uid, date.today() - timedelta(days=1))
    assert not _exists(fact_id), "无消息日的 daily batch 也必须先做衰减"

    uid = "decay-returning-user"
    db.ensure_user(uid)
    two_days_ago = date.today() - timedelta(days=2)
    db.set_chat_date(uid, two_days_ago.isoformat())
    db.set_batch_date(uid, two_days_ago.isoformat())
    returning_fact = db.add_fact(uid, "用户临时在用夜班作息", expires_at=past)
    assert returning_fact is not None
    scheduled: dict[str, object] = {}

    def capture(key, factory):
        scheduled[key] = factory

    with patch("backend.core.affection.schedule", new=capture):
        await affection.on_message(uid, "今天回来了")
    decay_key = f"fact-decay:{uid}:{date.today().isoformat()}"
    assert decay_key in scheduled, "连续空日没有 daily batch 时，回来首句必须独立调度衰减"
    await scheduled[decay_key]()
    assert not _exists(returning_fact)
    print("[OK] 跨两天：空日 batch 可清理；全空档回来首句仍会独立触发")


async def main() -> None:
    db.ensure_user(UID)
    _test_cascade_and_retention_boundaries()
    _test_idempotent()
    await _test_empty_batch_and_two_day_trigger()
    print("\n=== M1 事实自然衰减回归全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
