# -*- coding: utf-8 -*-
"""M8.7「不同版本的我们」：关系版本检查点的隐私白名单、不可变、确定性比较、
人格隔离、真删除、导出恢复、reset 清理、损坏数据边界与 schema 下限。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_versions_"))
os.environ.setdefault("MEMORY_V2", "0")
os.environ.setdefault("MEMORY_MEM0", "0")

from backend.core import relationship_export as rex  # noqa: E402
from backend.core import relationship_versions as rver  # noqa: E402
from backend.core import state as state_module  # noqa: E402
from backend.api import relationship_versions as rver_api  # noqa: E402
from backend.core.userdb import (  # noqa: E402
    _SCHEMA_VERSION,
    db,
    kv_set,
    save_diary,
    save_promise,
)
from backend.core.persona_profiles import active_user_id  # noqa: E402

UID = "version-user"
OTHER = "version-other"

# 刻意播种、绝不允许出现在任何 snapshot_json 里的真实内容片段
MSG_MARKER = "消息标记青色渡鸦：今晚的暗号只有我们知道"
FACT_MARKER = "事实标记红隼：用户在整理作品集"
EVENT_MARKER = "事件标记琥珀：纪念日秘密暗号"
DIARY_MARKER = "日记标记玛瑙：今天偷偷把这件事写进日记"
ARTIFACT_MARKER = "产物标记紫水晶：共同书摘的正文片段"
HIT_MARKER = "情绪残留标记海盐"
EVENT_MEM_MARKER = "事件记忆标记季风：你上次说过的原话"
ARCHIVE_MARKER = "长期档案标记雾松"
REPAIR_MARKER = "修复方式标记晚风：用户刚才认真道歉了"
REASON_MARKER = "刚迎来了特殊日子"

PRIVACY_MARKERS = (
    MSG_MARKER, FACT_MARKER, EVENT_MARKER, DIARY_MARKER, ARTIFACT_MARKER,
    HIT_MARKER, EVENT_MEM_MARKER, ARCHIVE_MARKER, REPAIR_MARKER,
)
# 隐私字段：行为帧黑名单 + season reason/line 一律不得出现在快照里
FORBIDDEN_KEYS = (
    "reaction_line", "archive_line", "event_line", "tension_line", "reason", "line",
)
FORBIDDEN_RESULT_KEYS = {"verdict", "score", "trend", "better", "worse", "summary", "evaluation"}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _seed_real_content(uid: str) -> None:
    """播种消息/事实/事件/日记/产物/情绪记忆——只该以计数出现，不该以内容出现。"""
    db.ensure_user(uid)
    now = _now_iso()
    db.set_affection_absolute(uid, 10)
    with db._lock:
        db.conn.execute(
            "INSERT INTO messages (user_id, role, content, ts) VALUES (?, 'user', ?, ?)",
            (uid, MSG_MARKER, now),
        )
        db.conn.execute(
            "INSERT INTO facts (user_id, content, ts) VALUES (?, ?, ?)",
            (uid, FACT_MARKER, now),
        )
        db.conn.execute(
            "INSERT INTO relationship_events (user_id, event_type, source_type, source_id, "
            "subject, object, payload_json, occurred_at, created_at) "
            "VALUES (?, 'important_date', 'important_date', 0, ?, ?, ?, ?, ?)",
            (uid, uid, EVENT_MARKER, json.dumps({"label": EVENT_MARKER}, ensure_ascii=False), now, now),
        )
        db.conn.execute(
            "INSERT INTO artifacts (user_id, artifact_type, source_type, source_id, title, "
            "content, version, created_at, updated_at) "
            "VALUES (?, 'book_summary', 'activity', 0, ?, ?, 1, ?, ?)",
            (uid, ARTIFACT_MARKER, ARTIFACT_MARKER, now, now),
        )
        db.conn.commit()
    save_diary(uid, datetime.now().date().isoformat(), DIARY_MARKER)
    state_module._save_emotion_memory(uid, [{"hit": HIT_MARKER, "weight": 0.9, "ts": now}])
    state_module.record_emotion_archive(uid, topic=ARCHIVE_MARKER, valence=1, weight=0.9)
    state_module.record_event_memory(uid, text=EVENT_MEM_MARKER, valence=1, weight=0.9)
    # 播种纪念日事件会让 season 的 reason 携带用户内容（「刚迎来了特殊日子「…」」），
    # reason 与 line 绝不能进快照。


def _assert_no_markers(text: str, context: str) -> None:
    for marker in PRIVACY_MARKERS:
        assert marker not in text, f"{context} 泄漏了真实内容片段：{marker}"
    assert REASON_MARKER not in text, f"{context} 泄漏了 season reason"


def _all_keys(value, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            keys.add(path)
            keys |= _all_keys(item, path)
    elif isinstance(value, list):
        for item in value:
            keys |= _all_keys(item, prefix)
    return keys


def _snapshot_of(version: dict) -> dict:
    return version["snapshot"]


def _test_capture_whitelist_and_privacy() -> None:
    _seed_real_content(UID)
    view = rver.capture_version(UID, "升级前")
    raw = json.dumps(_snapshot_of(view), ensure_ascii=False)
    _assert_no_markers(raw, "capture")

    snapshot = _snapshot_of(view)
    assert snapshot["format_version"] == 1
    assert set(snapshot["state"].keys()) == {
        "affection", "mood", "mood_label", "energy", "tension", "stage", "resting",
    }
    assert set(snapshot["season"].keys()) == {"code", "label"}
    assert set(snapshot["behavior"].keys()) == set(rver._BEHAVIOR_FIELDS)
    assert set(snapshot["counts"].keys()) == set(rver._COUNT_KEYS)
    for key in FORBIDDEN_KEYS:
        for section in ("state", "season", "behavior"):
            assert key not in snapshot[section], f"快照 {section} 里出现了隐私字段 {key}"

    # 白名单字段与确定性预期一致
    assert snapshot["state"]["affection"] == 10
    assert snapshot["state"]["stage"] == state_module.stage_of(10)
    assert snapshot["season"]["code"] == "celebrate"  # 播种的纪念日事件落在庆祝窗内
    assert snapshot["counts"]["messages"] == 1
    assert snapshot["counts"]["facts_active"] == 1
    assert snapshot["counts"]["events_active"] == 1
    assert snapshot["counts"]["artifacts_real"] == 1
    assert snapshot["counts"]["diary"] == 1
    assert snapshot["counts"]["artifacts_fiction"] == 0

    # 数据库里的 snapshot_json 本身也不含任何原文
    with db._lock:
        stored = db.conn.execute(
            "SELECT snapshot_json FROM relationship_versions WHERE id = ?", (view["id"],)
        ).fetchone()[0]
    _assert_no_markers(stored, "数据库 snapshot_json")

    # 张力场景：tension_line 的修复提示（含用户文本）也不得进快照
    kv_set(UID, "state:relationship_tension", json.dumps({"level": 40, "last_repair": REPAIR_MARKER}))
    tense = rver.capture_version(UID, "闹别扭那阵子")
    tense_raw = json.dumps(_snapshot_of(tense), ensure_ascii=False)
    assert REPAIR_MARKER not in tense_raw, "tension 修复提示泄漏进了快照"
    assert tense["snapshot"]["state"]["tension"] == 40
    kv_set(UID, "state:relationship_tension", json.dumps({"level": 0, "last_repair": ""}))
    print("[OK] capture：精确白名单 + 全部隐私标记零泄漏（消息/事实/事件/日记/产物/情绪记忆/修复提示/season reason）")


def _test_immutability_and_exact_compare() -> None:
    before_view = rver.capture_version(UID, "第二次检查点")
    # 检查点之后真实生活继续：状态变化 + 新记录
    db.set_affection_absolute(UID, 55)  # 10 → 55，初识 → 亲密（P2-01 两维调试入口）
    db.set_mood(UID, 80)                      # 心情档变化
    with db._lock:
        for i in range(3):
            db.conn.execute(
                "INSERT INTO messages (user_id, role, content, ts) VALUES (?, 'user', ?, ?)",
                (UID, f"检查点之后的消息 {i}", _now_iso()),
            )
        db.conn.commit()
    db.add_fact(UID, "检查点之后的新事实")
    save_promise(UID, "检查点之后的约定")

    # 旧快照原样不变：重新读取与 capture 时返回完全一致
    listed = rver.list_versions(UID)
    old_view = next(v for v in listed if v["id"] == before_view["id"])
    assert old_view["snapshot"] == before_view["snapshot"], "旧快照被重新计算了"

    after_view = rver.capture_version(UID, "这个夏天结束时")
    result = rver.compare_versions(UID, before_view["id"], after_view["id"])
    assert result["ok"] is True
    assert result["before"]["snapshot"] == before_view["snapshot"]
    assert result["after"]["snapshot"] == after_view["snapshot"]

    deltas = result["numeric_deltas"]
    assert deltas["state.affection"] == 45
    assert deltas["state.mood"] == 20
    assert set(deltas.keys()) == {
        *(f"state.{k}" for k in ("affection", "mood", "energy", "tension")),
        *(f"counts.{k}" for k in rver._COUNT_KEYS),
    }
    assert deltas["counts.messages"] == 3
    assert deltas["counts.facts_active"] == 1
    assert deltas["counts.promises_pending"] == 1

    before_snap, after_snap = before_view["snapshot"], after_view["snapshot"]
    expected_changes = {
        path
        for path in rver._CHANGE_PATHS
        if rver._value_at(before_snap, path) != rver._value_at(after_snap, path)
    }
    got_changes = {change["key"] for change in result["changes"]}
    assert got_changes == expected_changes, "changes 集合与两份快照的实际差异不一致"
    assert "state.stage" in got_changes
    stage_change = next(c for c in result["changes"] if c["key"] == "state.stage")
    assert stage_change["before"] == state_module.stage_of(10)
    assert stage_change["after"] == state_module.stage_of(55)
    mood_change = next(c for c in result["changes"] if c["key"] == "state.mood_label")
    assert mood_change["before"] == state_module.emotion_label(60)[0]
    assert mood_change["after"] == state_module.emotion_label(80)[0]

    # 任何层级都不允许出现价值判断字段
    forbidden = {path for path in _all_keys(result) if path.split(".")[-1] in FORBIDDEN_RESULT_KEYS}
    assert not forbidden, f"比较结果出现判断字段：{forbidden}"
    print("[OK] 不可变 + 精确比较：数值 delta 与分类 changes 精确，无 verdict/score/trend/better/worse")


def _test_label_and_limit_validation() -> None:
    for bad_label in ("", "   "):
        try:
            rver.capture_version(UID, bad_label)
            raise AssertionError("空标签应被拒绝")
        except rver.RelationshipVersionError:
            pass
    try:
        rver.capture_version(UID, "长" * 61)
        raise AssertionError("超过 60 字的标签应被拒绝")
    except rver.RelationshipVersionError:
        pass
    ok_view = rver.capture_version(UID, "边" * 60)
    assert ok_view["label"] == "边" * 60

    listed = rver.list_versions(UID, limit=2)
    assert len(listed) == 2
    assert len(rver.list_versions(UID, limit=0)) == 1, "limit<1 应回退为 1"
    assert len(rver.list_versions(UID, limit=10**9)) == len(
        rver.list_versions(UID, limit=100)
    ), "limit>100 应回退为 100"
    print("[OK] 校验：label 空/超长拒绝、60 字边界通过，limit 1..100 夹紧")


def _test_persona_isolation() -> None:
    db.ensure_user(OTHER)
    other_view = rver.capture_version(OTHER, "另一个人格的版本")
    own_ids = {v["id"] for v in rver.list_versions(UID)}
    assert other_view["id"] not in own_ids
    assert [v["id"] for v in rver.list_versions(OTHER, limit=1)] == [other_view["id"]]

    # 跨人格 compare / delete 都不行
    mine = rver.list_versions(UID)[0]
    try:
        rver.compare_versions(UID, mine["id"], other_view["id"])
        raise AssertionError("跨人格 compare 应被拒绝")
    except rver.RelationshipVersionError:
        pass
    assert rver.delete_version(UID, other_view["id"]) is False
    assert any(v["id"] == other_view["id"] for v in rver.list_versions(OTHER)), "越权删除不应生效"

    # 相同 before/after 拒绝
    try:
        rver.compare_versions(UID, mine["id"], mine["id"])
        raise AssertionError("相同版本 compare 应被拒绝")
    except rver.RelationshipVersionError:
        pass

    # 读取路径不隐式建档
    ghost = "version-ghost"
    assert rver.list_versions(ghost) == []
    try:
        rver.compare_versions(ghost, 1, 2)
    except rver.RelationshipVersionError:
        pass
    assert rver.delete_version(ghost, 1) is False
    with db._lock:
        n = int(db.conn.execute("SELECT COUNT(*) FROM users WHERE user_id = ?", (ghost,)).fetchone()[0])
    assert n == 0, "读取路径不得隐式建档"
    print("[OK] 隔离：list/compare/delete 全部按人格隔离，读取不隐式建档，同 id 拒绝")


def _test_real_delete() -> None:
    view = rver.capture_version(UID, "注定被删除的版本")
    assert rver.delete_version(UID, view["id"]) is True
    assert all(v["id"] != view["id"] for v in rver.list_versions(UID, limit=100))
    assert rver.delete_version(UID, view["id"]) is False, "重复删除应返回 False"
    print("[OK] 删除：真删除，重复删除返回 False")


def _test_export_restore() -> None:
    bundle = rex.export_bundle(UID)
    assert "relationship_versions" in bundle["data"], "版本行应随关系包导出"
    target = "version-restored"
    preview = rex.preview_restore(rex.bundle_from_json(rex.bundle_to_json(bundle)), target)
    assert preview["ok"], preview["errors"]
    result = rex.restore_bundle(rex.bundle_from_json(rex.bundle_to_json(bundle)), target)
    assert result["ok"]
    source_rows = rver.list_versions(UID, limit=100)
    restored_rows = rver.list_versions(target, limit=100)
    assert len(restored_rows) == len(source_rows) >= 2
    for restored, source in zip(
        sorted(restored_rows, key=lambda v: v["label"]),
        sorted(source_rows, key=lambda v: v["label"]),
    ):
        assert restored["label"] == source["label"]
        assert restored["snapshot"] == source["snapshot"], "恢复后快照必须与原快照完全一致"
    with db._lock:
        stored = db.conn.execute(
            "SELECT snapshot_json FROM relationship_versions WHERE user_id = ? ORDER BY id",
            (target,),
        ).fetchall()
        origin = db.conn.execute(
            "SELECT snapshot_json FROM relationship_versions WHERE user_id = ? ORDER BY id",
            (UID,),
        ).fetchall()
    assert [row[0] for row in stored] == [row[0] for row in origin], "snapshot_json 应逐字保留"

    # 空命名空间（无任何版本）导出恢复同样通畅
    empty_uid = "version-empty-source"
    db.ensure_user(empty_uid)
    empty_bundle = rex.export_bundle(empty_uid)
    assert empty_bundle["data"]["relationship_versions"] == []
    empty_target = "version-empty-restored"
    assert rex.restore_bundle(empty_bundle, empty_target)["ok"]
    assert rver.list_versions(empty_target) == []
    print("[OK] 导出恢复：版本行随包迁移，snapshot_json 逐字一致；空命名空间恢复无异常")


async def _test_reset_clears_table() -> None:
    persona = active_user_id()
    rver.capture_version(persona, "重置前留档")
    from backend.core import reset as reset_module

    assert "relationship_versions" in reset_module._TABLES, "reset 权威清单必须覆盖新表"
    stats = await reset_module.reset_everything()
    assert stats["ok"], stats
    with db._lock:
        n = int(db.conn.execute(
            "SELECT COUNT(*) FROM relationship_versions WHERE user_id = ?", (persona,)
        ).fetchone()[0])
    assert n == 0, "权威 reset 后该人格不得残留版本行"
    print("[OK] reset：权威 reset_everything 后版本行零残留")


def _test_corrupt_snapshot_becomes_business_error() -> None:
    broken_uid = "version-corrupt"
    db.ensure_user(broken_uid)
    valid_snapshot = json.dumps({"format_version": 1, "state": {}, "season": {}, "behavior": {}, "counts": {}})
    with db._lock:
        rows = [
            ("完好格式", valid_snapshot),
            ("坏数据", "{not json"),
            ("未来格式", json.dumps({"format_version": 99})),
        ]
        ids = []
        for label, payload in rows:
            cur = db.conn.execute(
                "INSERT INTO relationship_versions (user_id, label, captured_at, schema_version, "
                "snapshot_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (broken_uid, label, _now_iso(), int(_SCHEMA_VERSION), payload, _now_iso()),
            )
            ids.append(int(cur.lastrowid))
        db.conn.commit()
    valid_id, bad_id, future_id = ids
    try:
        rver.list_versions(broken_uid)
        raise AssertionError("损坏快照应转业务错误")
    except rver.RelationshipVersionError:
        pass
    for left, right in ((bad_id, future_id), (valid_id, bad_id), (valid_id, future_id)):
        try:
            rver.compare_versions(broken_uid, left, right)
            raise AssertionError("损坏快照参与比较应转业务错误")
        except rver.RelationshipVersionError:
            pass
    print("[OK] 损坏数据：坏 JSON / 未知 format_version 均转为 RelationshipVersionError")


async def _test_api_list_maps_business_error() -> None:
    """损坏快照不能让列表接口冒泡成未处理的 500。"""
    original = rver_api.relationship_versions.list_versions

    def fail_list(_user_id: str, _limit: int):
        raise rver.RelationshipVersionError("这个版本的存档数据损坏了")

    rver_api.relationship_versions.list_versions = fail_list
    try:
        response = await rver_api.api_list(limit=50)
    finally:
        rver_api.relationship_versions.list_versions = original
    assert response.status_code == 400
    assert json.loads(response.body)["error"] == "这个版本的存档数据损坏了"
    print("[OK] API：列表中的损坏快照映射为 400 业务错误，不冒泡 500")


def _test_schema_v16() -> None:
    assert _SCHEMA_VERSION >= 17, "relationship_versions 需要 schema v17+"
    with db._lock:
        columns = {row[1] for row in db.conn.execute("PRAGMA table_info(relationship_versions)")}
    assert columns == {"id", "user_id", "label", "captured_at", "schema_version", "snapshot_json", "created_at"}
    from backend.core import reset as reset_module
    from backend.core.relationship_export import CATEGORIES

    assert "relationship_versions" in CATEGORIES["life"]
    assert "relationship_versions" in reset_module._TABLES
    print("[OK] schema v17+：表结构、导出类别与双 reset 清单全部就位")


async def main() -> None:
    _test_capture_whitelist_and_privacy()
    _test_immutability_and_exact_compare()
    _test_label_and_limit_validation()
    _test_persona_isolation()
    _test_real_delete()
    _test_export_restore()
    await _test_reset_clears_table()
    _test_corrupt_snapshot_becomes_business_error()
    await _test_api_list_maps_business_error()
    _test_schema_v16()
    print("M8.7 不同版本的我们：全部回归通过")


if __name__ == "__main__":
    asyncio.run(main())
