# -*- coding: utf-8 -*-
"""P2-06 重逢三段式：真实来源、一次投递、可跳过回应与日记收束。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_reunion_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import greeting, relationship_export as rex  # noqa: E402
from backend.core.reunion import (  # noqa: E402
    close_after_daily,
    get_arc,
    mark_offered,
    observe_user_turn,
    prepare_reunion,
    prompt_hint,
)
from backend.core.userdb import db, kv_set  # noqa: E402

NOW = datetime.now().replace(microsecond=0)


def _life_event(uid: str, *, hours_ago: int = 4, description: str = "在研究所整理了一页观察笔记") -> int:
    db.ensure_user(uid)
    occurred = (NOW - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO character_life_events "
            "(user_id,block_id,occurrence,kind,payload_json,namespace,occurred_at,computed_at) "
            "VALUES (?,?,?,'daily_life',?,'character_fiction',?,?)",
            (uid, f"block-{uid}", NOW.date().isoformat(),
             json.dumps({"description": description}, ensure_ascii=False), occurred, occurred),
        )
        db.conn.commit()
        return int(cur.lastrowid)


def test_core_lifecycle() -> int:
    uid = "reunion-core"
    source_id = _life_event(uid)
    arc = prepare_reunion(uid, 12, absent_since=NOW - timedelta(hours=10), now=NOW)
    assert arc and arc["source_snapshot_id"] == source_id
    assert prepare_reunion(uid, 12, absent_since=NOW - timedelta(hours=10), now=NOW) is None
    hint = prompt_hint(uid, arc["id"])
    assert "研究所整理了一页观察笔记" in hint
    assert "去了哪里" in hint and "负罪感" in hint

    offered_id = mark_offered(uid, arc["id"], "回来啦，我刚整理完一页笔记。", now=NOW)
    assert offered_id and mark_offered(uid, arc["id"], "重复", now=NOW) is None
    response_id = db.add_message(uid, "user", "听起来还挺认真，写了什么？")
    assert observe_user_turn(uid, response_id, "听起来还挺认真，写了什么？", now=NOW) == "responded"
    assert close_after_daily(uid, date.today(), now=NOW) == 1
    assert get_arc(uid, arc["id"])["state"] == "closed"
    print("[OK] 真实来源 → offered → responded → 日记收束，重复投递被拒绝")
    return 0


def test_new_topic_and_missing_source() -> int:
    uid = "reunion-skip"
    source_id = _life_event(uid)
    arc = prepare_reunion(uid, 20, absent_since=NOW - timedelta(hours=12), now=NOW)
    assert arc and mark_offered(uid, arc["id"], "我刚忙完。", now=NOW)
    before = db.get_user(uid)
    response_id = db.add_message(uid, "user", "对了，帮我检查一下这段代码的错误")
    assert observe_user_turn(uid, response_id, "对了，帮我检查一下这段代码的错误", now=NOW) == "closed"
    after = db.get_user(uid)
    assert (before["trust"], before["intimacy"]) == (after["trust"], after["intimacy"])

    uid2 = "reunion-deleted"
    source2 = _life_event(uid2)
    arc2 = prepare_reunion(uid2, 20, absent_since=NOW - timedelta(hours=12), now=NOW)
    with db._lock:
        db.conn.execute("DELETE FROM character_life_events WHERE id=?", (source2,))
        db.conn.commit()
    response2 = db.add_message(uid2, "user", "你刚才说什么？")
    assert observe_user_turn(uid2, response2, "你刚才说什么？", now=NOW) is None
    assert get_arc(uid2, arc2["id"])["state"] == "closed"
    print("[OK] 换题直接关闭且不扣关系；来源删除后弧失效")
    return 0


def test_no_source_and_restore_are_safe() -> int:
    uid = "reunion-empty"
    db.ensure_user(uid)
    assert prepare_reunion(uid, 48, absent_since=NOW - timedelta(hours=40), now=NOW) is None

    source_uid = "reunion-export"
    _life_event(source_uid)
    arc = prepare_reunion(source_uid, 12, absent_since=NOW - timedelta(hours=10), now=NOW)
    assert arc and mark_offered(source_uid, arc["id"], "我回来了。", now=NOW)
    bundle = rex.export_bundle(source_uid, ["life"])
    target = "reunion-restored"
    assert rex.preview_restore(bundle, target)["ok"]
    rex.restore_bundle(bundle, target)
    with db._lock:
        restored = db.conn.execute(
            "SELECT state,offered_message_id,response_message_id FROM reunion_arcs WHERE user_id=?",
            (target,),
        ).fetchone()
    assert restored and restored["state"] == "closed"
    assert restored["offered_message_id"] is None and restored["response_message_id"] is None
    print("[OK] 无离线来源不造弧；恢复的历史弧不会重新触发")
    return 0


async def test_greeting_integration() -> int:
    uid = "reunion-greeting"
    _life_event(uid)
    kv_set(uid, "web_last_seen", str(time.time() - 10 * 3600))
    original_chat = greeting.chat

    async def fake_chat(messages, **kwargs):
        prompt = "\n".join(str(m.get("content", "")) for m in messages)
        assert "研究所整理了一页观察笔记" in prompt
        assert "不要追问" in prompt
        return "回来啦，我刚在研究所整理完一页观察笔记。"

    greeting.chat = fake_chat
    try:
        text = await greeting.greeting_for(uid, "current")
    finally:
        greeting.chat = original_chat
    assert text and len(text) <= 180
    with db._lock:
        arc = db.conn.execute(
            "SELECT state,offered_message_id FROM reunion_arcs WHERE user_id=?",
            (uid,),
        ).fetchone()
        message = db.conn.execute(
            "SELECT content FROM messages WHERE id=? AND user_id=?",
            (arc["offered_message_id"], uid),
        ).fetchone()
    assert arc["state"] == "offered" and message["content"] == text
    print("[OK] 久别问候消费真实来源，屏幕文本与日记消息源一致")
    return 0


async def test_no_source_prompt_and_failure_fallback() -> int:
    uid = "reunion-neutral"
    db.ensure_user(uid)
    original_chat = greeting.chat
    seen = ""

    async def failing_chat(messages, **kwargs):
        nonlocal seen
        seen = "\n".join(str(m.get("content", "")) for m in messages)
        raise RuntimeError("offline")

    greeting.chat = failing_chat
    try:
        text = await greeting._greeting_text(
            uid,
            gap_hours=24,
            reunion_hint="没有可追溯的离线生活记录；不要声称离线时做过什么。",
        )
    finally:
        greeting.chat = original_chat
    assert text == "回来啦。好久不见。"
    assert "没有可追溯" in seen
    assert all(word not in text for word in ("舍得", "忘了", "等得"))
    print("[OK] 无来源与模型失败均使用中性问候，不制造负罪感")
    return 0


async def main() -> None:
    test_core_lifecycle()
    test_new_topic_and_missing_source()
    test_no_source_and_restore_are_safe()
    await test_greeting_integration()
    await test_no_source_prompt_and_failure_fallback()
    print("\n=== P2-06 重逢三段式：全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
