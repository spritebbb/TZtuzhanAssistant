# -*- coding: utf-8 -*-
"""C7 记忆纠偏：facts 删改 API、管理端点、纠正语检测、LLM 仲裁与对话内注入。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 数据目录隔离：先于任何 backend import 生效，避免读写真实 bot.db
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_correction_"))

from fastapi.testclient import TestClient

from backend.core.userdb import db, delete_fact, list_facts, update_fact

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
    await test_arbitrate_and_forget()
    await test_pipeline_correction_injection()
    print("\n=== C7 记忆纠偏：全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
