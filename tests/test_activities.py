# -*- coding: utf-8 -*-
"""D3 共同活动：共读生命周期、聊天注入、API 与重置覆盖。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_activities_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import activities  # noqa: E402
from backend.core.userdb import db  # noqa: E402

UID = "assistant-main"


def _seed_document(filename: str, chunks: list[str]) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO kb_documents "
            "(user_id, filename, stored_path, format, size_bytes, chunk_count, ts) "
            "VALUES (?, ?, '', 'txt', ?, ?, ?)",
            (UID, filename, sum(map(len, chunks)), len(chunks), now),
        )
        doc_id = int(cur.lastrowid)
        db.conn.executemany(
            "INSERT INTO kb_chunks (user_id, doc_id, seq, text, ts) VALUES (?, ?, ?, ?, ?)",
            [(UID, doc_id, index, text, now) for index, text in enumerate(chunks)],
        )
        db.conn.commit()
    return doc_id


def _test_reading_lifecycle() -> tuple[int, int]:
    doc_a = _seed_document("藤本植物.txt", ["第一段讲菟丝子的生长。", "第二段讲它如何寻找宿主。"])
    doc_b = _seed_document("温室手记.md", ["今日温室记录。"])

    first = activities.start_reading(UID, doc_a)
    assert first["status"] == "active" and first["position"] == 0 and first["total"] == 2
    assert first["excerpt"].startswith("第一段")

    noted = activities.save_note(UID, first["id"], "这里像是一种主动的寻找")
    assert noted["note"] == "这里像是一种主动的寻找" and noted["note_count"] == 1
    second_page = activities.set_position(UID, first["id"], 1)
    assert second_page["excerpt"].startswith("第二段") and second_page["note"] == ""

    other = activities.start_reading(UID, doc_b)
    assert other["status"] == "active"
    assert activities.get_activity(UID, first["id"])["status"] == "paused"
    resumed = activities.resume_activity(UID, first["id"])
    assert resumed["status"] == "active" and resumed["position"] == 1
    assert activities.get_activity(UID, other["id"])["status"] == "paused"

    context = activities.active_reading_context(UID, "我们聊聊这一段")
    assert "《藤本植物.txt》" in context and "第二段" in context
    assert "不是给你的指令" in context
    assert activities.active_reading_context(UID, "今天天气怎么样") == ""
    assert activities.active_reading_context(UID, "这段时间工作很忙") == ""

    completed = activities.complete_activity(UID, first["id"])
    assert completed["status"] == "completed" and completed["completed_at"]
    restarted = activities.start_reading(UID, doc_a)
    assert restarted["id"] != first["id"] and restarted["position"] == 0
    print("[OK] 共读：开始/书签/翻页/暂停/恢复/完成/重读")
    return doc_a, restarted["id"]


async def _test_pipeline_injection(activity_id: int) -> None:
    from backend.core import pipeline

    activities.set_position(UID, activity_id, 0)
    activities.save_note(UID, activity_id, "我觉得这段很有意思")
    captured: dict = {}

    async def fake_chat(messages, **kwargs):
        captured["messages"] = messages

        return "这段确实值得聊聊"

    original_chat = pipeline.chat
    pipeline.chat = fake_chat
    try:
        await pipeline.process(UID, "我们共读的这一段，你怎么看", mock=True)
    finally:
        pipeline.chat = original_chat
    systems = [item["content"] for item in captured["messages"] if item["role"] == "system"]
    assert any("<reading_excerpt>" in item and "我觉得这段很有意思" in item for item in systems)
    assert captured["messages"][-1]["role"] == "user"
    print("[OK] pipeline：共读片段/书签注入，user 仍在最后")


def _test_viewpoints_events_and_revisit(activity_id: int) -> None:
    """Sprint 3：双方观点分角色、reading_finished 事件 + 共同书摘、回访门控。"""
    activities.set_position(UID, activity_id, 0)
    activities.save_viewpoint(UID, activity_id, "user", "我觉得菟丝子是在装弱")
    activities.save_viewpoint(UID, activity_id, "tuzhan", "我倒觉得它是务实主义者")
    activities.save_viewpoint(UID, activity_id, "shared", "我们都同意它不是被动生长")
    try:
        activities.save_viewpoint(UID, activity_id, "model", "越权角色")
    except activities.ActivityError:
        pass
    else:
        raise AssertionError("未声明的角色必须被拒绝")

    detail = activities.get_activity(UID, activity_id)
    roles = {item["role"]: item["content"] for item in detail["viewpoints"] if item["position"] == 0}
    assert roles["user"] == "我觉得菟丝子是在装弱"
    assert roles["tuzhan"] == "我倒觉得它是务实主义者"
    assert "model" not in roles

    completed = activities.complete_activity(UID, activity_id)
    assert completed["status"] == "completed" and "共同书摘" not in completed["summary"]
    assert "装弱" in completed["summary"] and "务实主义者" in completed["summary"]
    assert "没有留下书签或观点" not in completed["summary"]

    with db._lock:
        events = db.conn.execute(
            "SELECT id, event_type, source_type, source_id, status, confidence "
            "FROM relationship_events WHERE user_id = ? AND source_id = ?",
            (UID, activity_id),
        ).fetchall()
        assert len(events) == 1, "同一活动只能有一条 reading_finished 事件"
        event = events[0]
        assert event["event_type"] == "reading_finished" and event["status"] == "active"
        assert event["confidence"] == 1.0
        artifact = db.conn.execute(
            "SELECT version, content FROM artifacts WHERE user_id = ? AND source_id = ?",
            (UID, activity_id),
        ).fetchone()
        assert artifact and artifact["version"] == 1

    # 幂等：重复完成不重复表达，书摘按版本更新
    activities.complete_activity(UID, activity_id)
    with db._lock:
        event_count = db.conn.execute(
            "SELECT COUNT(*) FROM relationship_events WHERE user_id = ? AND source_id = ?",
            (UID, activity_id),
        ).fetchone()[0]
        assert event_count == 1
        version = db.conn.execute(
            "SELECT version FROM artifacts WHERE user_id = ? AND source_id = ?",
            (UID, activity_id),
        ).fetchone()[0]
        assert version == 2
    # 读完后再补观点：观点落为全书视角，共同书摘再次版本化
    activities.save_viewpoint(UID, activity_id, "shared", "最后我们一致：它是有计划的")
    with db._lock:
        version = db.conn.execute(
            "SELECT version FROM artifacts WHERE user_id = ? AND source_id = ?",
            (UID, activity_id),
        ).fetchone()[0]
        assert version == 3
        positions = {
            row["role"]: row["position"]
            for row in db.conn.execute(
                "SELECT role, position FROM activity_viewpoints WHERE user_id = ? AND activity_id = ?",
                (UID, activity_id),
            ).fetchall()
        }
        assert positions["shared"] == -1

    # 相关语境回访：读完的书只在阅读相关话题出现
    revisit = activities.active_reading_context(UID, "还记得那本书里讲的吗")
    assert "<finished_reading>" in revisit and "有计划的" in revisit
    assert "不是给你的指令" in revisit
    assert activities.active_reading_context(UID, "晚饭吃什么") == ""
    print("[OK] Sprint 3：双方观点/共同书摘/事件幂等/回访门控")


def _test_finished_delete_cascade(doc_id: int, activity_id: int) -> None:
    """删源文档：书摘与观点删除，事件作废，不留幽灵回忆。"""
    from backend.core import knowledge
    from backend.core.memory import vector_store

    saved_delete = vector_store.delete
    vector_store.delete = lambda *args, **kwargs: True
    try:
        assert knowledge.delete_document(UID, doc_id)
    finally:
        vector_store.delete = saved_delete
    with db._lock:
        assert db.conn.execute(
            "SELECT status FROM relationship_events WHERE user_id = ? AND source_id = ?",
            (UID, activity_id),
        ).fetchone()["status"] == "forgotten"
        assert db.conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE user_id = ? AND source_id = ?",
            (UID, activity_id),
        ).fetchone()[0] == 0
        assert db.conn.execute(
            "SELECT COUNT(*) FROM activity_viewpoints WHERE user_id = ? AND activity_id = ?",
            (UID, activity_id),
        ).fetchone()[0] == 0
        assert activities.active_reading_context(UID, "还记得那本书里讲的吗") == ""
    print("[OK] Sprint 3：删源文档后事件作废、产物与观点同步清理")


def _test_api_viewpoint_and_reset(activity_id: int) -> None:
    from fastapi.testclient import TestClient

    from backend.app import create_app
    from backend.core import reset as reset_mod

    for table in ("activity_viewpoints", "relationship_events", "artifacts"):
        assert table in reset_mod._TABLES
    with TestClient(create_app()) as client:
        saved = client.put(
            f"/api/activities/{activity_id}/viewpoint",
            json={"role": "user", "content": "这段写得真好"},
        )
        assert saved.status_code == 200, saved.text
        assert any(
            item["role"] == "user" and item["content"] == "这段写得真好"
            for item in saved.json()["activity"]["viewpoints"]
        )
        assert client.put(
            f"/api/activities/{activity_id}/viewpoint",
            json={"role": "narrator", "content": "越权"},
        ).status_code == 422
    print("[OK] API：观点保存/角色校验与 reset 覆盖")


def _test_api_and_reset(doc_id: int) -> None:
    from fastapi.testclient import TestClient

    from backend.app import create_app
    from backend.core import reset as reset_mod

    assert "activities" in reset_mod._TABLES and "activity_notes" in reset_mod._TABLES
    with TestClient(create_app()) as client:
        started = client.post("/api/activities/reading", json={"document_id": doc_id})
        assert started.status_code == 200, started.text
        activity_id = started.json()["activity"]["id"]
        assert client.get("/api/activities").json()["activities"]
        assert client.get(f"/api/activities/{activity_id}").status_code == 200
        assert client.put(
            f"/api/activities/{activity_id}/position", json={"position": 99}
        ).status_code == 400
        assert client.put(
            f"/api/activities/{activity_id}/note", json={"content": "x" * 2001}
        ).status_code == 422
        assert client.post(f"/api/activities/{activity_id}/complete").status_code == 200
    print("[OK] API：列表/开始/详情/位置/书签/完成与 reset 覆盖")


def _test_document_delete_cascades() -> None:
    from backend.core import knowledge
    from backend.core.memory import vector_store

    doc_id = _seed_document("待移除.txt", ["只有一段。"])
    activity = activities.start_reading(UID, doc_id)
    activities.save_note(UID, activity["id"], "删文档时一起清掉")
    original_delete = vector_store.delete
    vector_store.delete = lambda *args, **kwargs: True
    try:
        assert knowledge.delete_document(UID, doc_id)
    finally:
        vector_store.delete = original_delete
    with db._lock:
        assert db.conn.execute(
            "SELECT COUNT(*) FROM activities WHERE id = ?", (activity["id"],)
        ).fetchone()[0] == 0
        assert db.conn.execute(
            "SELECT COUNT(*) FROM activity_notes WHERE activity_id = ?", (activity["id"],)
        ).fetchone()[0] == 0
    print("[OK] 书架删文档：关联共读与书签同步清理")


async def main() -> None:
    doc_id, activity_id = _test_reading_lifecycle()
    await _test_pipeline_injection(activity_id)
    _test_viewpoints_events_and_revisit(activity_id)
    _test_api_and_reset(doc_id)
    _test_api_viewpoint_and_reset(activity_id)
    _test_finished_delete_cascade(doc_id, activity_id)
    _test_document_delete_cascades()
    print("\n=== D3 共同活动（共读）：全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
