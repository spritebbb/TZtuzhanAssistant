# -*- coding: utf-8 -*-
"""F03 普通聊天中的心事门控：话题相关才注入、回执只记 selected、临时轮不写。

验收锚点（docs/Zcode技术指导.md F03 + 调度文档批次 7）：
- 候选必须到点、来源合法、阶段允许、与话题相关；最多 1 条、≤200 字；
- 注入只记 selected 回执，不 mark_expressed；registry 冷却 4 回合；
- 临时轮只读不写回执、不改心事状态；
- 来源删除使回执对应候选失效；不消耗后台主动额度。
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_f03_"))

from backend.core import pending_thoughts as pt
from backend.core.userdb import db

# 必须相对当前时间：写死日期会随真实时间过期，心事被 expires_at 过滤掉后
# 「话题相关才注入」的断言就会假红。
NOW = datetime.now().replace(microsecond=0)


def _activity(uid: str, title: str) -> int:
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO activities (user_id, kind, document_id, title, status, created_at, updated_at) "
            "VALUES (?, 'reading', 0, ?, 'paused', ?, ?)",
            (uid, title, (NOW - timedelta(days=5)).isoformat(timespec="seconds"),
             (NOW - timedelta(days=5)).isoformat(timespec="seconds")),
        )
        db.conn.commit()
    return int(cur.lastrowid)


def _thought(uid: str, content: str, *, source_type: str = "activity",
             source_id: int | None = None) -> int:
    # 唯一键 (user, kind, source_id)：每次换一个来源活动，避免撞唯一约束
    if source_id is None:
        source_id = _activity(uid, f"书{content[:6]}")
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO pending_thoughts "
            "(user_id, kind, source_type, source_id, content, earliest_at, expires_at, "
            "priority, created_at) VALUES (?, 'resume_reading', ?, ?, ?, ?, ?, 5, ?)",
            (uid, source_type, int(source_id or 0), content,
             (NOW - timedelta(hours=1)).isoformat(timespec="seconds"),
             (NOW + timedelta(days=3)).isoformat(timespec="seconds"),
             NOW.isoformat(timespec="seconds")),
        )
        db.conn.commit()
    return int(cur.lastrowid)


def test_relevance_gate() -> int:
    uid = "f03-gate"
    db.ensure_user(uid)
    aid = _activity(uid, "人类简史")
    tid = _thought(uid, "你们有一本读到一半搁下的《人类简史》，她有点想知道后来读到哪儿了", source_id=aid)
    # 话题相关 → 命中
    hits = pt.context_candidates(uid, "人类简史那本还在读吗", turn_id=101)
    assert [int(t["id"]) for t in hits] == [tid], hits
    # 话题无关 → 不塞
    assert pt.context_candidates(uid, "今天天气不错", turn_id=102) == []
    # 最多 1 条
    tid2 = _thought(uid, "另一条关于书的心事", source_id=_activity(uid, "第二本书"))
    assert len(pt.context_candidates(uid, "书", turn_id=103)) == 1
    assert tid2  # 仅用于确认第二条例存在
    print("[OK] 话题门控：相关才注入、最多 1 条")
    return 0


def test_receipt_and_not_expressed() -> int:
    uid = "f03-receipt"
    db.ensure_user(uid)
    aid = _activity(uid, "深夜食堂")
    tid = _thought(uid, "你们有一本搁下的《深夜食堂》", source_id=aid)
    pt.context_candidates(uid, "深夜食堂还在读吗", turn_id=201)
    rows = pt.receipts_for_turn(uid, 201)
    assert len(rows) == 1 and rows[0]["status"] == "selected"
    assert rows[0]["thought_id"] == tid
    # 注入不等于已表达
    thought = db.conn.execute(
        "SELECT status FROM pending_thoughts WHERE id=?", (tid,)).fetchone()
    assert thought["status"] == "pending"
    # 唯一三元组：重复注入同轮不重复写
    pt.record_receipt(uid, tid, 201, status="selected")
    assert len(pt.receipts_for_turn(uid, 201)) == 1
    # committed 由显式确认推进（首版保守不自动）
    assert pt.commit_receipt(uid, tid, 201) is True
    assert pt.receipts_for_turn(uid, 201)[0]["status"] == "committed"
    assert pt.commit_receipt(uid, tid, 201) is False
    print("[OK] 回执：selected 不改变心事状态；committed 幂等")
    return 0


def test_ephemeral_and_source_gone() -> int:
    uid = "f03-ephemeral"
    db.ensure_user(uid)
    aid = _activity(uid, "局外人")
    tid = _thought(uid, "你们有一本搁下的《局外人》", source_id=aid)
    hits = pt.context_candidates(uid, "局外人还在读吗", turn_id=301, ephemeral=True)
    assert [int(t["id"]) for t in hits] == [tid], "临时轮仍可按隐私规则读取"
    assert pt.receipts_for_turn(uid, 301) == [], "临时轮不写回执"

    # 来源删除 → 候选失效（不回执、不注入）
    with db._lock:
        db.conn.execute("DELETE FROM activities WHERE id=?", (aid,))
        db.conn.commit()
    assert pt.context_candidates(uid, "局外人还在读吗", turn_id=302) == []
    print("[OK] 临时轮只读不写；来源删除候选立即失效")
    return 0


def test_length_cap_and_provider() -> int:
    uid = "f03-provider"
    db.ensure_user(uid)
    aid = _activity(uid, "长书")
    long_content = "你们有一本搁下的书" + "，细节很多" * 60
    _thought(uid, long_content, source_id=aid)
    from backend.core.context_registry import collect_context

    selection = collect_context(uid, "那本书的细节", turn_id=401)
    picked = [c for c in selection.selected] if hasattr(selection, "selected") else []
    text = selection.assemble()
    assert "惦记" in text, text
    assert len(text) <= pt.THOUGHT_CONTEXT_MAX_CHARS + 80, len(text)
    assert picked or text, "registry 应选出心事候选"
    # 冷却：registry 对同一 entry 记录冷却回合（4 回合内不再 fresh 注入）
    key = f"pending_thoughts:{db.conn.execute('SELECT id FROM pending_thoughts WHERE user_id=?', (uid,)).fetchone()['id']}"
    assert selection.fresh_entry_keys, selection.fresh_entry_keys
    assert key in selection.fresh_entry_keys
    print("[OK] 200 字上限 + registry provider 注入与冷却键位")
    return 0


def main() -> int:
    failed = (
        test_relevance_gate()
        + test_receipt_and_not_expressed()
        + test_ephemeral_and_source_gone()
        + test_length_cap_and_provider()
    )
    if failed:
        print(f"\n=== F03 心事门控：{failed} 项失败 ===")
        return 1
    print("\n=== F03 心事门控：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
