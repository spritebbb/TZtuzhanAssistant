# -*- coding: utf-8 -*-
"""C7 记忆纠偏：facts 删改 API、管理端点、纠正语检测、LLM 仲裁与对话内注入。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 数据目录隔离：先于任何 backend import 生效，避免读写真实 bot.db
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_correction_"))

from fastapi.testclient import TestClient

from backend.core.userdb import (
    db,
    delete_fact,
    list_facts,
    resolve_fact_conflict,
    update_fact,
    update_fact_pinned,
    update_fact_surface_policy,
)

UID = "assistant-main"


def _seed_facts() -> dict[str, int]:
    db.ensure_user(UID)
    ids = {}
    for content in ("用户讨厌香菜", "用户住在襄阳", "用户答应周五发 demo"):
        with db._lock:
            cur = db.conn.execute(
                "INSERT INTO facts (user_id, content, ts) VALUES (?, ?, ?)",
                (UID, content, date.today().isoformat()),
            )
            db.conn.commit()
            ids[content] = int(cur.lastrowid)
    return ids


def test_fact_crud() -> None:
    ids = _seed_facts()
    fid = ids["用户讨厌香菜"]
    assert update_fact(UID, fid, "用户其实吃香菜")
    assert not update_fact(UID, fid, "")          # 空内容拒绝
    assert not update_fact(UID, 999999, "不存在")  # 不存在拒绝
    contents = {f["content"] for f in list_facts(UID)}
    assert "用户其实吃香菜" in contents and "用户讨厌香菜" not in contents
    edited = next(f for f in list_facts(UID) if f["id"] == fid)
    assert edited["source_type"] == "user_correction"
    assert edited["confidence"] == 1.0 and edited["verified_at"]
    assert update_fact_surface_policy(UID, fid, "do_not_proactively_surface")
    assert next(f for f in list_facts(UID) if f["id"] == fid)["surface_policy"] == "do_not_proactively_surface"
    assert not update_fact_surface_policy(UID, fid, "invalid")
    assert update_fact_pinned(UID, fid, True)
    assert next(f for f in list_facts(UID) if f["id"] == fid)["pinned"] == 1
    assert update_fact_pinned(UID, fid, False)
    assert next(f for f in list_facts(UID) if f["id"] == fid)["pinned"] == 0
    assert delete_fact(UID, fid)
    assert not delete_fact(UID, fid)  # 已删，返回 False
    assert not any(f["id"] == fid for f in list_facts(UID))
    print("[OK] facts 删改：改写/空值拒绝/删除/幂等")


def test_memory_admin_api() -> None:
    from backend.app import create_app

    ids = _seed_facts()
    fid = ids["用户住在襄阳"]
    with TestClient(create_app()) as client:
        res = client.get("/api/memory/facts")
        assert res.status_code == 200 and any(f["id"] == fid for f in res.json()["facts"])
        assert client.put(f"/api/memory/facts/{fid}", json={"content": "用户住在武汉"}).status_code == 200
        assert client.put(f"/api/memory/facts/{fid}", json={"content": "  "}).status_code == 400
        assert client.patch(
            f"/api/memory/facts/{fid}/surface-policy",
            json={"surface_policy": "do_not_proactively_surface"},
        ).status_code == 200
        fact = next(f for f in client.get("/api/memory/facts").json()["facts"] if f["id"] == fid)
        assert fact["surface_policy"] == "do_not_proactively_surface"
        assert fact["source_type"] == "user_correction" and fact["verified_at"]
        assert client.patch(
            f"/api/memory/facts/{fid}/pinned", json={"pinned": True}
        ).status_code == 200
        fact = next(f for f in client.get("/api/memory/facts").json()["facts"] if f["id"] == fid)
        assert fact["pinned"] == 1
        assert client.patch(
            "/api/memory/facts/999999/pinned", json={"pinned": True}
        ).status_code == 404
        assert client.patch(
            f"/api/memory/facts/{fid}/surface-policy", json={"surface_policy": "invalid"}
        ).status_code == 400
        candidate_id = db.add_fact(
            UID, "用户住在成都", confidence=0.8, conflicts_with_fact_id=fid
        )
        assert candidate_id is not None
        assert client.post(
            f"/api/memory/facts/{candidate_id}/resolve-conflict",
            json={"action": "keep_existing"},
        ).status_code == 200
        assert client.post(
            f"/api/memory/facts/{candidate_id}/resolve-conflict",
            json={"action": "accept_new"},
        ).status_code == 404
        assert client.post(
            f"/api/memory/facts/{fid}/resolve-conflict", json={"action": "invalid"}
        ).status_code == 400
        assert client.delete(f"/api/memory/facts/{fid}").status_code == 200
        assert client.delete(f"/api/memory/facts/{fid}").status_code == 404
    assert not any(f["id"] == fid for f in list_facts(UID))
    print("[OK] 记忆管理 API：列表/改写/删除/404")


def test_correction_detection() -> None:
    from backend.core.memory_correction import is_correction

    hits = ["你记错了", "记错了啦", "我什么时候说过这种话", "我没说过", "你说的不对", "不是那样的"]
    misses = ["你今天记性真好", "随便吧", "你小子", "哈哈哈"]
    assert all(is_correction(t) for t in hits)
    assert not any(is_correction(t) for t in misses)
    print("[OK] 纠正语检测：命中纠正、不误伤调侃")


def test_proactive_surface_policy() -> None:
    from backend.core.initiative import _build_proactive_prompt

    fid = db.add_fact(UID, "用户不想主动聊工作压力", confidence=0.9)
    assert fid is not None
    assert update_fact_surface_policy(UID, fid, "do_not_proactively_surface")
    messages = _build_proactive_prompt(UID)
    assert messages is not None
    prompt = messages[-1]["content"]
    assert "用户明确要求这些记忆不要由你主动提起" in prompt
    assert "用户不想主动聊工作压力" in prompt
    assert "只有用户先提起时才能回应" in prompt
    print("[OK] 不主动提起：主动消息生成收到明确禁提约束")


def test_conflict_pending_and_resolution() -> None:
    old_id = db.add_fact(UID, "用户最喜欢的饮料是红茶", confidence=0.9)
    assert old_id is not None
    rejected_id = db.add_fact(
        UID, "用户最喜欢的饮料是咖啡", confidence=0.8, conflicts_with_fact_id=old_id
    )
    assert rejected_id is not None
    pending = next(f for f in list_facts(UID) if f["id"] == rejected_id)
    assert pending["status"] == "pending_confirmation"
    assert pending["conflicting_content"] == "用户最喜欢的饮料是红茶"
    assert not any(h["content"] == "用户最喜欢的饮料是咖啡" for h in db.search_facts(UID, "饮料咖啡", 5))
    rejected = resolve_fact_conflict(UID, rejected_id, accept_new=False)
    assert rejected and not rejected["accepted"]
    assert not any(f["id"] == rejected_id for f in list_facts(UID))
    assert any(f["id"] == old_id for f in list_facts(UID))

    accepted_id = db.add_fact(
        UID, "用户最喜欢的饮料是豆浆", confidence=0.9, conflicts_with_fact_id=old_id
    )
    assert accepted_id is not None
    accepted = resolve_fact_conflict(UID, accepted_id, accept_new=True)
    assert accepted and accepted["old_fact_id"] == old_id
    visible = list_facts(UID)
    replacement = next(f for f in visible if f["id"] == accepted_id)
    assert replacement["status"] == "active"
    assert replacement["source_type"] == "user_confirmation"
    assert replacement["confidence"] == 1.0 and replacement["verified_at"]
    assert not any(f["id"] == old_id for f in visible)
    print("[OK] 冲突事实：确认前不召回，可保留原事实或采用新事实")


async def test_fact_extraction_provenance() -> None:
    from backend.core import daily

    old_id = db.add_fact(UID, "用户常用的编辑器是 VS Code", confidence=0.9)
    assert old_id is not None
    db.set_last_fact_msg_id(UID, db.max_message_id(UID))
    for index in range(8):
        db.add_message(UID, "user" if index % 2 == 0 else "assistant", f"溯源测试消息 {index}")
    source_rows = db.messages_after(UID, db.get_last_fact_msg_id(UID), 60)
    source_ids = [int(row["id"]) for row in source_rows]

    async def fake_chat(messages, **kwargs):
        return json.dumps({
            "facts": [
                {
                    "content": "用户最近在练习观察雨云",
                    "confidence": 0.86,
                    "retention_days": 30,
                },
                {
                    "content": "用户常用的编辑器是 PyCharm",
                    "confidence": 0.9,
                    "conflicts_with_fact_id": old_id,
                },
            ],
            "style": "",
        })

    async def fake_date_extract(*args, **kwargs):
        return None

    with (
        patch("backend.core.daily.chat", new=fake_chat),
        patch("backend.core.vector_store.index", return_value=True) as vector_index,
        patch("backend.core.date_memory.extract_from_transcript", new=fake_date_extract),
    ):
        await daily.extract_facts(UID)

    extracted = next(f for f in list_facts(UID) if f["content"] == "用户最近在练习观察雨云")
    assert extracted["source_type"] == "conversation_inference"
    assert extracted["confidence"] == 0.86
    assert json.loads(extracted["source_message_ids"]) == source_ids
    assert extracted["verified_at"] is None
    expiry = datetime.fromisoformat(extracted["expires_at"])
    assert datetime.now() + timedelta(days=29) < expiry < datetime.now() + timedelta(days=31)
    conflict = next(
        f for f in list_facts(UID) if f["content"] == "用户常用的编辑器是 PyCharm"
    )
    assert conflict["status"] == "pending_confirmation"
    assert conflict["conflicts_with_fact_id"] == old_id
    vector_index.assert_called_once()
    assert vector_index.call_args.args[2] == "用户最近在练习观察雨云"
    print("[OK] 事实提炼：结构化置信度与原始消息来源完整落库")


async def test_fact_lifecycle_cleanup_and_stale_vector_gate() -> None:
    from backend.core.fact_lifecycle import delete_fact_everywhere, update_fact_everywhere
    from backend.core.memory import long_term
    from backend.core.memory.migration import _sqlite_rows
    from backend.core.memory.vector_store import SearchHit

    fact_id = db.add_fact(UID, "用户每周六固定去游泳", confidence=0.9)
    assert fact_id is not None
    pending_id = db.add_fact(
        UID,
        "用户已经不再游泳",
        confidence=0.8,
        conflicts_with_fact_id=fact_id,
    )
    assert pending_id is not None
    with (
        patch("backend.core.vector_store.delete") as vector_delete,
        patch("backend.core.vector_store.index") as vector_index,
    ):
        assert update_fact_everywhere(UID, fact_id, "用户每周日固定去游泳")
        vector_delete.assert_called_once_with(UID, "facts", fact_id)
        vector_index.assert_called_once_with(UID, fact_id, "用户每周日固定去游泳", "facts")
    with db._lock:
        pending_status = db.conn.execute(
            "SELECT status FROM facts WHERE id = ?", (pending_id,)
        ).fetchone()["status"]
    assert pending_status == "rejected"

    stale_hit = SearchHit(fact_id, 0.01, "用户每周日固定去游泳")
    with (
        patch.object(long_term.config, "memory_v2", True),
        patch("backend.core.memory.vector_store.search", return_value=[stale_hit]),
    ):
        assert "用户每周日固定去游泳" in await long_term.recall_facts(UID, "周日游泳")
        with patch("backend.core.vector_store.delete", side_effect=RuntimeError("index busy")):
            assert delete_fact_everywhere(UID, fact_id)
        assert await long_term.recall_facts(UID, "周日游泳") == []

    root_id = db.add_fact(UID, "用户养了一只叫团子的猫", confidence=0.9)
    assert root_id is not None
    child_id = db.add_fact(
        UID, "用户养的猫叫雪球", confidence=0.8, conflicts_with_fact_id=root_id
    )
    assert child_id is not None
    with patch("backend.core.vector_store.delete") as vector_delete:
        assert delete_fact_everywhere(UID, root_id)
    deleted_vector_ids = {call.args[2] for call in vector_delete.call_args_list}
    assert deleted_vector_ids == {root_id, child_id}
    with db._lock:
        remaining = db.conn.execute(
            "SELECT COUNT(*) AS c FROM facts WHERE id IN (?, ?)", (root_id, child_id)
        ).fetchone()["c"]
    assert remaining == 0
    assert all(row["status"] == "active" for row in _sqlite_rows("facts"))
    print("[OK] 生命周期：删改级联冲突候选，陈旧向量无法残留召回")


async def test_arbitrate_and_forget() -> None:
    from backend.core import memory_correction

    ids = _seed_facts()
    keep_id = ids["用户住在襄阳"]
    wrong_id = ids["用户讨厌香菜"]

    async def fake_chat(messages, **kwargs):
        assert "记忆仲裁员" in messages[0]["content"]
        # 清单必须带编号事实
        assert f"{wrong_id}. 用户讨厌香菜" in messages[-1]["content"]
        return json.dumps({"wrong_ids": [wrong_id, 999999, "abc"], "reason": "用户否定了讨厌香菜"})

    with patch("backend.core.memory_correction.chat", new=fake_chat):
        deleted = await memory_correction.arbitrate_and_forget(UID, "你记错了，我吃香菜")

    assert deleted == [wrong_id], deleted  # 非法 id/非数字被过滤
    remaining = {f["id"] for f in list_facts(UID)}
    assert wrong_id not in remaining and keep_id in remaining

    # 仲裁判断「没有错误」时一条不删
    async def fake_chat_none(messages, **kwargs):
        return json.dumps({"wrong_ids": [], "reason": ""})

    with patch("backend.core.memory_correction.chat", new=fake_chat_none):
        assert await memory_correction.arbitrate_and_forget(UID, "你记错了") == []
    print("[OK] LLM 仲裁：只删明确否定的，非法 id 过滤，宁缺勿滥")


async def test_pipeline_correction_injection() -> None:
    from backend.core import affection
    from backend.core.pipeline import process

    affection.set_affection(UID, 60)
    db.set_first_chat_done(UID)
    captured: list[list[dict]] = []

    async def fake_chat(messages, **kwargs):
        captured.append(messages)
        return "【思考】内部\n【回复】哦，是我记岔了"

    with patch("backend.core.pipeline.chat", new=fake_chat):
        await process(UID, "你记错了，我根本不吃辣", mock=True)

    assert captured, "pipeline 未调用主 chat"
    messages = captured[0]
    systems = [m["content"] for m in messages if m["role"] == "system"]
    hit = [s for s in systems if "别嘴硬别辩解" in s]
    assert hit, "纠正语未触发认错注入"
    idx = next(i for i, m in enumerate(messages) if m.get("content") == hit[0])
    last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
    assert idx < last_user, "纠偏注入必须在 user 消息之前"
    print("[OK] 对话内纠偏：认错注入生效且遵守 user-last")


async def main() -> None:
    test_fact_crud()
    test_memory_admin_api()
    test_correction_detection()
    test_proactive_surface_policy()
    test_conflict_pending_and_resolution()
    await test_fact_extraction_provenance()
    await test_fact_lifecycle_cleanup_and_stale_vector_gate()
    await test_arbitrate_and_forget()
    await test_pipeline_correction_injection()
    print("\n=== C7 记忆纠偏：全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
