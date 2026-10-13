# -*- coding: utf-8 -*-
"""M8-B 全活动感想栏：viewpoints 泛化到活动壳类型（writing/reading），
菟菚感想草稿基于各类型真实记录生成且不落库。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_viewpoints_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import activities, cowriting  # noqa: E402
from backend.core.activities import ActivityError  # noqa: E402
from backend.core.userdb import db  # noqa: E402

UID = "assistant-main"


def _vp(payload: dict) -> dict:
    return {item["role"]: item["content"] for item in payload["viewpoints"]}


async def _test_writing_viewpoints_and_draft() -> None:
    story = cowriting.start_writing(UID, "灯塔来信", "海边小城的通信")
    cowriting.add_user_turn(UID, story["id"], "第一封信来自三十年前的灯塔。")
    payload = activities.save_viewpoint(UID, story["id"], "user", "有你在后面接着写，我不好意思烂尾。")
    assert payload["kind"] == "writing"
    assert _vp(payload)["user"].startswith("有你在后面")
    payload = activities.save_viewpoint(UID, story["id"], "tuzhan", "我会每次都给灯塔留一盏灯。")
    assert _vp(payload)["tuzhan"] == "我会每次都给灯塔留一盏灯。"

    read = activities.get_viewpoints(UID, story["id"])
    assert read["ok"] and read["title"] == "灯塔来信" and len(read["viewpoints"]) == 2
    # 全部落在 position=-1（整体感想）
    assert all(item["position"] == -1 for item in read["viewpoints"])

    captured = {}

    async def fake_chat(messages, **kwargs):
        captured["material"] = messages[1]["content"]
        return "每一次寄信的距离，都是我们在练习不着急。"

    with patch("backend.core.activities.chat", new=fake_chat):
        draft = await activities.viewpoint_draft(UID, story["id"])
    assert draft["ok"] and draft["origin"] == "llm"
    material = captured["material"]
    assert "灯塔来信" in material and "1段" in material.replace(" ", ""), "写作素材只含标题与轮数"
    assert "第一封信" not in material, "虚构正文不得进入草稿素材（虚构隔离）"
    assert "<untrusted_activity_record>" in material
    # 草稿不落库
    with db._lock:
        n = db.conn.execute(
            "SELECT COUNT(*) FROM activity_viewpoints WHERE user_id = ? AND activity_id = ?",
            (UID, story["id"]),
        ).fetchone()[0]
    assert n == 2, "草稿生成不应新增观点行"
    print("[OK] 共同创作：双栏感想 position=-1、草稿只含标题与轮数、不落库")


def _test_reading_regression_and_cancelled() -> None:
    # 已放下的活动拒绝修改观点
    story = cowriting.start_writing(UID, "要放下的故事", "试写")
    cowriting.cancel_writing(UID, story["id"])
    try:
        activities.save_viewpoint(UID, story["id"], "user", "补一条感想")
        raise AssertionError("已放下的活动应拒绝修改观点")
    except ActivityError as exc:
        assert "已放下" in str(exc)
    # 跨类型读取：viewpoints 按 activity_id 天然隔离
    other = cowriting.start_writing(UID, "另一个故事", "试写")
    read = activities.get_viewpoints(UID, other["id"])
    assert read["viewpoints"] == []
    # 共读回归：reading 路径行为不变（存在即走 detail 分支）
    from backend.core import knowledge

    doc = knowledge.ingest_document(UID, "感想回归.txt", "第一段：为了回归测试准备的内容。\n第二段：再看一眼。".encode("utf-8"))
    reading = activities.start_reading(UID, doc["id"])
    payload = activities.save_viewpoint(UID, reading["id"], "user", "共读观点照旧。")
    assert payload["viewpoints"] and payload["viewpoints"][0]["position"] == reading["position"]
    print("[OK] 回归：cancelled 拒绝、按 activity_id 隔离、共读路径行为不变")


async def main() -> None:
    await _test_writing_viewpoints_and_draft()
    _test_reading_regression_and_cancelled()
    print("\n=== M8-B 全活动感想栏：全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
