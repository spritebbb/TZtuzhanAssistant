# -*- coding: utf-8 -*-
"""C4 好感度玩法闭环：跨越检测/彩蛋/队列消化/注入/收集/reset 覆盖。

数据目录隔离（TZTUZHAN_DATA_DIR 指向临时目录），不读写真实 bot.db；
pipeline 注入与落账用 mock 模式验证，不依赖真实 LLM。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 数据目录隔离：先于任何 backend import 生效
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_c4_"))

from backend.core import unlock  # noqa: E402
from backend.core.userdb import db, kv_get  # noqa: E402


def _set_affection(user_id: str, value: int) -> None:
    with db._lock:
        db.conn.execute("UPDATE users SET affection = ? WHERE user_id = ?", (value, user_id))
        db.conn.commit()


def _clean(user_id: str) -> None:
    with db._lock:
        db.conn.execute("DELETE FROM unlocks WHERE user_id = ?", (user_id,))
        db.conn.execute("DELETE FROM kv_store WHERE user_id = ?", (user_id,))
        db.conn.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
        db.conn.commit()


def _test_crossing_detection() -> None:
    uid = "c4-cross"
    db.ensure_user(uid)
    _clean(uid)

    # 首次见面：只登记现状不补发（老用户防洪流）
    _set_affection(uid, 30)  # 已在「熟悉」
    assert unlock.check_and_enqueue(uid) == []
    assert unlock.next_pending(uid) is None

    # 同级内波动：不出 pending
    _set_affection(uid, 40)
    assert unlock.check_and_enqueue(uid) == []

    # 单级跨越：熟悉→亲密
    _set_affection(uid, 55)
    keys = unlock.check_and_enqueue(uid)
    assert keys == ["stage_close"], f"应只入队亲密解锁，实际 {keys}"
    pending = unlock.next_pending(uid)
    assert pending is not None and pending["key"] == "stage_close"
    assert len(pending["anchors"]) >= 2, "锚点应随 pending 一起取出"

    # 连跨两级：亲密→恋人 + 达成眷恋，一次入队两条（队列制消化）
    _set_affection(uid, 78)
    keys = unlock.check_and_enqueue(uid)
    assert keys == ["stage_lover", "bond_juanlian"], f"连跨两级应入队两条，实际 {keys}"

    # 跌回去再涨回来：同 key 一生只解锁一次（UNIQUE 守卫）
    _set_affection(uid, 40)
    unlock.check_and_enqueue(uid)
    _set_affection(uid, 80)
    assert unlock.check_and_enqueue(uid) == [], "重复跨越不应再入队"
    _clean(uid)
    print("[OK] 跨越检测：首见登记/同级波动/单级/连跨两级/防重复")


def _test_easter_eggs() -> None:
    uid = "c4-easter"
    db.ensure_user(uid)
    _clean(uid)

    # 满分彩蛋
    _set_affection(uid, 100)
    keys = unlock.check_and_enqueue(uid)
    assert "easter_full100" in keys
    # 满分时阶段/羁绊也在顶，首次登记不补发 threshold 解锁
    assert "stage_lover" not in keys and "bond_baitou" not in keys

    # 连续陪伴彩蛋：造 7 天消息记录（含今天）
    for i in range(7):
        day = (date.today() - timedelta(days=i)).isoformat()
        with db._lock:
            db.conn.execute(
                "INSERT INTO messages (user_id, role, content, ts) VALUES (?, 'user', '在吗', ?)",
                (uid, f"{day}T12:00:00"),
            )
            db.conn.commit()
    keys = unlock.check_and_enqueue(uid)
    assert "easter_streak7" in keys, f"连续 7 天应触发彩蛋，实际 {keys}"
    # 再次检测不重复
    assert unlock.check_and_enqueue(uid) == []

    # 投喂彩蛋：直接插 kb_documents 行模拟 D2 已投喂
    with db._lock:
        db.conn.execute(
            "INSERT INTO kb_documents (user_id, filename, stored_path, format, size_bytes, chunk_count, ts)"
            " VALUES (?, '笔记.md', '/tmp/x.md', 'md', 100, 1, ?)",
            (uid, datetime.now().isoformat(timespec="seconds")),
        )
        db.conn.commit()
    keys = unlock.check_and_enqueue(uid)
    assert "easter_first_doc" in keys

    # 不足 7 天：不触发（换一个干净用户，只造 5 天）
    uid2 = "c4-easter-short"
    db.ensure_user(uid2)
    _clean(uid2)
    _set_affection(uid2, 10)
    for i in range(5):
        day = (date.today() - timedelta(days=i)).isoformat()
        with db._lock:
            db.conn.execute(
                "INSERT INTO messages (user_id, role, content, ts) VALUES (?, 'user', '早', ?)",
                (uid2, f"{day}T08:00:00"),
            )
            db.conn.commit()
    assert "easter_streak7" not in unlock.check_and_enqueue(uid2)

    _clean(uid)
    _clean(uid2)
    with db._lock:
        db.conn.execute("DELETE FROM kb_documents WHERE user_id = 'c4-easter'")
        db.conn.commit()
    print("[OK] 彩蛋：满分/连续7天/首次投喂/不足天数不触发/防重复")


def _test_queue_delivery() -> None:
    uid = "c4-queue"
    db.ensure_user(uid)
    _clean(uid)
    _set_affection(uid, 0)
    unlock.check_and_enqueue(uid)  # 登记基线

    _set_affection(uid, 80)  # 连跨：熟悉/亲密/恋人/眷恋 → 四条排队
    keys = unlock.check_and_enqueue(uid)
    assert len(keys) == 4, f"应入队 4 条，实际 {keys}"

    # 队列制：每轮只见最久等待的一条，pending 不过期
    first = unlock.next_pending(uid)
    assert first is not None and first["key"] == "stage_familiar"
    unlock.mark_delivered(uid, first["key"], "她说：好像有点习惯你了")
    second = unlock.next_pending(uid)
    assert second is not None and second["key"] == "stage_close"

    # 落账后收集槽位：1 delivered + 3 pending + 5 locked = 9
    slots = unlock.list_slots(uid)
    assert len(slots) == 9, "收集页恒为 9 槽位"
    by_status = {}
    for s in slots:
        by_status.setdefault(s["status"], []).append(s["key"])
    assert by_status["delivered"] == ["stage_familiar"]
    assert set(by_status["pending"]) == {"stage_close", "stage_lover", "bond_juanlian"}
    assert len(by_status["locked"]) == 5
    # delivered 槽位带她说的话；pending/locked 不剧透
    fam = next(s for s in slots if s["key"] == "stage_familiar")
    assert fam["content"] == "她说：好像有点习惯你了" and fam["delivered_at"]
    pend = next(s for s in slots if s["key"] == "stage_close")
    assert pend["content"] is None and pend["delivered_at"] is None
    _clean(uid)
    print("[OK] 队列消化：每轮一条/不过期/9 槽位/剧透控制")


async def _test_pipeline_injection() -> None:
    from backend.core import pipeline

    uid = "c4-pipe"
    db.ensure_user(uid)
    _clean(uid)
    _set_affection(uid, 0)
    unlock.check_and_enqueue(uid)
    _set_affection(uid, 30)  # 跨「熟悉」
    assert unlock.check_and_enqueue(uid) == ["stage_familiar"]

    captured: dict = {}

    async def fake_chat(messages, **kwargs):
        captured["messages"] = messages
        return "嗯，其实我有句话想跟你说"

    original_chat = pipeline.chat
    pipeline.chat = fake_chat
    try:
        reply = await pipeline.process(uid, "在吗", mock=True)
    finally:
        pipeline.chat = original_chat

    assert reply, "mock 流程应返回回复"
    systems = [m["content"] for m in captured["messages"] if m["role"] == "system"]
    injected = [c for c in systems if "心里盘旋的念头" in c]
    assert injected, "pipeline 应注入解锁时刻 system 消息"
    body = injected[0]
    assert "好像有点习惯你了" in body, "注入应含主题"
    assert any(a in body for a in unlock._DEF_BY_KEY["stage_familiar"]["anchors"]), "注入应含锚点"
    # 回复落账：解锁被标记 delivered，下一轮不再注入
    assert unlock.next_pending(uid) is None
    slots = unlock.list_slots(uid)
    fam = next(s for s in slots if s["key"] == "stage_familiar")
    assert fam["status"] == "delivered" and fam["content"]
    _clean(uid)
    print("[OK] pipeline 注入与落账：锚点进 system/回复后 delivered/不重复注入")


def _test_reset_coverage() -> None:
    from backend.core import reset as reset_mod

    assert "unlocks" in reset_mod._TABLES
    with db._lock:
        db.conn.execute("SELECT id FROM unlocks LIMIT 1")
    # 首轮检测登记用的 kv 也应在 reset 范围内（kv_store 本就在清单）
    assert "kv_store" in reset_mod._TABLES
    print("[OK] 重置覆盖：unlocks 在 reset 清单且 schema 已建")


async def _test_api() -> None:
    from fastapi.testclient import TestClient

    from backend.app import create_app

    uid = "assistant-main"
    db.ensure_user(uid)
    with TestClient(create_app()) as client:
        resp = client.get("/api/unlocks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] and len(data["slots"]) == 9
        kinds = {s["kind"] for s in data["slots"]}
        assert kinds == {"stage", "bond", "easter"}
    print("[OK] API：/api/unlocks 九槽位闭环")


async def _run() -> None:
    _test_crossing_detection()
    _test_easter_eggs()
    _test_queue_delivery()
    await _test_pipeline_injection()
    _test_reset_coverage()
    await _test_api()


if __name__ == "__main__":
    asyncio.run(_run())
