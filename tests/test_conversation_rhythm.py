# -*- coding: utf-8 -*-
"""P2-05 会话节奏回归：A 追发生命周期、B 晚安收尾、C 称呼候选。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_rhythm_"))
NOW = datetime(2026, 9, 8, 15, 0, 0)


def test_followup_lifecycle() -> int:
    from backend.core.conversation_rhythm import (add_followup, cancel_followups,
                                                  mark_sent, ready_followups)

    uid = "rhythm-a"
    from backend.core.userdb import db

    db.ensure_user(uid)
    origin = db.add_message(uid, "assistant", "那本书的名字")
    item = add_followup(uid, origin_turn_id=origin, hint="那本书的名字", now=NOW)
    assert item is not None
    assert add_followup(uid, origin_turn_id=origin, hint="那本书的名字", now=NOW) is None, "同源幂等"

    # 20 分钟内不 ready
    assert ready_followups(uid, now=NOW + timedelta(minutes=10)) == []
    ready = ready_followups(uid, now=NOW + timedelta(minutes=21))
    assert len(ready) == 1 and ready[0]["origin_turn_id"] == origin

    # 表达成功 → 实例移除（每来源至多一次）
    mark_sent(uid, origin)
    assert ready_followups(uid, now=NOW + timedelta(minutes=30)) == []
    print("[OK] 追发幂等 / 20 分钟可发 / 表达后移除")
    return 0


def test_followup_expiry_and_new_topic_and_dnd() -> int:
    from backend.core.conversation_rhythm import (add_followup, ready_followups,
                                                  _load)

    uid = "rhythm-b"
    from backend.core.userdb import db

    db.ensure_user(uid)
    first = db.add_message(uid, "assistant", "半句话")
    add_followup(uid, origin_turn_id=first, hint="半句话", now=NOW)
    # 2 小时过期
    assert ready_followups(uid, now=NOW + timedelta(hours=3)) == []
    assert _load(uid) == [], "过期实例清理"

    # 新话题让位：未发追发静默作废
    second = db.add_message(uid, "assistant", "刚才那个梗")
    add_followup(uid, origin_turn_id=second, hint="刚才那个梗", now=NOW)
    kept = ready_followups(uid, now=NOW + timedelta(minutes=25),
                           new_topic_text="对了我想跟你说一件完全无关的事，今天我……")
    assert kept == []
    # 勿扰：旧追发取消，恢复后也不突然补发
    third = db.add_message(uid, "assistant", "睡前那句")
    add_followup(uid, origin_turn_id=third, hint="睡前那句", now=NOW)
    assert ready_followups(uid, now=NOW + timedelta(minutes=25), dnd=True) == []
    assert ready_followups(uid, now=NOW + timedelta(minutes=25)) == []
    print("[OK] 2 小时过期 / 新话题让位 / 勿扰取消")
    return 0


def test_user_turn_creates_and_cancels_sourced_followup() -> int:
    from backend.core.conversation_rhythm import handle_user_turn, ready_followups
    from backend.core.userdb import db

    uid = "rhythm-ingress"
    db.ensure_user(uid)
    origin = db.add_message(uid, "assistant", "我刚想说那本书最后一章其实挺有意思")
    turn = db.add_message(uid, "user", "我先去忙了，回头再聊")
    result = handle_user_turn(uid, turn, "我先去忙了，回头再聊", now=NOW)
    assert result["created"] and result["created"]["origin_turn_id"] == origin
    assert ready_followups(uid, now=NOW + timedelta(minutes=21))
    with db._lock:
        db.conn.execute("DELETE FROM messages WHERE id=? AND user_id=?", (origin, uid))
        db.conn.commit()
    assert ready_followups(uid, now=NOW + timedelta(minutes=22)) == []

    db.add_message(uid, "assistant", "还有半句")
    turn2 = db.add_message(uid, "user", "我先去忙了")
    handle_user_turn(uid, turn2, "我先去忙了", now=NOW)
    assert ready_followups(uid, now=NOW + timedelta(minutes=21))
    stopped = handle_user_turn(uid, turn2 + 1, "不用再补充", now=NOW)
    assert stopped["action"] == "stopped"
    assert ready_followups(uid, now=NOW + timedelta(minutes=21)) == []
    print("[OK] 生产入口创建追发 / 来源删除与手动停止取消")
    return 0


def test_goodnight_cancels_without_penalty() -> int:
    from backend.core.conversation_rhythm import (_expired, _load, add_followup,
                                                  on_goodnight, ready_followups)
    from backend.core.userdb import db

    uid = "rhythm-goodnight"
    db.ensure_user(uid)
    origin = db.add_message(uid, "assistant", "晚安前想说的话")
    add_followup(uid, origin_turn_id=origin, hint="晚安前想说的话", now=NOW)
    result = on_goodnight(uid)
    assert result["cancelled_followups"] == 1 and result["penalty"] is None
    # 取消后 ready 队列为空；下次 ready 扫描会把过期/取消实例清掉
    assert ready_followups(uid, now=NOW + timedelta(minutes=30)) == []
    assert all(i.get("cancelled") or _expired(i, NOW + timedelta(minutes=30))
               for i in _load(uid)) or _load(uid) == []
    print("[OK] 晚安取消本会话追发，无惩罚锁")
    return 0


def test_address_candidates_and_forbidden() -> int:
    from backend.core.conversation_rhythm import address_candidates
    from backend.core.user_preferences import _upsert, migrate_legacy
    from backend.core.userdb import db

    uid = "rhythm-address"
    db.ensure_user(uid)
    db.set_nickname(uid, "老张")           # legacy 允许（惰性迁移）
    migrate_legacy(uid)
    _upsert(uid, "address", {"forbidden": ["爸爸"]}, origin="user_teaching",
            source_message_id=None, status="active", confidence=1.0)
    cands = address_candidates(uid, stage="亲密")
    assert cands["forbidden"] == ["爸爸"], cands
    assert "老张" in cands["allowed_candidates"]
    assert "亲密" in cands["stage_hint"]
    assert cands["sources"] and all(s["preference_id"] for s in cands["sources"])
    assert all(s["revocable"] for s in cands["sources"])
    print("[OK] 称呼禁令硬约束 / 允许列表只作候选 / 来源可查")
    return 0


def main() -> int:
    failed = (
        test_followup_lifecycle()
        + test_followup_expiry_and_new_topic_and_dnd()
        + test_user_turn_creates_and_cancels_sourced_followup()
        + test_goodnight_cancels_without_penalty()
        + test_address_candidates_and_forbidden()
    )
    if failed:
        print(f"\n=== P2-05 会话节奏：{failed} 项失败 ===")
        return 1
    print("\n=== P2-05 会话节奏：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
