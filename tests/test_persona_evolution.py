# -*- coding: utf-8 -*-
"""P3-05 演化日志与本地质量统计。

验收锚点（docs/Zcode技术指导.md P3-05 + 2026-09-07 拍板）：
- 白名单仅表达层三参数；非白名单参数被拒；
- 小步上限 ±0.1、24h 冷却、幂等（同 source_event 只演化一次）；
- 撤销重算：不覆盖历史，重放得到唯一当前值；
- 源事件删除/作废后对应偏移失效；
- 统计只聚合计数、临时轮不写、可关可清、不入关系包。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_p305_"))

from backend.core import experience_metrics as metrics
from backend.core import persona_evolution as evo
from backend.core.userdb import db


def _event(uid: str) -> int:
    with db._lock:
        promise = db.conn.execute(
            "INSERT INTO promises (user_id, content, follow_up, source, created_at) "
            "VALUES (?, ?, '', 'test', datetime('now'))",
            (uid, "演化来源约定"),
        )
        cur = db.conn.execute(
            "INSERT INTO relationship_events (user_id, event_type, source_type, source_id, "
            "subject, object, payload_json, confidence, privacy, occurred_at, created_at) "
            "VALUES (?, 'promise_completed', 'promise', ?, ?, ?, ?, 1.0, 'normal', "
            "datetime('now'), datetime('now'))",
            (uid, int(promise.lastrowid), uid, "约定", "{}"),
        )
        db.conn.commit()
    return int(cur.lastrowid)


def test_whitelist_and_step_cap() -> int:
    uid = "p305-cap"
    db.ensure_user(uid)
    try:
        evo.evolve(uid, "security_level", 0.5)
        raise AssertionError("非白名单参数应被拒")
    except ValueError:
        pass
    # 大步被 clamp 到 ±0.1
    r = evo.evolve(uid, "humor_usage_rate", 0.9)
    assert r["applied"] and abs(r["new"] - 0.6) < 1e-9, r
    # 冷却：24h 内不再演化（等过 1s 时钟精度门槛）
    time.sleep(1.1)
    r2 = evo.evolve(uid, "humor_usage_rate", 0.1)
    assert r2["applied"] is False and r2["reason"] == "cooldown"
    print("[OK] 白名单拒绝 / 小步上限 / 冷却")
    return 0


def test_idempotent_and_revert_recompute() -> int:
    uid = "p305-revert"
    db.ensure_user(uid)
    e1, e2 = _event(uid), _event(uid)
    r1 = evo.evolve(uid, "verbosity_preference", 0.1, source_event_id=e1)
    # 同一秒内连发两次合法演化（时钟精度为秒，视为同刻连发放行）
    r2 = evo.evolve(uid, "verbosity_preference", 0.1, source_event_id=e2)
    assert r1["applied"] and r2["applied"] and abs(r2["new"] - 0.7) < 1e-9
    # 幂等：同 source_event 再演化不动
    again = evo.evolve(uid, "verbosity_preference", 0.1, source_event_id=e1)
    assert again["applied"] is False and again["reason"] == "idempotent"
    # 撤销第一条 → 其后续条目（old=0.6 以被撤销的 new 为前提）同样失效，
    # 重算回到 base（0.5）；再撤销第二条仍是 base
    out = evo.revert(uid, r1["id"])
    assert abs(out["value"] - 0.5) < 1e-9, out
    # 撤销第二条 → 回到 base
    out2 = evo.revert(uid, r2["id"])
    assert abs(out2["value"] - 0.5) < 1e-9
    print("[OK] 幂等 / 撤销重算（不覆盖历史）")
    return 0


def test_source_death_invalidates_offset() -> int:
    uid = "p305-source"
    db.ensure_user(uid)
    e1 = _event(uid)
    r1 = evo.evolve(uid, "initiative_template_weight", 0.1, source_event_id=e1)
    assert abs(r1["new"] - 0.6) < 1e-9
    # 源事件作废 → 偏移失效，当前值回到 base
    with db._lock:
        db.conn.execute("UPDATE relationship_events SET status='invalidated' WHERE id=?", (e1,))
        db.conn.commit()
    assert abs(evo.current_value(uid, "initiative_template_weight") - 0.5) < 1e-9
    # 源删除同理
    e2 = _event(uid)
    evo.evolve(uid, "initiative_template_weight", -0.05, source_event_id=e2)
    with db._lock:
        db.conn.execute("DELETE FROM relationship_events WHERE id=?", (e2,))
        db.conn.commit()
    assert abs(evo.current_value(uid, "initiative_template_weight") - 0.5) < 1e-9
    print("[OK] 源删除/作废偏移失效")
    return 0


def test_metrics_aggregate_and_exclusion() -> int:
    uid = "p305-metrics"
    db.ensure_user(uid)
    assert metrics.record(uid, "latency", "<1s")
    assert metrics.record(uid, "latency", "<1s", count=2)
    assert metrics.record(uid, "rule_failure", "style_guard")
    assert metrics.record(uid, "not_a_kind", "x") is False
    assert metrics.record(uid, "source_pick", "knowledge_opinions")
    total = {s["kind"]: s["total"] for s in metrics.summary(uid)}
    assert total["latency"] == 3 and total["rule_failure"] == 1
    # 清理
    assert metrics.clear_user(uid) == 3
    assert metrics.summary(uid) == []
    # 不入关系包
    from backend.core.relationship_export import CATEGORIES

    assert "experience_metrics" not in CATEGORIES["life"]
    assert all("experience_metrics" not in tables for tables in CATEGORIES.values())
    print("[OK] 统计聚合 / 清理 / 不入关系包")
    return 0


def test_export_roundtrip_evolution() -> int:
    from backend.core.relationship_export import export_bundle, restore_bundle

    uid, target = "p305-export", "p305-import"
    db.ensure_user(uid)
    e1 = _event(uid)
    r1 = evo.evolve(uid, "humor_usage_rate", 0.05, source_event_id=e1)
    bundle = export_bundle(uid, ["life", "events", "tasks"])
    assert "persona_evolution_log" in bundle["data"], list(bundle["data"].keys())
    restore_bundle(bundle, target)
    # 恢复后重算值一致（事件也随 events 类别恢复，偏移有效）
    v = evo.current_value(target, "humor_usage_rate")
    assert abs(v - 0.55) < 1e-9, v
    assert r1["id"] > 0
    print("[OK] 演化历史随 life 导出恢复并重算")
    return 0


def main() -> int:
    failed = (
        test_whitelist_and_step_cap()
        + test_idempotent_and_revert_recompute()
        + test_source_death_invalidates_offset()
        + test_metrics_aggregate_and_exclusion()
        + test_export_roundtrip_evolution()
    )
    if failed:
        print(f"\n=== P3-05：{failed} 项失败 ===")
        return 1
    print("\n=== P3-05 演化与统计：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
