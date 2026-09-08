# -*- coding: utf-8 -*-
"""L05 领域信任：首值取全局 trust、幂等、日限幅、领域隔离、重置与导出。

验收锚点（docs/Zcode技术指导.md L05 + 调度文档批次 11）：
- 首次领域值 = 当时的全局 trust（不是 0）；
- 同事件同域幂等（唯一 user/event/domain），不双算；
- 日每域 ±4 截断；0–100；
- 领域互不影响，且不替代全局双维；
- GET 最多 2 条来源；DELETE 按当前全局 trust 重建；LC-1 导出恢复。
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_l05_"))

from backend.core import domain_trust as dt
from backend.core.userdb import db


_SEQ = 0


def _event(uid: str) -> int:
    global _SEQ
    _SEQ += 1
    with db._lock:
        promise = db.conn.execute(
            "INSERT INTO promises (user_id, content, follow_up, source, created_at) "
            "VALUES (?, ?, '', 'test', ?)",
            (uid, f"约定{_SEQ}", datetime.now().isoformat(timespec="seconds")),
        )
        cur = db.conn.execute(
            "INSERT INTO relationship_events (user_id, event_type, source_type, source_id, "
            "subject, object, payload_json, confidence, privacy, occurred_at, created_at) "
            "VALUES (?, 'promise_completed', 'promise', ?, ?, '约定', '{}', 1.0, 'normal', ?, ?)",
            (uid, int(promise.lastrowid), uid,
             datetime.now().isoformat(timespec="seconds"),
             datetime.now().isoformat(timespec="seconds")),
        )
        db.conn.commit()
    return int(cur.lastrowid)


def test_first_value_is_global_trust() -> int:
    uid = "l05-first"
    db.ensure_user(uid)
    with db._lock:
        db.conn.execute("UPDATE users SET trust=70, intimacy=60, affection=60 WHERE user_id=?", (uid,))
        db.conn.commit()
    snapshot = dt.get_snapshot(uid)
    values = {item["domain"]: item["value"] for item in snapshot["domains"]}
    assert all(v == 70 for v in values.values()), values
    assert {item["domain"] for item in snapshot["domains"]} == set(dt.DOMAINS)
    print("[OK] 首次领域值 = 全局 trust（不是 0）")
    return 0


def test_idempotent_and_daily_cap() -> int:
    uid = "l05-cap"
    db.ensure_user(uid)
    with db._lock:
        db.conn.execute("UPDATE users SET trust=50, intimacy=50, affection=50 WHERE user_id=?", (uid,))
        db.conn.commit()
    e1 = _event(uid)
    first = dt.apply_domain_event(uid, e1, "promise_confirmed")
    assert first["domain"] == "promise" and first["delta"] == 2
    again = dt.apply_domain_event(uid, e1, "promise_confirmed")
    assert again["idempotent"] is True and again["value"] == first["value"]
    # 同日同域上限 ±4
    for _ in range(5):
        dt.apply_domain_event(uid, _event(uid), "promise_confirmed")
    values = {item["domain"]: item["value"] for item in dt.get_snapshot(uid)["domains"]}
    assert values["promise"] == 54, values["promise"]
    print("[OK] 幂等 + 日每域 +4 截断")
    return 0


def test_domain_isolation_and_reasons() -> int:
    uid = "l05-isolation"
    db.ensure_user(uid)
    with db._lock:
        db.conn.execute("UPDATE users SET trust=60, intimacy=60, affection=60 WHERE user_id=?", (uid,))
        db.conn.commit()
    dt.apply_domain_event(uid, _event(uid), "boundary_respected")       # privacy +1
    dt.apply_domain_event(uid, _event(uid), "user_self_disclosure")     # emotional +1
    dt.apply_domain_event(uid, _event(uid), "confirmed_offense")        # emotional -2
    snapshot = {item["domain"]: item for item in dt.get_snapshot(uid)["domains"]}
    assert snapshot["privacy"]["value"] == 61
    assert snapshot["emotional"]["value"] == 59  # 60 +1 -2
    assert snapshot["task"]["value"] == 60, "未涉及的域不受影响"
    assert len(snapshot["emotional"]["reasons"]) <= 2
    # 重置按当前全局 trust 重建
    dt.reset_domain(uid, "emotional")
    after = {item["domain"]: item["value"] for item in dt.get_snapshot(uid)["domains"]}
    assert after["emotional"] == 60
    print("[OK] 领域隔离 / 来源最多 2 条 / 重置按全局 trust 重建")
    return 0


def test_reducer_single_entry() -> int:
    """唯一 reducer：apply_relationship_event 同时写全局与领域，不双算。"""
    from backend.core.affection import apply_relationship_event

    uid = "l05-reducer"
    db.ensure_user(uid)
    with db._lock:
        db.conn.execute("UPDATE users SET trust=50, intimacy=50, affection=50 WHERE user_id=?", (uid,))
        db.conn.commit()
    eid = _event(uid)
    trust, _ = apply_relationship_event(uid, eid, "promise_confirmed")
    assert trust == 52
    # 领域首值跟随「事件后的全局 trust」（52），再叠加本事件领域增量 +2
    values = {item["domain"]: item["value"] for item in dt.get_snapshot(uid)["domains"]}
    assert values["promise"] == 54, values["promise"]
    # 重复入账不二次加分
    trust2, _ = apply_relationship_event(uid, eid, "promise_confirmed")
    assert trust2 == 52
    assert {item["domain"]: item["value"] for item in dt.get_snapshot(uid)["domains"]}["promise"] == 54
    print("[OK] 唯一 reducer：全局与领域同一事件各算一次，重复入账不双算")
    return 0


def test_export_roundtrip() -> int:
    from backend.core.relationship_export import export_bundle, restore_bundle

    uid, target = "l05-export", "l05-import"
    db.ensure_user(uid)
    dt.apply_domain_event(uid, _event(uid), "promise_confirmed")
    bundle = export_bundle(uid, ["events", "tasks"])
    assert "domain_trust_snapshot" in bundle["data"], list(bundle["data"].keys())
    restore_bundle(bundle, target)
    row = db.conn.execute(
        "SELECT value FROM domain_trust_snapshot WHERE user_id=? AND domain='promise'",
        (target,)).fetchone()
    assert row is not None and int(row["value"]) >= 0
    print("[OK] LC-1：领域事件与快照随 events 类别导出→恢复")
    return 0


def main() -> int:
    failed = (
        test_first_value_is_global_trust()
        + test_idempotent_and_daily_cap()
        + test_domain_isolation_and_reasons()
        + test_reducer_single_entry()
        + test_export_roundtrip()
    )
    if failed:
        print(f"\n=== L05 领域信任：{failed} 项失败 ===")
        return 1
    print("\n=== L05 领域信任：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
