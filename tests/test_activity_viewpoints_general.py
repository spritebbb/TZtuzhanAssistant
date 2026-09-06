# -*- coding: utf-8 -*-
"""M8-B 全活动感想栏：viewpoints 泛化到全部活动壳类型（goal/list/writing/focus/
reading），菟菚感想草稿基于各类型真实记录生成且不落库。"""
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

from backend.core import activities, colists, cowriting, focus, goals  # noqa: E402
from backend.core.activities import ActivityError  # noqa: E402
from backend.core.userdb import db  # noqa: E402

UID = "assistant-main"


def _vp(payload: dict) -> dict:
    return {item["role"]: item["content"] for item in payload["viewpoints"]}


async def _test_goal_viewpoints_and_draft() -> None:
    goal = goals.start_goal(UID, "坚持晨跑", "一起把身体养好", "明早先跑一公里")
    goals.add_progress(UID, goal["id"], "第一天：跑完了，腿酸但开心")
    payload = activities.save_viewpoint(UID, goal["id"], "user", "有你在后面追着，我不好意思偷懒。")
    assert payload["kind"] == "goal"
    assert _vp(payload)["user"].startswith("有你在后面")
    payload = activities.save_viewpoint(UID, goal["id"], "tuzhan", "我会每天在楼下等你。")
    assert _vp(payload)["tuzhan"] == "我会每天在楼下等你。"

    read = activities.get_viewpoints(UID, goal["id"])
    assert read["ok"] and read["title"] == "坚持晨跑" and len(read["viewpoints"]) == 2
    # 全部落在 position=-1（整体感想）
    assert all(item["position"] == -1 for item in read["viewpoints"])

    captured = {}

    async def fake_chat(messages, **kwargs):
        captured["material"] = messages[1]["content"]
        return "看着你从嘟囔着起床到主动换好跑鞋，我想陪你把这条路一直跑下去。"

    with patch("backend.core.activities.chat", new=fake_chat):
        draft = await activities.viewpoint_draft(UID, goal["id"])
    assert draft["ok"] and draft["origin"] == "llm"
    material = captured["material"]
    assert "坚持晨跑" in material and "一起把身体养好" in material
    assert "第一天：跑完了" in material, "草稿素材应包含真实进展"
    # 草稿不落库
    with db._lock:
        n = db.conn.execute(
            "SELECT COUNT(*) FROM activity_viewpoints WHERE user_id = ? AND activity_id = ?",
            (UID, goal["id"]),
        ).fetchone()[0]
    assert n == 2, "草稿生成不应新增观点行"
    print("[OK] 目标：双栏感想 position=-1、草稿引用动机与真实进展、不落库")


async def _test_list_writing_focus_material() -> None:
    songlist = colists.start_list(UID, "雨天歌单", "song")
    colists.add_item(UID, songlist["id"], "雨天专用 BGM")

    async def fake_chat(messages, **kwargs):
        return "这首一响，窗外的雨都变成了背景板。"

    with patch("backend.core.activities.chat", new=fake_chat):
        draft = await activities.viewpoint_draft(UID, songlist["id"])
    assert draft["ok"]
    payload = activities.save_viewpoint(UID, songlist["id"], "tuzhan", draft["draft"])
    assert _vp(payload)["tuzhan"].startswith("这首一响")

    story = cowriting.start_writing(UID, "灯塔来信", "海边小城的通信")
    cowriting.add_user_turn(UID, story["id"], "第一封信来自三十年前的灯塔。")
    async def fake_chat_writing(messages, **kwargs):
        material = messages[1]["content"]
        assert "灯塔来信" in material and "1段" in material.replace(" ", ""), "写作素材只含标题与轮数"
        assert "第一封信" not in material, "虚构正文不得进入草稿素材（虚构隔离）"
        return "每一次寄信的距离，都是我们在练习不着急。"
    with patch("backend.core.activities.chat", new=fake_chat_writing):
        writing_draft = await activities.viewpoint_draft(UID, story["id"])
    assert writing_draft["ok"]

    timer = focus.start_focus(UID, 25)
    async def fake_chat_focus(messages, **kwargs):
        assert "25 分钟" in messages[1]["content"]
        return "那 25 分钟里我们谁都没说话，但都知道对方在。"
    with patch("backend.core.activities.chat", new=fake_chat_focus):
        focus_draft = await activities.viewpoint_draft(UID, timer["id"])
    assert focus_draft["ok"]
    print("[OK] 清单/共同创作/专注：各自真实记录进素材；虚构正文被隔离在素材之外")


def _test_reading_regression_and_cancelled() -> None:
    # 非共读活动：cancelled 后拒绝修改
    songlist = colists.start_list(UID, "要放下的清单", "song")
    colists.cancel_list(UID, songlist["id"])
    try:
        activities.save_viewpoint(UID, songlist["id"], "user", "补一条感想")
        raise AssertionError("已放下的活动应拒绝修改观点")
    except ActivityError as exc:
        assert "已放下" in str(exc)
    # 跨类型读取：viewpoints 按 activity_id 天然隔离
    other = colists.start_list(UID, "另一份清单", "song")
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
    await _test_goal_viewpoints_and_draft()
    await _test_list_writing_focus_material()
    _test_reading_regression_and_cancelled()
    print("\n=== M8-B 全活动感想栏：全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
