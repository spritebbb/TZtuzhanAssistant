# -*- coding: utf-8 -*-
"""M8 关系快照：资格边界（未满/正好/超过）、cutoff 过滤（cutoff 后来源、
非 normal/失效事件、自身 artifact 不进入）、确定性汇编与 caps/omitted、
显式创建幂等、删除后 eligible 恢复且绝不自动再生、多人格隔离、reset、
导出恢复引用映射、feature flag 与 HTTP 渐进降级。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_snaps_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import relationship_export as rex  # noqa: E402
from backend.core import relationship_snapshots as rsnap  # noqa: E402
from backend.core.relationship_events import record as record_event  # noqa: E402
from backend.core.userdb import db, save_diary  # noqa: E402

UID = "assistant-main"
UID2 = "persona-two"
UID3 = "persona-three"
UID4 = "persona-export"


def _rows(sql: str, params: tuple) -> list:
    with db._lock:
        return db.conn.execute(sql, params).fetchall()


def _add_user_message(uid: str, ts: str, content: str = "你好呀") -> int:
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO messages (user_id, role, content, ts) VALUES (?, 'user', ?, ?)",
            (uid, content, ts),
        )
        db.conn.commit()
        return int(cur.lastrowid)


def _add_event(
    uid: str,
    source_id: int,
    *,
    occurred_at: str,
    event_type: str = "promise_completed",
    privacy: str = "normal",
    obj: str = "",
    expires_at: str | None = None,
) -> int | None:
    source_type = "important_date" if event_type == "important_date" else "promise"
    return record_event(
        uid, event_type, source_type, source_id,
        obj=obj, privacy=privacy, occurred_at=occurred_at, expires_at=expires_at,
    )


def _add_artifact(
    uid: str,
    source_id: int,
    created_at: str,
    *,
    artifact_type: str = "book_summary",
    title: str,
    status: str = "active",
) -> int:
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO artifacts (user_id, artifact_type, source_type, source_id, title, "
            "content, version, created_at, updated_at, status) "
            "VALUES (?, ?, 'activity', ?, ?, '真实产物内容', 1, ?, ?, ?)",
            (uid, artifact_type, source_id, title, created_at, created_at, status),
        )
        db.conn.commit()
        return int(cur.lastrowid)


def _add_viewpoint(uid: str, activity_id: int, role: str, position: int, content: str, ts: str) -> int:
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO activity_viewpoints (user_id, activity_id, role, position, content, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uid, activity_id, role, position, content, ts),
        )
        db.conn.commit()
        return int(cur.lastrowid)


def _add_term(uid: str, term: str, count: int, first_seen: str, last_seen: str) -> int:
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO user_terms (user_id, term, category, meaning, count, first_seen, last_seen) "
            "VALUES (?, ?, 'catchphrase', '', ?, ?, ?)",
            (uid, term, count, first_seen, last_seen),
        )
        db.conn.commit()
        return int(cur.lastrowid)


def _snap_artifacts(uid: str, snapshot_id: int) -> list:
    return _rows(
        "SELECT id FROM artifacts WHERE user_id = ? AND artifact_type = 'relationship_snapshot' "
        "AND source_id = ?",
        (uid, snapshot_id),
    )


def _markdown_head(markdown: str) -> str:
    return markdown.split("\n---\n")[0]


def _test_state_without_messages() -> None:
    state = rsnap.snapshot_state(UID3)
    assert state["start_date"] is None
    assert state["snapshots"] == []
    assert all(not m["eligible"] for m in state["milestones"]), "没有 user 消息就没有资格"
    try:
        rsnap.create_snapshot(UID3, 30)
        raise AssertionError("应当报错：第一句对话")
    except rsnap.RelationshipSnapshotError as exc:
        assert "第一句对话" in str(exc)
    print("[OK] 无 user 消息：无资格、GET 只读、创建被拒")


def _test_eligibility_boundaries() -> None:
    today = date.today()
    start = today - timedelta(days=28)
    _add_user_message(UID, f"{start.isoformat()}T09:00:00")
    db.ensure_user(UID)
    # 连续聊天 / last_chat_date / last_batch_date 都不应干扰起点计算
    db.set_chat_date(UID, today.isoformat(), today.isoformat())
    db.set_batch_date(UID, today.isoformat())

    state = rsnap.snapshot_state(UID)
    assert state["start_date"] == start.isoformat()
    assert state["days_since"] == 29, "28 天前开始 → 今天是第 29 天（自然日含起始日）"
    assert not state["milestones"][0]["eligible"], "29 < 30：未满里程碑"

    # 更晚的连续聊天不改变起点
    _add_user_message(UID, f"{today.isoformat()}T12:00:00")
    assert rsnap.snapshot_state(UID)["start_date"] == start.isoformat()

    # 正好达到：首条消息移到 29 天前 → 今天第 30 天
    new_start = today - timedelta(days=29)
    with db._lock:
        db.conn.execute(
            "UPDATE messages SET ts = ? WHERE user_id = ? AND role = 'user'",
            (f"{new_start.isoformat()}T09:00:00", UID),
        )
        db.conn.commit()
    state = rsnap.snapshot_state(UID)
    m30, m100, m365 = state["milestones"]
    assert state["days_since"] == 30
    assert m30["eligible"] and not m100["eligible"] and not m365["eligible"], "超过边界：100/365 仍不可"

    for bad_days, needle in ((45, "三种"), (100, "还没到"), (365, "还没到")):
        try:
            rsnap.create_snapshot(UID, bad_days)
            raise AssertionError(f"应当报错：{needle}")
        except rsnap.RelationshipSnapshotError as exc:
            assert needle in str(exc), f"错误信息不符：{exc}"

    snap = rsnap.create_snapshot(UID, 30)
    assert snap["snapshot_days"] == 30
    assert snap["cutoff_date"] == today.isoformat(), "第 30 天当天整理，cutoff 就是今天"
    assert snap["rendered_markdown"] and snap["source_manifest"]["sections"]
    again = rsnap.create_snapshot(UID, 30)
    assert again["id"] == snap["id"], "重复 POST 幂等"
    assert again["generated_at"] == snap["generated_at"] and again["rendered_markdown"] == snap["rendered_markdown"]
    assert len(_snap_artifacts(UID, snap["id"])) == 1, "artifact 幂等 upsert，至多一条"
    print("[OK] 资格边界：未满/正好/超过、起点只看最早 user 消息、POST 幂等")


def _test_cutoff_content() -> None:
    today = date.today()
    start = today - timedelta(days=39)
    cutoff = start + timedelta(days=29)
    assert cutoff == today - timedelta(days=10), "第 40 天整理：30 天 cutoff 已过去 10 天"
    _add_user_message(UID2, f"{start.isoformat()}T09:00:00")

    ev_in = _add_event(UID2, 101, occurred_at=f"{(cutoff - timedelta(days=1)).isoformat()}T10:00:00", obj="一起晨跑")
    ev_after = _add_event(UID2, 102, occurred_at=f"{(cutoff + timedelta(days=1)).isoformat()}T10:00:00", obj="cutoff 之后的事")
    ev_secret = _add_event(UID2, 103, occurred_at=f"{(cutoff - timedelta(days=2)).isoformat()}T10:00:00", privacy="sensitive", obj="秘密的事")
    ev_forgotten = _add_event(UID2, 104, occurred_at=f"{(cutoff - timedelta(days=2)).isoformat()}T10:00:00", obj="已作废的事")
    with db._lock:
        db.conn.execute("UPDATE relationship_events SET status = 'forgotten' WHERE id = ?", (ev_forgotten,))
        db.conn.commit()
    ev_expired = _add_event(
        UID2, 105, event_type="important_date",
        occurred_at=f"{(cutoff - timedelta(days=20)).isoformat()}T00:00:00",
        obj="早已过期的日子", expires_at=f"{(cutoff - timedelta(days=13)).isoformat()}T00:00:00",
    )
    ev_alive = _add_event(
        UID2, 106, event_type="important_date",
        occurred_at=f"{(cutoff - timedelta(days=20)).isoformat()}T00:00:00",
        obj="还活着的纪念日", expires_at=f"{(cutoff + timedelta(days=7)).isoformat()}T00:00:00",
    )

    art_in = _add_artifact(UID2, 201, f"{(cutoff - timedelta(days=1)).isoformat()}T12:00:00", title="《旧文档》共同书摘")
    art_after = _add_artifact(UID2, 202, f"{(cutoff + timedelta(days=1)).isoformat()}T12:00:00", artifact_type="goal_review", title="新目标回顾")
    art_self = _add_artifact(UID2, 203, f"{(cutoff - timedelta(days=1)).isoformat()}T12:00:00", artifact_type="relationship_snapshot", title="快照自身的产物")

    vp_in = _add_viewpoint(UID2, 1, "user", 1, "我觉得这一段写得很好", f"{(cutoff - timedelta(days=1)).isoformat()}T15:00:00")
    vp_after = _add_viewpoint(UID2, 1, "tuzhan", 1, "我更喜欢结尾", f"{(cutoff + timedelta(days=1)).isoformat()}T15:00:00")

    term_in = _add_term(UID2, "绝绝子", 3, f"{(cutoff - timedelta(days=5)).isoformat()}T09:00:00", f"{(cutoff - timedelta(days=1)).isoformat()}T09:00:00")
    _add_term(UID2, "一次性", 1, f"{(cutoff - timedelta(days=5)).isoformat()}T09:00:00", f"{(cutoff - timedelta(days=1)).isoformat()}T09:00:00")
    _add_term(UID2, "新词", 5, f"{(cutoff - timedelta(days=5)).isoformat()}T09:00:00", f"{(cutoff + timedelta(days=1)).isoformat()}T09:00:00")

    diary_in = save_diary(UID2, (cutoff - timedelta(days=1)).isoformat(), "那天我们在读同一本书")
    diary_after = save_diary(UID2, (cutoff + timedelta(days=1)).isoformat(), "cutoff 之后的日记")

    snap = rsnap.create_snapshot(UID2, 30)
    assert snap["cutoff_date"] == cutoff.isoformat()
    md = snap["rendered_markdown"]
    for included in ("一起晨跑", "还活着的纪念日", "《旧文档》共同书摘", "我觉得这一段写得很好", "「绝绝子」", "那天我们在读同一本书"):
        assert included in md, f"应收录：{included}"
    for excluded in ("cutoff 之后的事", "秘密的事", "已作废的事", "早已过期的日子", "新目标回顾",
                     "快照自身的产物", "我更喜欢结尾", "一次性", "「新词」", "cutoff 之后的日记"):
        assert excluded not in md, f"不应收录：{excluded}"

    items = {(i["type"], i["id"]) for i in snap["source_manifest"]["items"]}
    assert ("relationship_event", ev_in) in items and ("relationship_event", ev_alive) in items
    for absent in (ev_after, ev_secret, ev_forgotten, ev_expired):
        assert ("relationship_event", absent) not in items, f"事件 {absent} 不应进 manifest"
    assert ("artifact", art_in) in items
    assert ("artifact", art_after) not in items and ("artifact", art_self) not in items
    assert ("activity_viewpoint", vp_in) in items and ("activity_viewpoint", vp_after) not in items
    assert ("user_term", term_in) in items
    assert ("diary", diary_in) in items and ("diary", diary_after) not in items

    counts = snap["source_counts"]
    assert counts["relationship_events"] == {"count": 2, "omitted": 0, "cap": 30}
    assert counts["artifacts"] == {"count": 1, "omitted": 0, "cap": 20}
    assert counts["diary"] == {"count": 1, "omitted": 0, "cap": 12}
    assert counts["user_terms"]["count"] == 1 and counts["activity_viewpoints"]["count"] == 1

    # 假的自身 artifact 已完成使命；清掉以免影响其它断言（真实流程里该类型会被排除）
    with db._lock:
        db.conn.execute("DELETE FROM artifacts WHERE id = ?", (art_self,))
        db.conn.commit()
    print("[OK] cutoff 过滤：晚于 cutoff/敏感/作废/过期来源不进入，自身 artifact 被排除")


def _test_caps_and_ordering() -> None:
    today = date.today()
    start = today - timedelta(days=60)  # 与 UID2 不同起点，供隔离测试断言
    cutoff = start + timedelta(days=29)
    _add_user_message(UID3, f"{start.isoformat()}T08:00:00")
    base = datetime.combine(cutoff - timedelta(days=1), dtime(8, 0, 0))
    for i in range(32):
        _add_event(UID3, 300 + i, occurred_at=(base + timedelta(seconds=i)).isoformat(timespec="seconds"), obj=f"第{i}件小事")

    snap = rsnap.create_snapshot(UID3, 30)
    counts = snap["source_counts"]["relationship_events"]
    assert counts == {"count": 30, "omitted": 2, "cap": 30}, f"截断计数应准确：{counts}"
    ev_items = [i for i in snap["source_manifest"]["items"] if i["type"] == "relationship_event"]
    assert len(ev_items) == 30
    ids = [i["id"] for i in ev_items]
    dates = [i["date"] for i in ev_items]
    assert ids == sorted(ids) and dates == sorted(dates), "按发生时间/id 确定性升序"
    print("[OK] caps：32 条事件收录 30 条、omitted=2，manifest 有序可追溯")


def _test_delete_and_recreate() -> None:
    old_snap = rsnap.snapshot_state(UID3)["snapshots"][0]
    assert old_snap["snapshot_days"] == 30

    assert rsnap.delete_snapshot(UID3, 30) is True
    assert rsnap.delete_snapshot(UID3, 30) is False, "重复删除返回 False"
    state = rsnap.snapshot_state(UID3)
    m30 = next(m for m in state["milestones"] if m["days"] == 30)
    assert m30["eligible"] and not m30["has_snapshot"], "删除后回到 eligible，且 GET 不会自动再生"
    assert state["snapshots"] == []
    assert not _snap_artifacts(UID3, old_snap["id"]), "删除必须级联清理 artifact"
    assert not _rows(
        "SELECT id FROM relationship_events WHERE user_id = ? AND source_type = 'relationship_snapshot'",
        (UID3,),
    ), "不得留下指向已删快照的事件幽灵"

    new_snap = rsnap.create_snapshot(UID3, 30)
    assert new_snap["id"] != old_snap["id"], "重建得到新快照"
    # 来源没变 → 确定性汇编内容逐字一致（仅 generated_at 时间戳不同）
    assert _markdown_head(new_snap["rendered_markdown"]) == _markdown_head(old_snap["rendered_markdown"])
    assert new_snap["source_manifest"] == old_snap["source_manifest"]
    print("[OK] 删除/重建：真删除、eligible 恢复、不自动再生、内容确定性一致")


def _test_isolation() -> None:
    uid2_snap = rsnap.snapshot_state(UID2)["snapshots"][0]
    uid3_snap = rsnap.snapshot_state(UID3)["snapshots"][0]
    assert uid2_snap["id"] != uid3_snap["id"]
    assert all(s["id"] != uid3_snap["id"] for s in rsnap.snapshot_state(UID2)["snapshots"])

    assert rsnap.delete_snapshot(UID2, 30) is True, "只能删自己的"
    assert rsnap.snapshot_state(UID3)["snapshots"][0]["id"] == uid3_snap["id"], "他人快照不受影响"
    u2 = rsnap.snapshot_state(UID2)
    assert u2["snapshots"] == []
    m30 = next(m for m in u2["milestones"] if m["days"] == 30)
    assert m30["eligible"] and not m30["has_snapshot"]
    assert u2["start_date"] != rsnap.snapshot_state(UID3)["start_date"], "各自起点互不可见"
    print("[OK] 隔离：快照、起点、删除全部按命名空间隔离")


def _test_export_restore() -> None:
    start = date.today() - timedelta(days=39)
    _add_user_message(UID4, f"{start.isoformat()}T09:00:00")
    source_diary_id = save_diary(
        UID4,
        (start + timedelta(days=5)).isoformat(),
        "这一页迁移后仍应指向目标命名空间里的日记",
    )
    snap = rsnap.create_snapshot(UID4, 30)

    bundle = rex.export_bundle(UID4)
    target = "snaps-restored"
    preview = rex.preview_restore(bundle, target)
    assert preview["ok"], preview["errors"]
    assert rex.restore_bundle(bundle, target)["ok"]

    def key(row) -> tuple:
        return (
            row["snapshot_days"], row["start_date"], row["cutoff_date"],
            row["generated_at"], row["rendered_markdown"],
        )

    src = _rows("SELECT * FROM relationship_snapshots WHERE user_id = ?", (UID4,))
    dst = _rows("SELECT * FROM relationship_snapshots WHERE user_id = ?", (target,))
    assert len(src) == len(dst) == 1
    assert key(dst[0]) == key(src[0]), "快照正文与时间字段应保持一致"

    src_manifest = snap["source_manifest"]
    dst_manifest = json.loads(dst[0]["source_manifest_json"])
    src_diary_item = next(item for item in src_manifest["items"] if item["type"] == "diary")
    dst_diary_item = next(item for item in dst_manifest["items"] if item["type"] == "diary")
    assert src_diary_item["id"] == source_diary_id
    assert dst_diary_item["id"] != source_diary_id, "恢复后 manifest 不应保留旧库主键"
    restored_diary = _rows(
        "SELECT user_id, content FROM diary WHERE id = ?", (dst_diary_item["id"],)
    )[0]
    assert restored_diary["user_id"] == target
    assert restored_diary["content"] == "这一页迁移后仍应指向目标命名空间里的日记"

    dst_ids = {int(r["id"]) for r in dst}
    art = _rows(
        "SELECT source_id, title, content FROM artifacts "
        "WHERE user_id = ? AND artifact_type = 'relationship_snapshot'", (target,),
    )
    assert len(art) == 1
    assert int(art[0]["source_id"]) in dst_ids, "artifact 引用应映射到新命名空间的快照 id"
    assert art[0]["content"] == snap["rendered_markdown"]
    print("[OK] 导出恢复：快照逐字段一致，artifact 主键引用映射到新命名空间")


def _test_http_and_flag() -> None:
    from fastapi.testclient import TestClient

    from backend.app import create_app
    from backend.core.config import config

    with TestClient(create_app()) as client:
        listed = client.get("/api/relationship-snapshots")
        assert listed.status_code == 200
        payload = listed.json()
        assert payload["ok"] and len(payload["milestones"]) == 3
        existing = payload["snapshots"]
        assert existing and existing[0]["snapshot_days"] == 30, "资格测试创建的 30 天快照应在"

        again = client.post("/api/relationship-snapshots", json={"snapshot_days": 30})
        assert again.status_code == 200
        assert again.json()["snapshot"]["id"] == existing[0]["id"], "重复 POST 幂等"

        assert client.post("/api/relationship-snapshots", json={"snapshot_days": 45}).status_code == 400
        assert client.post("/api/relationship-snapshots", json={"snapshot_days": 365}).status_code == 400

        assert client.delete("/api/relationship-snapshots/30").status_code == 200
        assert client.delete("/api/relationship-snapshots/30").status_code == 404
        after = client.get("/api/relationship-snapshots").json()
        assert after["snapshots"] == []
        m30 = next(m for m in after["milestones"] if m["days"] == 30)
        assert m30["eligible"] and not m30["has_snapshot"]

        config.relationship_snapshots_enabled = False
        try:
            assert client.get("/api/relationship-snapshots").status_code == 403
            assert client.post("/api/relationship-snapshots", json={"snapshot_days": 30}).status_code == 403
            assert client.delete("/api/relationship-snapshots/30").status_code == 403
            # 原有功能不受影响：未来信件与共同产物照常可用
            assert client.get("/api/future-letters").status_code == 200
            assert client.get("/api/artifacts").status_code == 200
        finally:
            config.relationship_snapshots_enabled = True
    print("[OK] HTTP：GET 只读、显式创建/删除、flag 关闭 403 且未来信件/产物可用")


def _test_restart_continuity() -> None:
    snap = rsnap.create_snapshot(UID, 30)
    assert snap["snapshot_days"] == 30
    # 模拟应用重启：另一个连接重新打开同一数据库文件，快照仍在
    from backend.core.config import config

    raw = sqlite3.connect(config.data_dir / "bot.db")
    try:
        n = raw.execute(
            "SELECT COUNT(*) FROM relationship_snapshots WHERE user_id = ?", (UID,)
        ).fetchone()[0]
        assert n >= 1, "重启后快照应仍在"
        version = int(raw.execute("PRAGMA user_version").fetchone()[0])
        assert version >= 17, f"关系快照需要 schema v17+，实际 {version}"
    finally:
        raw.close()
    print("[OK] 重启连续性：快照落盘可见")


def _test_reset_clears_snapshots() -> None:
    assert _rows("SELECT id FROM relationship_snapshots WHERE user_id = ?", (UID,))
    db.reset()
    assert _rows(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'relationship_snapshots'", ()
    )
    assert not _rows("SELECT id FROM relationship_snapshots", ())
    with db._lock:
        version = int(db.conn.execute("PRAGMA user_version").fetchone()[0])
    assert version >= 17
    print("[OK] 重置：relationship_snapshots 清空且表结构保留")


def main() -> None:
    _test_state_without_messages()
    _test_eligibility_boundaries()
    _test_cutoff_content()
    _test_caps_and_ordering()
    _test_delete_and_recreate()
    _test_isolation()
    _test_export_restore()
    _test_http_and_flag()
    _test_restart_continuity()
    _test_reset_clears_snapshots()
    print("关系快照 M8 测试通过")


if __name__ == "__main__":
    main()
