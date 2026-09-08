# -*- coding: utf-8 -*-
"""G01 记忆显著度：评分公式/滞回分层/shadow 语义/视角/初历/源删级联/导出恢复。

验收输入（§14.8 原文锚定）：
- explicit=1/anchor=1 → 60 进入 long；score 29 short、45 保持；
- pinned score 0 不降级；confidence 0.4 不变（confidence 与 score 独立）；
- 同日刷三次计 1 天（distinct_days=1）；
- policy 不改写 facts.expires_at（shadow）；旧条目迁移 tier=legacy；
- 初历唯一键：同 topic_key 第二次 False；源删后标记消失。
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_g01_"))

from backend.core import memory_salience as ms
from backend.core.userdb import db

AT = datetime(2026, 9, 8, 12, 0).astimezone()
UID = "g01-user"


def _fact(uid: str, content: str) -> int:
    return db.add_fact(uid, content)


def test_score_formula() -> int:
    assert ms.compute_score(explicit_importance=True, relationship_anchor=True,
                            distinct_days_mentioned=0, first_event=False) == 60
    assert ms.compute_score(explicit_importance=False, relationship_anchor=False,
                            distinct_days_mentioned=3, first_event=True) == 40
    assert ms.compute_score(explicit_importance=True, relationship_anchor=True,
                            distinct_days_mentioned=99, first_event=True) == 80 + 10 + 10, \
        "40+20+10*3+10=100（clamp 上限）"
    assert ms.compute_score(explicit_importance=False, relationship_anchor=False,
                            distinct_days_mentioned=0, first_event=False) == 0
    print("[OK] 评分公式 S=40e+20a+10min(d,3)+10f，clamp")
    return 0


def test_tier_hysteresis_and_pinned() -> int:
    assert ms.decide_tier(60, current_tier="short", pinned=False) == "long"
    assert ms.decide_tier(29, current_tier="long", pinned=False) == "short"
    assert ms.decide_tier(45, current_tier="short", pinned=False) == "short", "中间带保持"
    assert ms.decide_tier(45, current_tier="long", pinned=False) == "long", "中间带保持"
    assert ms.decide_tier(0, current_tier="long", pinned=True) == "long", "pinned 不降级"
    assert ms.decide_tier(0, current_tier="short", pinned=True) == "short"
    assert ms.decide_tier(100, current_tier="new", pinned=False) == "long", "新条目默认 short 后可升"
    print("[OK] 滞回分层 + pinned 永不通过分数降级")
    return 0


def test_policy_shadow_semantics() -> int:
    uid = UID
    db.ensure_user(uid)
    fid = _fact(uid, "用户喜欢下雨天")
    # 显式重要 + 关系锚点 → long，但 facts.expires_at 不被触碰（shadow）
    policy = ms.evaluate_fact(uid, fid, explicit=True, anchor=True)
    assert policy["tier"] == "long" and policy["score"] == 60, policy
    from backend.core.userdb import list_facts

    fact = next(f for f in list_facts(uid, 10) if int(f["id"]) == fid)
    assert fact.get("expires_at") is None, "shadow 不得改写 facts.expires_at"
    # confidence 独立：score 高不抬 confidence
    assert float(fact["confidence"]) == 0.7, "confidence 不因 score 升高改变"
    # legacy 迁移
    fid2 = _fact(uid, "用户最近在准备考试")
    policy2 = ms.evaluate_fact(uid, fid2, explicit=False, legacy=True)
    assert policy2["tier"] == "legacy", policy2
    # review_at 有值（shadow 观察窗口）
    assert policy["review_at"]
    print("[OK] policy shadow 语义：不改 expires_at / confidence 独立 / legacy 迁移")
    return 0


def test_first_occurrence_unique_and_forget() -> int:
    uid = "g01-first"
    db.ensure_user(uid)
    assert ms.mark_first_occurrence(uid, "outing", "  去看海　") is True
    # 同 topic（规范化后）第二次 → False
    assert ms.mark_first_occurrence(uid, "outing", "去看海") is False
    assert ms.is_first_occurrence(uid, "outing", "去看雪山") is True
    # 源删除：标记消失，且旧历史不再吃首次加成（is_first 恢复 True 但需新事件显式建立）
    removed = ms.forget_for_source(uid, source_event_id=None, fact_id=None)
    # 直接按 topic 删（源删等价路径）
    with db._lock:
        cur = db.conn.execute(
            "DELETE FROM first_occurrences WHERE user_id=? AND topic_key=?",
            (uid, ms._normalize_topic_key("去看海")))
        db.conn.commit()
    assert ms.is_first_occurrence(uid, "outing", "去看海") is True, "删除后可由未来新事件重新建立"
    print("[OK] 初历唯一键（topic_key 规范化）+ 源删后标记消失")
    return 0


def test_annotations_and_cascade() -> int:
    uid = "g01-anno"
    db.ensure_user(uid)
    fid = _fact(uid, "用户养了一只猫")
    aid = ms.add_annotation(uid, fid, emotion="羡慕", viewpoint="这个人很温柔",
                            origin="inference", confidence=0.6)
    assert aid > 0
    rows = ms.annotations_for(uid, fid)
    assert rows and rows[0]["emotion"] == "羡慕"
    # origin 校验
    try:
        ms.add_annotation(uid, fid, origin="hack")
        raise AssertionError("应拒绝非法 origin")
    except ms.SalienceError:
        pass
    # 源删级联：policy+annotations 全清
    ms.evaluate_fact(uid, fid, explicit=True)
    removed = ms.forget_for_source(uid, fact_id=fid)
    assert removed["policy"] == 1 and removed["annotations"] == 1, removed
    assert ms.get_policy(uid, fid) is None
    print("[OK] 视角注释（不改写事实文本）+ 源删级联三表")
    return 0


def test_export_restore_roundtrip() -> int:
    from backend.core.relationship_export import export_bundle, restore_bundle

    uid = "g01-export"
    db.ensure_user(uid)
    fid = _fact(uid, "用户在学吉他")
    ms.evaluate_fact(uid, fid, explicit=True, anchor=True, distinct_days=2)
    ms.add_annotation(uid, fid, emotion="欣赏")
    ms.mark_first_occurrence(uid, "hobby", "学吉他")  # 不挂跨类别 source_event_id

    bundle = export_bundle(uid, ["memory"])
    assert "memory_policy" in bundle["data"], list(bundle["data"].keys())[:6]
    # 恢复到空人格（restore 只允许空命名空间，用全新 user_id）
    target = "g01-restore-target"
    result = restore_bundle(bundle, target)
    rows = db.conn.execute(
        "SELECT COUNT(*) n FROM memory_policy WHERE user_id=?", (target,)).fetchone()["n"]
    assert rows >= 1, f"恢复后应有 policy: {rows}"
    print("[OK] LC-1 导出→空目录恢复闭环（memory 类别含三张新表）")
    return 0


def test_real_lifecycle_and_scope() -> int:
    from backend.core.fact_lifecycle import delete_fact_everywhere
    from backend.core.relationship_events import record_memory_corrected

    uid = "g01-real-delete"
    db.ensure_user(uid)
    fid = _fact(uid, "用户喜欢猫")
    other = _fact(uid, "另一条事实")
    event_id = record_memory_corrected(uid, fid, action="rewrite")
    ms.evaluate_fact(uid, fid, explicit=True)
    ms.add_annotation(uid, other, viewpoint="相关视角", source_event_id=event_id)
    ms.mark_first_occurrence(uid, "memory_corrected", "猫", event_id)
    for operation in (
        lambda: ms.evaluate_fact("foreign-user", fid),
        lambda: ms.add_annotation("foreign-user", fid),
        lambda: ms.mark_first_occurrence("foreign-user", "memory_corrected", "猫", event_id),
    ):
        try:
            operation()
        except ms.SalienceError:
            pass
        else:
            raise AssertionError("cross-persona source must be rejected")
    with patch("backend.core.fact_lifecycle._delete_vectors") as vectors:
        assert delete_fact_everywhere(uid, fid)
        vectors.assert_called_once_with(uid, [fid])
    assert ms.get_policy(uid, fid) is None
    assert ms.annotations_for(uid, other) == []
    assert ms.is_first_occurrence(uid, "memory_corrected", "猫")
    assert not delete_fact_everywhere(uid, fid)
    return 0


def test_annotation_event_remap() -> int:
    from backend.core.relationship_events import record_memory_corrected
    from backend.core.relationship_export import export_bundle, restore_bundle

    uid, target = "g01-event-export", "g01-event-import"
    db.ensure_user(uid)
    fid = _fact(uid, "事件来源")
    event_id = record_memory_corrected(uid, fid, action="rewrite")
    ms.add_annotation(uid, fid, source_event_id=event_id)
    ms.mark_first_occurrence(uid, "memory_corrected", "来源", event_id)
    restore_bundle(export_bundle(uid, ["memory", "events"]), target)
    row = db.conn.execute(
        "SELECT a.source_event_id, e.user_id AS event_user FROM memory_annotations a "
        "JOIN relationship_events e ON e.id=a.source_event_id WHERE a.user_id=?",
        (target,),
    ).fetchone()
    assert row and row["event_user"] == target
    assert row["source_event_id"] != event_id
    return 0


def test_observation_sources_and_replay() -> int:
    uid = "g01-observation"
    db.ensure_user(uid)
    fid = _fact(uid, "用户热爱天文学")
    moment = datetime(2026, 9, 8, 12)
    ids = [db.add_message(uid, "user", "天文学对我很重要") for _ in range(3)]
    assistant_id = db.add_message(uid, "assistant", "一定要记住")
    foreign_id = db.add_message("foreign-observation", "user", "一定要记住")
    with db._lock:
        db.conn.execute("UPDATE messages SET ts=? WHERE user_id=?",
                        (moment.isoformat(), uid))
        db.conn.commit()
    first = ms.observe_fact(uid, fid, ids + [assistant_id, foreign_id, 999999],
                            now=moment, new_fact=True)
    assert first["score"] == 50 and first["distinct_days"] == 1
    assert json.loads(first["source_message_ids"]) == ids
    deadline = first["review_at"]
    for day in range(1, 15):
        mid = db.add_message(uid, "user", "继续讨论天文学")
        with db._lock:
            db.conn.execute("UPDATE messages SET ts=? WHERE id=?",
                            ((moment + timedelta(days=day)).isoformat(), mid))
            db.conn.commit()
        policy = ms.observe_fact(uid, fid, [mid], now=moment + timedelta(days=day))
        assert policy["review_at"] == deadline
    assert policy["score"] == 70 and policy["tier"] == "long"
    row = db.conn.execute("SELECT expires_at, confidence FROM facts WHERE id=?", (fid,)).fetchone()
    assert row["expires_at"] is None and row["confidence"] == 0.7
    legacy = _fact(uid, "用户有一把吉他")
    assert ms.observe_fact(uid, legacy, [], now=moment) is None
    with patch("backend.core.features.flag", return_value=False):
        assert ms.observe_fact(uid, legacy, ids, new_fact=True) is None
    from backend.core.relationship_export import export_bundle, restore_bundle
    restore_bundle(export_bundle(uid, ["memory"]), "g01-observation-restored")
    restored = db.conn.execute(
        "SELECT source_message_ids FROM memory_policy WHERE user_id=?",
        ("g01-observation-restored",),
    ).fetchone()
    assert json.loads(restored["source_message_ids"]) == []
    return 0


def test_v24_policy_upgrade() -> int:
    from backend.core.userdb import UserDB, _SCHEMA
    from backend.core.config import config
    with tempfile.TemporaryDirectory(prefix="g01-upgrade-") as raw:
        root = Path(raw)
        conn = sqlite3.connect(root / "bot.db")
        old_schema = _SCHEMA.replace("    source_message_ids TEXT NOT NULL DEFAULT '[]',\n", "")
        old_schema = old_schema.replace("    first_observed_at TEXT,\n", "")
        conn.executescript(old_schema)
        conn.execute("INSERT INTO memory_policy(user_id,fact_id,tier,score,updated_at) "
                     "VALUES ('upgrade',1,'legacy',10,'2026-09-08')")
        conn.execute("PRAGMA user_version=24")
        conn.commit()
        conn.close()
        with patch.object(config, "data_dir", root):
            upgraded = UserDB()
            try:
                row = upgraded.conn.execute("SELECT * FROM memory_policy").fetchone()
                assert row["tier"] == "legacy" and row["source_message_ids"] == "[]"
                assert row["first_observed_at"] is None
                assert upgraded.conn.execute("PRAGMA user_version").fetchone()[0] == 27
                assert list((root / "backups").glob("schema-bot-v24-to-v27-*/bot.db"))
            finally:
                upgraded.conn.close()
    return 0


async def test_extraction_produces_policy() -> int:
    from backend.core import daily
    uid = "g01-extract-policy"
    db.ensure_user(uid)
    ids = [db.add_message(uid, "user", "观星这件事对我很重要") for _ in range(8)]
    content = "用户喜欢观星"
    response = {"facts": [{"content": content, "source_message_ids": ids}], "style": ""}
    with patch("backend.core.daily.chat", new=AsyncMock(return_value=json.dumps(response))), \
         patch("backend.core.vector_store.index", return_value=True), \
         patch("backend.core.date_memory.extract_from_transcript", new=AsyncMock()):
        await daily.extract_facts(uid)
    fact = db.conn.execute("SELECT id FROM facts WHERE user_id=?", (uid,)).fetchone()
    policy = ms.get_policy(uid, fact["id"])
    assert policy["score"] == 50 and policy["distinct_days"] == 1
    more = [db.add_message(uid, "user", "又想聊聊星空") for _ in range(8)]
    response["facts"][0].update(existing_fact_id=fact["id"], source_message_ids=more)
    with patch("backend.core.daily.chat", new=AsyncMock(return_value=json.dumps(response))), \
         patch("backend.core.vector_store.index", return_value=True), \
         patch("backend.core.date_memory.extract_from_transcript", new=AsyncMock()):
        await daily.extract_facts(uid)
    assert db.conn.execute("SELECT COUNT(*) FROM facts WHERE user_id=?", (uid,)).fetchone()[0] == 1
    assert len(json.loads(ms.get_policy(uid, fact["id"])["source_message_ids"])) == 16
    return 0


def test_production_sources() -> int:
    """G01 片3：锚点/初历/注释的生产来源（真实事件链路，非手工调用）。"""
    from backend.core.fact_lifecycle import update_fact_everywhere
    from backend.core.relationship_events import record, invalidate_for_source

    uid = "g01-production"
    db.ensure_user(uid)
    # 1) 用户亲手改写记忆 → memory_corrected 事件 → user_teaching 注释自动登记；
    #    同一事实重复确认不堆积（只保留最近一条 user_teaching）。
    fid = _fact(uid, "用户喜欢猫")
    with patch("backend.core.fact_lifecycle._delete_vectors"), \
         patch("backend.core.fact_lifecycle._index_vector"):
        assert update_fact_everywhere(uid, fid, "用户非常喜欢猫")
    anns = ms.annotations_for(uid, fid)
    assert any(a["origin"] == "user_teaching" for a in anns), anns
    fid_rewrite = next(f["id"] for f in db.conn.execute(
        "SELECT id FROM facts WHERE user_id=?", (uid,)).fetchall())
    with patch("backend.core.fact_lifecycle._delete_vectors"), \
         patch("backend.core.fact_lifecycle._index_vector"):
        update_fact_everywhere(uid, fid_rewrite, "用户特别喜欢猫")
    anns = ms.annotations_for(uid, fid_rewrite)
    teaching = [a for a in anns if a["origin"] == "user_teaching"]
    assert len(teaching) == 1, teaching

    # 2) 关系事件落库 → 邻近窗口内的新事实自动获得锚点加成（score 0→20）。
    anchored = _fact(uid, "用户周末想学摄影")
    ms.observe_fact(uid, anchored, [], new_fact=True)
    assert ms.get_policy(uid, anchored)["score"] == 0
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO activities (user_id, kind, document_id, status, title, created_at, updated_at) "
            "VALUES (?, 'goal', 0, 'completed', '周末摄影计划', datetime('now'), datetime('now'))", (uid,))
        activity_id = cur.lastrowid
        db.conn.commit()
    event_id = record(uid, "goal_completed", "activity", activity_id,
                      subject=uid, obj="周末摄影计划")
    assert event_id is not None
    policy = ms.get_policy(uid, anchored)
    assert policy["relationship_anchor"] == 1 and policy["score"] == 20, policy

    # 3) 事件落库时按事件主题自动建立初历；重复事件不吃首次加成；
    #    源事件作废 → 初历级联消失，但锚点已入账的 policy 不回滚。
    assert ms.is_first_occurrence(uid, "goal_completed", "学画") is True, \
        "不同主题仍是初历"
    with db._lock:
        row = db.conn.execute(
            "SELECT COUNT(*) n FROM first_occurrences WHERE user_id=? AND source_event_id=?",
            (uid, event_id)).fetchone()
    assert row["n"] == 1
    repeat = record(uid, "goal_completed", "activity", activity_id,
                    subject=uid, obj="周末摄影计划")
    assert repeat is None, "同一 (user,type,source) 幂等不重复登记"
    invalidate_for_source(uid, "activity", activity_id)
    with db._lock:
        row = db.conn.execute(
            "SELECT COUNT(*) n FROM first_occurrences WHERE user_id=? AND source_event_id=?",
            (uid, event_id)).fetchone()
    assert row["n"] == 0
    print("[OK] 生产来源：改写→user_teaching 注释 / 事件→锚点重评 / 事件→初历与级联")
    return 0


def test_lifecycle_display_and_fading() -> int:
    """G01 片4：解释层露出（非敏感元数据 + 二次授权）与可见遗忘候选。"""
    from backend.core.explainability import build_reply_explanation
    from backend.core.state import AgentState
    from backend.core.userdb import update_fact_pinned

    uid = "g01-display"
    db.ensure_user(uid)
    now = datetime.now()
    pinned = _fact(uid, "用户喜欢猫")
    short = _fact(uid, "用户最近在学吉他")
    secret = db.add_fact(uid, "用户不提的私事", surface_policy="never_surface")
    dated = db.add_fact(uid, "用户这周在赶项目",
                        expires_at=(now + timedelta(days=2)).isoformat(timespec="seconds"))
    update_fact_pinned(uid, pinned, True)
    ms.observe_fact(uid, short, [], now=now - timedelta(days=28), new_fact=True)
    ms.observe_fact(uid, dated, [], now=now - timedelta(days=1), new_fact=True)
    ms.record_user_teaching_annotation(uid, pinned)

    meta = ms.lifecycle_for_facts(uid, [pinned, short, secret, dated, 999999])
    assert meta[pinned]["retention"] == "长期保留" and meta[pinned]["user_confirmed"]
    assert meta[short]["retention"].startswith("保留到 "), meta[short]
    assert secret not in meta, "never_surface 不进入解释层（二次授权）"
    assert meta[dated]["retention"].startswith("保留到 ")
    assert 999999 not in meta, "已删除/不存在的事实不出现"
    for entry in meta.values():
        assert "score" not in entry and "confidence" in entry and entry["can_edit"]
    # 二次授权是返回时查询：改策略后同一批 id 的露出立即收敛。
    db.conn.execute("UPDATE facts SET surface_policy='never_surface' WHERE id=?", (short,))
    db.conn.commit()
    assert short not in ms.lifecycle_for_facts(uid, [short])

    # 解释快照：只给实际引用的事实行附 lifecycle，其余行不带。
    state = AgentState()
    frame = __import__("backend.core.behavior", fromlist=["BehaviorFrame"]).BehaviorFrame(
        mood_line="", stage_line="", texture_line="", initiative="", reaction_line="",
        rest_line="", tension_line="", archive_line="", event_line="",
    )
    snapshot = build_reply_explanation(
        state, frame,
        memory_rows=[("长期事实", "用户喜欢猫", pinned), ("相关对话", "随便聊聊")],
        fact_ids=[pinned], user_id=uid,
    )
    fact_row = next(m for m in snapshot["memories"] if m["text"] == "用户喜欢猫")
    assert fact_row["lifecycle"]["retention"] == "长期保留", fact_row
    other_row = next(m for m in snapshot["memories"] if m["text"] == "随便聊聊")
    assert "lifecycle" not in other_row
    # 旧客户端兼容：不传 fact_ids 时快照结构不变。
    plain = build_reply_explanation(state, frame, memory_rows=[("长期事实", "用户喜欢猫")])
    assert "lifecycle" not in plain["memories"][0]

    # 可见遗忘：到期前候选只给元数据（无原文），到期后由既有衰减物理删除。
    candidates = ms.expiring_soon_candidates(uid, within_days=3, now=now)
    ids = {c["fact_id"] for c in candidates}
    assert dated in ids and pinned not in ids and secret not in ids
    assert all("content" not in c for c in candidates), "候选不含原文"
    from backend.core.pending_thoughts import due_thoughts, sync_pending_thoughts

    assert sync_pending_thoughts(uid) >= 1
    fading = [t for t in due_thoughts(uid, 10) if t["kind"] == "memory_fading"]
    assert fading and dated in {t["source_id"] for t in fading}, fading
    print("[OK] 解释层露出（二次授权/不含 score）+ 可见遗忘到期前候选")
    return 0


def test_policy_lifecycle_consumption() -> int:
    from backend.core.fact_decay import decay_expired_facts
    from backend.core.userdb import update_fact_pinned
    uid = "g01-lifecycle"
    db.ensure_user(uid)
    now = datetime.now()
    expired = _fact(uid, "用户最近在筹备火星展览")
    pinned = _fact(uid, "用户今天临时去看海")
    legacy = _fact(uid, "用户收藏古典唱片")
    explicit_deadline = db.add_fact(uid, "用户最近参与数学竞赛",
                                    expires_at=(now + timedelta(days=20)).isoformat())
    ms.observe_fact(uid, explicit_deadline, [], now=now - timedelta(days=8), new_fact=True)
    ms.observe_fact(uid, expired, [], now=now - timedelta(days=8), new_fact=True)
    ms.add_annotation(uid, expired, viewpoint="相关的感受")
    ms.observe_fact(uid, pinned, [], now=now - timedelta(days=2), new_fact=True)
    update_fact_pinned(uid, pinned, True)
    assert ms.policy_expired_ids(uid, now=now) == {expired}
    assert expired not in db.recallable_fact_ids(uid, [expired, pinned, legacy])
    assert db.recallable_fact_ids(uid, [pinned, legacy]) == {pinned, legacy}
    assert explicit_deadline in db.recallable_fact_ids(uid, [explicit_deadline])
    assert db.search_facts(uid, "火星展览", 10) == []
    assert ms.observe_fact(uid, expired, [], now=now) is None
    real_flag = __import__("backend.core.features", fromlist=["flag"]).flag
    with patch("backend.core.features.flag", side_effect=lambda name: False if name == "memory_lifecycle_enabled" else real_flag(name)):
        assert ms.policy_expired_ids(uid, now=now) == set()
        assert expired in db.recallable_fact_ids(uid, [expired])
    with patch("backend.core.fact_lifecycle._delete_vectors"):
        assert decay_expired_facts(uid, now=now) == [expired]
    assert ms.get_policy(uid, expired) is None and not ms.annotations_for(uid, expired)
    assert ms.get_policy(uid, pinned) is not None
    # 长期分层不能延长用户期限。
    hard = db.add_fact(uid, "用户明天到期的明确期限", expires_at=(now - timedelta(seconds=1)).isoformat())
    ms.evaluate_fact(uid, hard, explicit=True, anchor=True)
    assert hard not in db.recallable_fact_ids(uid, [hard])
    assert ms.short_retention_days("用户今天出差") == 1
    assert ms.short_retention_days("用户正在写书") == 7
    assert ms.short_retention_days("用户喜欢咖啡") == 30
    late_pin = _fact(uid, "临时借用紫色自行车")
    ms.observe_fact(uid, late_pin, [], now=now - timedelta(days=2), new_fact=True)
    original_expired = ms.policy_expired_ids
    def pin_after_scan(*args, **kwargs):
        candidates = original_expired(*args, **kwargs)
        update_fact_pinned(uid, late_pin, True)
        return candidates
    with patch("backend.core.memory_salience.policy_expired_ids", side_effect=pin_after_scan), \
         patch("backend.core.fact_lifecycle._delete_vectors"):
        decay_expired_facts(uid, now=now)
    assert db.conn.execute("SELECT pinned FROM facts WHERE id=?", (late_pin,)).fetchone()[0] == 1
    return 0


def test_fact_ids_by_content_strip() -> int:
    """M1 修复回归：F07 反查按 strip 归一化，检索/向量带空白也能命中。"""
    uid = "g01-reverse-lookup"
    db.ensure_user(uid)
    fid = _fact(uid, "用户喜欢秋天")
    got = db.fact_ids_by_content(uid, ["  用户喜欢秋天  ", "\t用户喜欢秋天\n"])
    assert got.get("用户喜欢秋天") == fid, f"strip 后应命中（实际={got}）"
    assert db.fact_ids_by_content(uid, ["", "   "]) == {}, "空/纯空白输入不应报错或误命中"
    assert db.fact_ids_by_content(uid, ["用户不喜欢秋天"]) == {}, "不存在的正文不应命中"
    print("[OK] F07 反查 strip 归一化：带空白正文命中、空输入安全")
    return 0


async def main() -> int:
    failed = (
        test_score_formula()
        + test_tier_hysteresis_and_pinned()
        + test_policy_shadow_semantics()
        + test_first_occurrence_unique_and_forget()
        + test_annotations_and_cascade()
        + test_export_restore_roundtrip()
        + test_real_lifecycle_and_scope()
        + test_annotation_event_remap()
        + test_observation_sources_and_replay()
        + test_v24_policy_upgrade()
        + await test_extraction_produces_policy()
        + test_production_sources()
        + test_lifecycle_display_and_fading()
        + test_policy_lifecycle_consumption()
        + test_fact_ids_by_content_strip()
    )
    if failed:
        print(f"\n=== G01 记忆显著度：{failed} 项失败 ===")
        return 1
    print("\n=== G01 记忆显著度（影子评分+视角+初历）：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
