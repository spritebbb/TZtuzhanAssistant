# -*- coding: utf-8 -*-
"""关系包导出/恢复的表清单守卫 + 换机漏表批端到端回归（DF-1）。

背景（2026-09-29 缺陷审查）：CATEGORIES 漏 7 张用户数据表（user_preferences、
activity_lists、list_items、watches、event_chains、relationship_dimension_ledger、
humor_usage、document_segments），换机/恢复静默丢数据；restore 的 kv 通道未过
kv_registry 白名单（P3-44 同族口子）。本文件三层防线：

1. 覆盖守卫：schema 全表 - CATEGORIES ⊆ 豁免清单（运行态/凭据/文件伴生）——
   将来新增用户数据表若忘登记，这里红；
2. 幽灵表守卫：CATEGORIES 不得含 schema 里不存在的表；
3. 端到端往返：建 → 导出 → 恢复到新命名空间，新表全量到达且引用重映射正确；
   恶意 kv 键被拒。
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_df1_"))

from backend.core import relationship_export as rex
from backend.core.kv_registry import match_spec
from backend.core.userdb import db

# ---- 豁免清单（每条注明不导出的理由；新增豁免必须在此登记理由） ----

EXEMPT = {
    # 运行态/派生态：可再生产，不属于「关系本体」
    "activity_draft_receipts", "context_lifecycle", "thought_context_receipts",
    "experience_metrics", "greeting_variant_usage", "usage_log", "wrapup_outbox",
    "document_import_jobs", "situation_files",
    # 38项#22 兴趣源：授权清单用户可重建、素材可重拉（含外部网络内容不宜进可分享备份）
    "interest_feeds", "interest_feed_items",
    # 后台任务认领/租约记录（pending/running/lease，无 user_id 列）
    "job_runs",
    # kv_store 不走表通道（bundle["kv"] 按 kv_registry 白名单单列）
    "kv_store",
    # 声纹档案：表只是元数据，本体是音频/模型文件资产——待独立切片随文件一起走
    "voice_profiles", "voice_manifests",
}

TS = "2026-09-29T10:00:00"


def _schema_tables() -> set[str]:
    src = (ROOT / "backend" / "core" / "userdb.py").read_text(encoding="utf-8")
    return set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", src))


def test_coverage_guard() -> int:
    schema = _schema_tables()
    exported = {t for tables in rex.CATEGORIES.values() for t in tables}
    missing = (schema - exported) - EXEMPT
    assert not missing, f"用户数据表未登记导出清单（换机会丢）：{sorted(missing)}"
    ghost = exported - schema
    assert not ghost, f"导出清单含 schema 不存在的表：{sorted(ghost)}"
    for table, _ref, column in rex._REFERENCE_RULES:
        assert table in exported, f"引用规则的表 {table} 不在导出清单"
        del column
    print("[OK] 覆盖守卫：schema 85 表 - CATEGORIES ⊆ 豁免清单；无幽灵表")
    return 0


def _seed(uid: str) -> dict:
    """构造覆盖全部新补表的引用链，返回各表 old_id 供恢复后比对。"""
    db.ensure_user(uid)
    ids: dict[str, int] = {}
    with db._lock:
        c = db.conn
        c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
        c.execute(
            "INSERT INTO user_terms (user_id, term, category, meaning, count, first_seen, last_seen) "
            "VALUES (?, '菟丝子', 'catchphrase', '', 1, ?, ?)",
            (uid, TS, TS))
        ids["term"] = int(c.execute(
            "SELECT id FROM user_terms WHERE user_id=?", (uid,)).fetchone()["id"])
        c.execute(
            "INSERT INTO activities (user_id, kind, document_id, title, status, created_at, updated_at) "
            "VALUES (?, 'goal', 0, '共同目标A', 'active', ?, ?)", (uid, TS, TS))
        ids["activity"] = int(c.execute(
            "SELECT id FROM activities WHERE user_id=? AND title='共同目标A'", (uid,)).fetchone()["id"])
        c.execute(
            "INSERT INTO relationship_events (user_id, event_type, source_type, source_id, occurred_at, created_at) "
            "VALUES (?, 'reading_finished', 'activity', ?, ?, ?)",
            (uid, ids["activity"], TS, TS))
        ids["event"] = int(c.execute(
            "SELECT id FROM relationship_events WHERE user_id=?", (uid,)).fetchone()["id"])
        c.execute(
            "INSERT INTO kb_documents (user_id, filename, stored_path, format, size_bytes, chunk_count, ts) "
            "VALUES (?, '笔记.md', '', 'md', 10, 1, ?)", (uid, TS))
        ids["doc"] = int(c.execute(
            "SELECT id FROM kb_documents WHERE user_id=?", (uid,)).fetchone()["id"])
        c.execute(
            "INSERT INTO user_preferences (user_id, category, value_json, origin, status, created_at, updated_at, source_message_id) "
            "VALUES (?, 'comfort', '{}', 'user_teaching', 'active', ?, ?, 424242)", (uid, TS, TS))
        c.execute(
            "INSERT INTO activity_lists (activity_id, user_id, list_kind, created_at, updated_at) "
            "VALUES (?, ?, 'song', ?, ?)", (ids["activity"], uid, TS, TS))
        c.execute(
            "INSERT INTO list_items (user_id, activity_id, title, added_by, ts) "
            "VALUES (?, ?, '歌一', 'user', ?)", (uid, ids["activity"], TS))
        c.execute(
            "INSERT INTO watches (user_id, url, interval_minutes, created_at, updated_at) "
            "VALUES (?, 'https://example.com/a', 60, ?, ?)", (uid, TS, TS))
        c.execute(
            "INSERT INTO relationship_dimension_ledger (user_id, event_id, rule_id, trust_delta, intimacy_delta, occurred_at) "
            "VALUES (?, ?, 'r1', 2, 1, ?)", (uid, ids["event"], TS))
        c.execute(
            "INSERT INTO pending_thoughts (user_id, kind, source_type, source_id, content, earliest_at, expires_at, priority, created_at) "
            "VALUES (?, 'goal_checkin', 'activity', ?, '惦记', ?, ?, 5, ?)",
            (uid, ids["activity"], TS, "2026-10-06T10:00:00", TS))
        ids["thought"] = int(c.execute(
            "SELECT id FROM pending_thoughts WHERE user_id=?", (uid,)).fetchone()["id"])
        c.execute(
            "INSERT INTO event_chains (user_id, source_event_id, rule_id, rule_version, node, status, due_at, attempt, result_id, created_at, updated_at) "
            "VALUES (?, ?, 'promise_aftermath', 1, 'aftermath', 'done', ?, 1, ?, ?, ?)",
            (uid, ids["event"], TS, ids["thought"], TS, TS))
        c.execute(
            "INSERT INTO humor_usage (user_id, term_id, source_turn_id, reaction, status, created_at) "
            "VALUES (?, ?, 987654, 'positive', 'approved', ?)", (uid, ids["term"], TS))
        c.execute(
            "INSERT INTO document_segments (user_id, document_id, segment_index, title, created_at) "
            "VALUES (?, ?, 0, '段一', ?)", (uid, ids["doc"], TS))
        db.conn.commit()
    return ids


def test_roundtrip_and_kv_gate() -> int:
    src_uid, dst_uid = "df1-src", "df1-dst"
    old = _seed(src_uid)
    bundle = rex.export_bundle(src_uid)
    assert bundle["data"].get("user_preferences"), "补表后必须出现在导出包里"
    # kv 闸门：登记键（surprise:last）恢复；未登记的伪造键拒绝
    bundle["kv"]["exported"]["surprise:last"] = "2026-09-29"
    bundle["kv"]["exported"]["hacker:arbitrary"] = '{"hacked": true}'
    preview = rex.preview_restore(bundle, dst_uid)
    assert preview["ok"], preview["errors"]
    assert preview["kv_exported"] == 1, "preview 只应统计登记键"
    rex.restore_bundle(bundle, dst_uid)

    new = {
        "term": db.conn.execute("SELECT id FROM user_terms WHERE user_id=?", (dst_uid,)).fetchone()["id"],
        "activity": db.conn.execute("SELECT id FROM activities WHERE user_id=? AND title='共同目标A'", (dst_uid,)).fetchone()["id"],
        "event": db.conn.execute("SELECT id FROM relationship_events WHERE user_id=?", (dst_uid,)).fetchone()["id"],
        "thought": db.conn.execute("SELECT id FROM pending_thoughts WHERE user_id=?", (dst_uid,)).fetchone()["id"],
        "doc": db.conn.execute("SELECT id FROM kb_documents WHERE user_id=?", (dst_uid,)).fetchone()["id"],
    }
    q = lambda sql: db.conn.execute(sql, (dst_uid,)).fetchone()
    row = q("SELECT activity_id FROM activity_lists WHERE user_id=?")
    assert row["activity_id"] == new["activity"], "activity_lists 主键必须跟随 activities 重映射"
    row = q("SELECT activity_id FROM list_items WHERE user_id=?")
    assert row["activity_id"] == new["activity"], "list_items 引用必须对齐新 activities id"
    row = q("SELECT event_id FROM relationship_dimension_ledger WHERE user_id=?")
    assert row["event_id"] == new["event"], "ledger 引用必须对齐新事件 id"
    row = q("SELECT source_event_id, result_id FROM event_chains WHERE user_id=?")
    assert (row["source_event_id"], row["result_id"]) == (new["event"], new["thought"])
    row = q("SELECT term_id, source_turn_id FROM humor_usage WHERE user_id=?")
    assert row["term_id"] == new["term"] and row["source_turn_id"] is None, "包外轮次引用应清空"
    row = q("SELECT document_id FROM document_segments WHERE user_id=?")
    assert row["document_id"] == new["doc"]
    row = q("SELECT source_message_id FROM user_preferences WHERE user_id=?")
    assert row["source_message_id"] is None, "包外消息引用应清空"
    assert q("SELECT url FROM watches WHERE user_id=?")["url"] == "https://example.com/a"
    kv = db.conn.execute(
        "SELECT value FROM kv_store WHERE user_id=? AND key='surprise:last'", (dst_uid,)).fetchone()
    assert kv and kv["value"] == "2026-09-29"
    evil = db.conn.execute(
        "SELECT 1 FROM kv_store WHERE user_id=? AND key='hacker:arbitrary'", (dst_uid,)).fetchone()
    assert evil is None, "未登记 kv 键必须被恢复白名单拒绝"
    print("[OK] 端到端往返：新表全量到达、引用重映射正确；kv 白名单拒绝伪造键")
    return 0


def main() -> int:
    failed = test_coverage_guard() + test_roundtrip_and_kv_gate()
    if failed:
        print(f"\n=== DF-1 导出/恢复守卫：{failed} 项失败 ===")
        return 1
    print("\n=== DF-1 导出/恢复守卫：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
