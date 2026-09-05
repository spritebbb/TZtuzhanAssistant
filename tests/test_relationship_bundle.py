# -*- coding: utf-8 -*-
"""M1/E03 关系导出与恢复：计数与引用一致、空命名空间约束、kv 登记表覆盖。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_bundle_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import relationship_export as rex  # noqa: E402
from backend.core.kv_registry import KV_KEY_SPECS, match_spec  # noqa: E402
from backend.core.proactive_policy import mark_active_done  # noqa: E402
from backend.core.userdb import db, kv_set, save_promise  # noqa: E402

UID = "export-user"


def _seed(uid: str) -> None:
    db.ensure_user(uid)
    save_promise(uid, "用户答应周五把 demo 发给菟菚看", follow_up=date.today().isoformat())
    now = "2026-09-06T12:00:00"
    with db._lock:
        db.conn.execute(
            "INSERT INTO facts (user_id, content, ts, confidence) VALUES (?, ?, ?, 1.0)",
            (uid, "对方在整理作品集", now),
        )
        cur = db.conn.execute(
            "INSERT INTO activities (user_id, kind, document_id, title, status, position, created_at, updated_at) "
            "VALUES (?, 'writing', 0, '灯塔看守人的猫', 'completed', 0, ?, ?)",
            (uid, now, now),
        )
        activity_id = int(cur.lastrowid)
        db.conn.execute(
            "INSERT INTO activity_writings (activity_id, user_id, premise, created_at, updated_at) "
            "VALUES (?, ?, '一座只会亮一次的灯塔', ?, ?)",
            (activity_id, uid, now, now),
        )
        db.conn.execute(
            "INSERT INTO writing_turns (user_id, activity_id, author, content, ts) "
            "VALUES (?, ?, 'user', '猫在第七天开始学着数浪。', ?)",
            (uid, activity_id, now),
        )
        db.conn.execute(
            "INSERT INTO artifacts (user_id, artifact_type, source_type, source_id, title, "
            "content, version, created_at, updated_at) "
            "VALUES (?, 'co_story', 'activity', ?, '《灯塔看守人的猫》共同故事', '正文', 1, ?, ?)",
            (uid, activity_id, now, now),
        )
        db.conn.execute(
            "INSERT INTO relationship_events (user_id, event_type, source_type, source_id, "
            "subject, object, occurred_at, created_at) "
            "VALUES (?, 'story_finished', 'activity', ?, ?, '灯塔看守人的猫', ?, ?)",
            (uid, activity_id, uid, now, now),
        )
        db.conn.execute(
            "INSERT INTO messages (user_id, role, content, ts) VALUES (?, 'user', '开始写故事吧', ?)",
            (uid, now),
        )
        db.conn.commit()
    kv_set(uid, "state:emotion_memory", '{"mood": "雀跃"}')
    kv_set(uid, "web_last_seen", "1757123456.0")
    mark_active_done(uid, "initiative-loop")


def _test_export_counts_and_kv_registry() -> None:
    bundle = rex.export_bundle(UID)
    counts = {t: n for tables in bundle["counts"].values() for t, n in tables.items()}
    for table in ("promises", "facts", "activities", "activity_writings",
                  "writing_turns", "artifacts", "relationship_events", "messages"):
        assert counts.get(table, 0) >= 1, f"{table} 应被导出"
    assert "usage_log" not in bundle["data"], "成本账本不属于关系记忆，不导出"
    # kv 按登记表枚举：持久关系状态导出，每日额度/运行锚点跳过
    assert bundle["kv"]["exported"].get("state:emotion_memory") == '{"mood": "雀跃"}'
    assert "web_last_seen" not in bundle["kv"]["exported"]
    assert not any(key.startswith("proactive:") for key in bundle["kv"]["exported"])
    assert rex.unregistered_kv_keys(UID) == [], f"kv 出现未登记键：{rex.unregistered_kv_keys(UID)}"
    # 登记表本身无重复模式
    patterns = [spec.pattern for spec in KV_KEY_SPECS]
    assert len(patterns) == len(set(patterns)), "登记表存在重复键模式"
    assert match_spec("state:emotion_memory") is not None
    assert match_spec("proactive:done:2099-01-01") is not None
    print("[OK] 导出：计数完整、成本账本排除、kv 按登记表枚举且无未登记键")


def _test_preview_and_restore_consistency() -> None:
    bundle = rex.export_bundle(UID)
    raw = rex.bundle_to_json(bundle)
    parsed = rex.bundle_from_json(raw)
    target = "restored-user"
    preview = rex.preview_restore(parsed, target)
    assert preview["ok"], preview["errors"]
    assert preview["total"] == sum(
        n for tables in bundle["counts"].values() for n in tables.values()
    ), "预览计数应与导出计数一致"

    result = rex.restore_bundle(parsed, target)
    assert result["ok"] and result["total"] == preview["total"]

    with db._lock:
        def count(table: str, uid: str) -> int:
            return int(db.conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (uid,)
            ).fetchone()[0])
        # 关键计数一致
        for table in ("promises", "facts", "activities", "artifacts",
                      "relationship_events", "messages", "writing_turns"):
            assert count(table, target) == count(table, UID), f"{table} 计数不一致"
        # 引用一致：活动 → 侧表 → 产物 → 事件全链路
        refs = db.conn.execute(
            "SELECT w.activity_id, a.user_id FROM writing_turns w "
            "JOIN activities a ON a.id = w.activity_id AND a.user_id = w.user_id "
            "WHERE w.user_id = ?",
            (target,),
        ).fetchall()
        assert refs and refs[0]["user_id"] == target, "恢复后 user_id 应重写为目标命名空间"
        artifact_ref = db.conn.execute(
            "SELECT source_id FROM artifacts WHERE user_id = ? AND artifact_type = 'co_story'",
            (target,),
        ).fetchone()
        event_ref = db.conn.execute(
            "SELECT source_id FROM relationship_events WHERE user_id = ? AND event_type = 'story_finished'",
            (target,),
        ).fetchone()
        assert artifact_ref and event_ref
        activity_ids = {int(r["id"]) for r in db.conn.execute(
            "SELECT id FROM activities WHERE user_id = ?", (target,)
        ).fetchall()}
        assert int(artifact_ref["source_id"]) in activity_ids
        assert int(event_ref["source_id"]) in activity_ids
        assert int(db.conn.execute(
            "SELECT COUNT(*) FROM kv_store WHERE user_id = ? AND key = 'state:emotion_memory'",
            (target,),
        ).fetchone()[0]) == 1
        assert count("users", target) == 1
    # 目标命名空间已非空：再恢复必须拒绝
    try:
        rex.restore_bundle(parsed, target)
        raise AssertionError("非空命名空间不应允许再次恢复")
    except rex.BundleError:
        pass
    print("[OK] 恢复：新命名空间计数与引用一致；重复恢复被拒绝")


def _test_validation_rejects_bad_bundles() -> None:
    bundle = rex.export_bundle(UID)
    # 引用断裂：删掉 activities 但保留 activity_writings
    broken = rex.bundle_from_json(rex.bundle_to_json(bundle))
    broken["data"]["activities"] = []
    preview = rex.preview_restore(broken, "fresh-target")
    assert not preview["ok"] and any("引用断裂" in e for e in preview["errors"])
    # schema 版本不一致
    wrong = rex.bundle_from_json(rex.bundle_to_json(bundle))
    wrong["schema_version"] = 1
    preview = rex.preview_restore(wrong, "fresh-target")
    assert not preview["ok"] and any("schema" in e for e in preview["errors"])
    # 非法类别
    try:
        rex.export_bundle(UID, ["nonexistent"])
        raise AssertionError("未知类别应报错")
    except rex.BundleError:
        pass
    # 坏 JSON
    try:
        rex.bundle_from_json(b"{not json")
        raise AssertionError("坏 JSON 应报错")
    except rex.BundleError:
        pass
    print("[OK] 校验：引用断裂/版本不一致/未知类别/坏 JSON 均被拒绝")


async def main() -> None:
    _seed(UID)
    _test_export_counts_and_kv_registry()
    _test_preview_and_restore_consistency()
    _test_validation_rejects_bad_bundles()
    print("关系导出与恢复 E03 测试通过")


if __name__ == "__main__":
    asyncio.run(main())
