# -*- coding: utf-8 -*-
"""F02 素材互通：引用边、解析边界、命名空间隔离、源消失拒绝、日记接线。

验收锚点（docs/Zcode技术指导.md F02 + 调度文档批次 7）：
- 引用边白名单校验；深 ≤2、总 ≤5、≤1200 token；
- 现实/虚构/观点三 namespace 分别编译，虚构不能当现实证据；
- 源消失立即拒绝使用（解析返回 None，引用边删除）；
- 只增强素材：未登记用途/无链接返回空；不为既有历史补链接。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_f02_"))

from backend.core import narrative_sources as ns
from backend.core.userdb import db

NOW = datetime(2026, 9, 8, 21, 0)


def _event(uid: str, text: str) -> int:
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO relationship_events (user_id, event_type, source_type, source_id, "
            "subject, object, payload_json, confidence, privacy, occurred_at, created_at) "
            "VALUES (?, 'goal_completed', 'activity', 0, ?, ?, '{}', 1.0, 'normal', ?, ?)",
            (uid, uid, text, NOW.isoformat(timespec="seconds"), NOW.isoformat(timespec="seconds")),
        )
        db.conn.commit()
    return int(cur.lastrowid)


def _activity(uid: str, title: str) -> int:
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO activities (user_id, kind, document_id, title, status, created_at, updated_at) "
            "VALUES (?, 'reading', 0, ?, 'active', ?, ?)",
            (uid, title, NOW.isoformat(timespec="seconds"), NOW.isoformat(timespec="seconds")),
        )
        db.conn.commit()
    return int(cur.lastrowid)


def _life(uid: str, text: str) -> int:
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO character_life_events "
            "(user_id, kind, block_id, occurrence, payload_json, occurred_at, computed_at) "
            "VALUES (?, 'daily_life', 'evening', ?, ?, ?, ?)",
            (uid, NOW.date().isoformat(),
             json.dumps({"description": text}, ensure_ascii=False),
             NOW.isoformat(timespec="seconds"), NOW.isoformat(timespec="seconds")),
        )
        db.conn.commit()
    return int(cur.lastrowid)


def test_link_and_whitelist() -> int:
    uid = "f02-link"
    db.ensure_user(uid)
    eid = _event(uid, "一起完成了晨跑计划")
    assert ns.link_source(uid, "diary", 1, "event", eid, version="2026-09-08") is True
    assert ns.link_source(uid, "diary", 1, "event", eid) is False, "同 owner/source 幂等"
    assert len(ns.links_for(uid, "diary", 1)) == 1
    for bad in (
        lambda: ns.link_source(uid, "hack", 1, "event", eid),
        lambda: ns.link_source(uid, "diary", 1, "hack", eid),
    ):
        try:
            bad()
        except ns.NarrativeSourceError:
            pass
        else:
            raise AssertionError("白名单外必须拒绝")
    print("[OK] 引用边：幂等 + owner/source 白名单")
    return 0


def test_resolve_and_source_gone() -> int:
    uid = "f02-resolve"
    db.ensure_user(uid)
    eid = _event(uid, "一起读完了《人类简史》")
    aid = _activity(uid, "晨跑计划")
    ns.link_source(uid, "diary", 2, "event", eid)
    ns.link_source(uid, "diary", 2, "activity", aid)
    materials = ns.collect_sources(uid, "diary:2")
    assert {m.source_type for m in materials} == {"event", "activity"}, materials
    assert all(m.namespace == "reality" for m in materials)
    # 源删除 → 解析拒绝 + 引用边清理
    with db._lock:
        db.conn.execute("DELETE FROM relationship_events WHERE id=?", (eid,))
        db.conn.commit()
    assert ns.resolve_source(uid, {"source_type": "event", "source_id": eid}) is None
    assert ns.forget_for_source(uid, "event", eid) == 1
    materials = ns.collect_sources(uid, "diary:2")
    assert [m.source_type for m in materials] == ["activity"], materials
    print("[OK] 解析只认仍存在的源；源消失删边且下游不可引用")
    return 0


def test_limits_and_namespace_isolation() -> int:
    uid = "f02-limits"
    db.ensure_user(uid)
    for i in range(8):
        ns.link_source(uid, "artifact", 9, "fact", _fact(uid, f"事实{i}"))
    long_life = _life(uid, "她在阳台晒了一下午太阳" * 200)
    ns.link_source(uid, "artifact", 9, "character_life", long_life)
    materials = ns.collect_sources(uid, "artifact:9")
    assert len(materials) <= ns.MAX_ITEMS, len(materials)
    assert sum(m.token_count for m in materials) <= ns.MAX_TOKENS
    # 超长虚构素材被预算挡下；虚构与现实施料分层编译
    compiled = ns.compile_namespaces(materials)
    assert all(m.namespace == "reality" for m in compiled["reality"])
    assert compiled["fiction"] == [] or all(m.namespace == "fiction" for m in compiled["fiction"])
    # 未登记用途 / 无链接：返回空（不为历史补链接）
    assert ns.collect_sources(uid, "unknown:1") == []
    assert ns.collect_sources(uid, "diary:99999") == []
    print("[OK] 边界：总 ≤5 / ≤1200 token / 未登记用途为空 / 命名空间分层")
    return 0


def _fact(uid: str, content: str) -> int:
    # 直接插入：add_fact 会按二元组去重，测试需要确定条数
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO facts (user_id, content, ts, source_type) "
            "VALUES (?, ?, ?, 'conversation_inference')",
            (uid, content, NOW.isoformat(timespec="seconds")),
        )
        db.conn.commit()
    return int(cur.lastrowid)


def test_fiction_not_evidence() -> int:
    uid = "f02-namespace"
    db.ensure_user(uid)
    eid = _event(uid, "真实发生的事")
    lid = _life(uid, "她虚构的日常")
    ns.link_source(uid, "diary", 3, "event", eid)
    ns.link_source(uid, "diary", 3, "character_life", lid)
    compiled = ns.compile_namespaces(ns.collect_sources(uid, "diary:3"))
    assert [m.text for m in compiled["reality"]] == ["真实发生的事"]
    assert [m.text for m in compiled["fiction"]] == ["她虚构的日常"]
    text = ns.format_material(ns.collect_sources(uid, "diary:3"))
    assert "真实记录" in text and "虚构" in text
    print("[OK] 虚构不冒充现实：三 namespace 分别编译并标注")
    return 0


def test_diary_wiring() -> int:
    """写日记时自动登记当天真实事件/完成活动为来源。"""
    from backend.core import daily

    uid = "f02-diary"
    db.ensure_user(uid)
    eid = _event(uid, "当天完成的事")
    with db._lock:
        db.conn.execute(
            "UPDATE relationship_events SET occurred_at=? WHERE id=?",
            (f"{NOW.date().isoformat()}T10:00:00", eid))
        db.conn.execute(
            "INSERT INTO activities (user_id, kind, document_id, title, status, created_at, "
            "updated_at, completed_at) VALUES (?, 'reading', 0, '当天读完的书', 'completed', ?, ?, ?)",
            (uid, NOW.isoformat(timespec="seconds"), NOW.isoformat(timespec="seconds"),
             f"{NOW.date().isoformat()}T11:00:00"))
        db.conn.commit()
    db.conn.execute("DELETE FROM diary WHERE user_id=?", (uid,))
    db.conn.commit()
    from unittest.mock import AsyncMock, patch

    with patch("backend.core.daily.chat", new=AsyncMock(return_value=json.dumps(
            {"content": "今天挺安静的，只记下了一点点。", "mood": "平静"}))):
        import asyncio
        diary = asyncio.run(daily.write_daily_diary(uid, NOW.date(), "用户: 在吗"))
    assert diary and diary["id"]
    links = ns.links_for(uid, "diary", int(diary["id"]))
    kinds = {link["source_type"] for link in links}
    assert "event" in kinds and "activity" in kinds, links
    print("[OK] 日记接线：只把当天真实记录登记为来源")
    return 0


def main() -> int:
    failed = (
        test_link_and_whitelist()
        + test_resolve_and_source_gone()
        + test_limits_and_namespace_isolation()
        + test_fiction_not_evidence()
        + test_diary_wiring()
    )
    if failed:
        print(f"\n=== F02 素材互通：{failed} 项失败 ===")
        return 1
    print("\n=== F02 素材互通：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
