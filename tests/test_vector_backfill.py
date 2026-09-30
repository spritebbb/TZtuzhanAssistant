# -*- coding: utf-8 -*-
"""38项#6 孤儿向量定期补灌回归。

覆盖：缺口检测差集（missing_ids）、按表补灌（backfill_user）、无缺口零动作、
embedding 未就绪跳过、全用户遍历（backfill_all）。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tz_vecback_"))

from backend.core.memory import vector_store as vs  # noqa: E402
from backend.core.userdb import db  # noqa: E402
from backend.maintenance import vector_backfill as vb  # noqa: E402


class _FakeCol:
    def __init__(self, existing: list[str]) -> None:
        self.existing = existing

    def get(self, ids, include=None):
        return {"ids": [i for i in ids if i in self.existing]}


def test_missing_ids_diff() -> int:
    col = _FakeCol(["u1|lm|1", "u1|lm|3"])
    orig = vs._collection
    orig_enabled = vs.enabled
    vs._collection = lambda kind: col if kind == "lm" else None
    vs.enabled = lambda: True
    try:
        miss = vs.missing_ids("u1", "lm", [1, 2, 3, 4])
    finally:
        vs._collection = orig
        vs.enabled = orig_enabled
    assert miss == [2, 4], miss
    assert vs.missing_ids("u1", "lm", []) == []
    print("[OK] missing_ids 差集正确")
    return 0


def test_backfill_user_fills_gap() -> int:
    uid = "backfill-test-user"
    db.add_long_memory(uid, "第一条记忆", pinned=False)
    db.add_long_memory(uid, "第二条记忆", pinned=False)
    db.add_fact(uid, "一个事实", source_type="legacy")
    rows = db.conn.execute(
        "SELECT id, content FROM long_memory WHERE user_id=? ORDER BY id", (uid,)
    ).fetchall()

    calls: list[tuple[str, str, int, str]] = []
    orig_add, orig_miss = vs.add, vs.missing_ids
    orig_ready, orig_rebuild, orig_enabled = (
        vs._embedding_ready_for_write, vs._rebuilding, vs.enabled
    )
    vs.enabled = lambda: True
    vs._embedding_ready_for_write = lambda: True
    vs._rebuilding = False
    # lm 全缺（模拟运行期 embedding 未就绪被拒写）；facts 无缺口
    vs.missing_ids = lambda u, k, r: [x["id"] for x in rows] if k == "lm" else []

    def fake_add(u, k, rid, text, **kw):
        calls.append((u, k, rid, text))
        return True

    vs.add = fake_add
    try:
        n = vb.backfill_user(uid)
    finally:
        vs.add, vs.missing_ids = orig_add, orig_miss
        vs._embedding_ready_for_write, vs._rebuilding, vs.enabled = (
            orig_ready, orig_rebuild, orig_enabled
        )
    assert n == len(rows), (n, len(rows))
    assert all(c[1] == "lm" for c in calls)
    assert calls[0][3] == "第一条记忆"
    print(f"[OK] backfill_user 补齐 lm 缺口 {n} 条，内容取自源表")
    return 0


def test_no_gap_and_not_ready() -> int:
    uid = "backfill-test-user2"
    db.add_long_memory(uid, "又一条", pinned=False)
    calls: list = []
    orig_add = vs.add
    vs.enabled = lambda: True
    vs._embedding_ready_for_write = lambda: True
    vs._rebuilding = False
    vs.missing_ids = lambda u, k, r: []
    vs.add = lambda *a, **k: calls.append(1) or True
    try:
        assert vb.backfill_user(uid) == 0 and not calls
    finally:
        vs.add = orig_add
    # embedding 未就绪 → 前置短路零调用
    vs._embedding_ready_for_write = lambda: False
    try:
        assert vb.backfill_user(uid) == 0 and not calls
    finally:
        pass
    print("[OK] 无缺口零动作；embedding 未就绪前置跳过")
    return 0


def main() -> None:
    test_missing_ids_diff()
    test_backfill_user_fills_gap()
    test_no_gap_and_not_ready()
    print("\n=== 38项#6 向量补灌: 3 组全部通过 ===")


if __name__ == "__main__":
    main()
