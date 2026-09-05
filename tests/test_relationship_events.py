# -*- coding: utf-8 -*-
"""M2 Sprint 2：关系事件内核——4 类事件、幂等/过期/纠正/级联、自然回忆与解释来源。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_events_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import relationship_events as events  # noqa: E402
from backend.core.fact_lifecycle import (  # noqa: E402
    delete_fact_everywhere,
    update_fact_everywhere,
)
from backend.core.userdb import db, save_promise  # noqa: E402

UID = "assistant-main"


def _count(event_type: str, source_id: int, status: str = "active") -> int:
    with db._lock:
        return int(db.conn.execute(
            "SELECT COUNT(*) FROM relationship_events "
            "WHERE user_id = ? AND event_type = ? AND source_id = ? AND status = ?",
            (UID, event_type, source_id, status),
        ).fetchone()[0])


def _test_registry_and_idempotency() -> None:
    # 未注册类型拒绝落库
    try:
        events.record(UID, "dream_happened", "activity", 1)
    except events.RelationshipEventError:
        pass
    else:
        raise AssertionError("未注册的事件类型必须被拒绝")

    # reading_finished：同源幂等，不重复表达
    first = events.record(UID, "reading_finished", "activity", 11, obj="书A",
                          payload={"filename": "书A"})
    assert first is not None
    assert events.record(UID, "reading_finished", "activity", 11, obj="书A") is None
    assert _count("reading_finished", 11) == 1

    # important_date：每年重复的日子刷新同一条事件，不逐年堆积
    saved = events.record(UID, "important_date", "important_date", 5, obj="你的生日",
                          occurred_at="2025-09-05T00:00:00")
    assert saved is not None
    refreshed = events.refresh_important_date(
        UID, {"id": 5, "label": "你的生日", "kind": "birthday"}, date(2026, 9, 5),
    )
    assert refreshed == saved
    assert _count("important_date", 5) == 1
    with db._lock:
        row = db.conn.execute(
            "SELECT occurred_at, expires_at FROM relationship_events WHERE id = ?", (saved,)
        ).fetchone()
        assert row["occurred_at"].startswith("2026-09-05")
        assert row["expires_at"] > row["occurred_at"]
    print("[OK] 类型注册：未注册拒绝；reading_finished 幂等；important_date 按年刷新")


def _test_expiry_correction_invalidation() -> None:
    past = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    expired = events.record(UID, "promise_completed", "promise", 21, obj="旧约定",
                            occurred_at=past, expires_at=past)
    live = events.record(UID, "promise_completed", "promise", 22, obj="新约定")

    actives = events.active_events(UID, event_type="promise_completed")
    assert [item["id"] for item in actives] == [live]
    assert actives[0]["object"] == "新约定"

    assert events.mark_corrected(UID, live)
    with db._lock:
        status = db.conn.execute(
            "SELECT status FROM relationship_events WHERE id = ?", (live,)
        ).fetchone()["status"]
    assert status == "corrected"
    assert events.active_events(UID, event_type="promise_completed") == []

    assert events.invalidate_for_source(UID, "promise", 21) == 1
    assert events.invalidate_for_source(UID, "promise", 9999) == 0
    print("[OK] 过期过滤 / 纠正 corrected / 按来源作废")


def _test_memory_corrected_lifecycle() -> None:
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO facts (user_id, content, ts, source_type, confidence, status) "
            "VALUES (?, '用户喜欢喝美式', ?, 'dialogue', 0.8, 'active')",
            (UID, datetime.now().isoformat(timespec="seconds")),
        )
        fact_id = int(cur.lastrowid)
        db.conn.commit()

    # 改写 → memory_corrected 事件
    assert update_fact_everywhere(UID, fact_id, "用户其实只喝拿铁")
    assert _count("memory_corrected", fact_id) == 1
    with db._lock:
        payload = db.conn.execute(
            "SELECT payload_json FROM relationship_events WHERE event_type = 'memory_corrected' "
            "AND source_id = ?", (fact_id,),
        ).fetchone()["payload_json"]
    assert "拿铁" in payload and "美式" in payload

    # 删除事实 → 关联事件作废，不留幽灵回忆
    assert delete_fact_everywhere(UID, fact_id)
    assert _count("memory_corrected", fact_id) == 0
    with db._lock:
        status = db.conn.execute(
            "SELECT status FROM relationship_events WHERE event_type = 'memory_corrected' "
            "AND source_id = ?", (fact_id,),
        ).fetchone()["status"]
    assert status == "forgotten"
    print("[OK] memory_corrected：改写留痕，删事实级联作废")


def _test_promise_and_recall() -> None:
    promise_id = save_promise(UID, "用户答应周五发 demo", follow_up="", source="")
    assert promise_id is not None
    events.record_promise_completed(UID, {"id": promise_id, "content": "用户答应周五发 demo"})

    # 相关语境：约定话题才回忆
    hit = events.event_recall(UID, "上次说好的那件事怎么样了")
    assert "周五发 demo" in hit["context"] and hit["sources"]
    # 无关话题零注入
    assert events.event_recall(UID, "今天晚饭吃什么")["context"] == ""
    assert events.event_recall(UID, "今天天气怎么样")["context"] == ""

    # 特殊日子：话题里出现日期标签才带出
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO important_dates (user_id, date, label, kind, ts) "
            "VALUES (?, '09-05', '我们认识的日子', 'anniversary', ?)",
            (UID, datetime.now().isoformat(timespec="seconds")),
        )
        date_id = int(cur.lastrowid)
        db.conn.commit()
    events.refresh_important_date(
        UID, {"id": date_id, "label": "我们认识的日子", "kind": "anniversary"}, date.today(),
    )
    date_hit = events.event_recall(UID, "我们认识的日子快到了呢")
    assert "我们认识的日子" in date_hit["context"]
    assert events.event_recall(UID, "随便聊聊最近的节日")["context"] == ""
    print("[OK] 聊天内自然回忆：约定/纪念日按语境门控，无关话题零污染")


async def _test_pipeline_wiring() -> None:
    from backend.core import pipeline

    captured: dict = {}

    async def fake_chat(messages, **kwargs):
        captured["messages"] = messages
        return "嗯，那件事我记得"

    original_chat = pipeline.chat
    pipeline.chat = fake_chat
    try:
        await pipeline.process(UID, "上次说好的那件事，我做到了哦", mock=True)
    finally:
        pipeline.chat = original_chat
    systems = [item["content"] for item in captured["messages"] if item["role"] == "system"]
    assert any("周五发 demo" in item for item in systems)
    assert captured["messages"][-1]["role"] == "user"

    # 解释快照：事件来源可回答「因为哪件真实发生的事」
    from backend.core.explainability import build_reply_explanation
    from backend.core.state import AgentState

    snapshot = build_reply_explanation(
        AgentState(),
        type("Frame", (), {
            "mood_line": "", "stage_line": "", "initiative": "", "reaction_line": "",
            "rest_line": "", "tension_line": "", "archive_line": "", "event_line": "",
        })(),
        memory_rows=[("事件来源", "约定事件：用户答应周五发 demo")],
    )
    assert any(item["kind"] == "事件来源" for item in snapshot["memories"])
    print("[OK] pipeline：事件回忆注入 user 仍在最后；解释快照展示事件来源")


async def main() -> None:
    _test_registry_and_idempotency()
    _test_expiry_correction_invalidation()
    _test_memory_corrected_lifecycle()
    _test_promise_and_recall()
    await _test_pipeline_wiring()
    print("\n=== M2 Sprint 2 关系事件内核：全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
