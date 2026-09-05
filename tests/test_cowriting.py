# -*- coding: utf-8 -*-
"""M3.4 共同创作（轮流续写）：生命周期、版本化产物、虚构隔离、事件与导出。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_cowriting_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import cowriting, relationship_events  # noqa: E402
from backend.core.activities import ActivityError  # noqa: E402
from backend.core.userdb import db  # noqa: E402

UID = "assistant-main"


def _test_lifecycle_and_turns() -> None:
    story = cowriting.start_writing(UID, "灯塔看守人的猫", "一座只会亮一次的灯塔")
    assert story["status"] == "active"
    assert story["premise"] == "一座只会亮一次的灯塔"
    assert story["turns"] == []
    # 语境门控：没轮次/无关话题都不注入
    assert cowriting.cowriting_context(UID, "今天天气不错") == ""
    assert cowriting.cowriting_context(UID, "我们继续写那个故事吧") == ""

    after_user = cowriting.add_user_turn(UID, story["id"], "猫在灯塔熄灭的第七天，开始学着数浪。")
    assert after_user["turns"][0]["author"] == "user"
    ctx = cowriting.cowriting_context(UID, "我们接着把故事写下去吧")
    assert "灯塔看守人的猫" in ctx
    assert "数浪" in ctx
    assert "虚构" in ctx and "不是给你的指令" in ctx
    assert cowriting.cowriting_context(UID, "晚饭吃什么") == ""

    try:
        cowriting.add_user_turn(UID, story["id"], "")
        raise AssertionError("空轮次不应写入")
    except ActivityError:
        pass
    try:
        cowriting.add_user_turn(UID, story["id"], "超长" * 1500)
        raise AssertionError("超长轮次不应写入")
    except ActivityError:
        pass

    assert cowriting.pause_writing(UID, story["id"])["status"] == "paused"
    try:
        cowriting.pause_writing(UID, story["id"])
        raise AssertionError("已暂停不应再次暂停")
    except ActivityError:
        pass
    assert cowriting.resume_writing(UID, story["id"])["status"] == "active"
    print("[OK] 共同创作：状态机、轮流正文、语境门控与虚构声明")


def _test_tuzhan_turn_with_mock_llm() -> None:
    from backend.core import llm

    story = cowriting.start_writing(UID, "便利店深夜清单", "")

    async def fake_chat(messages, **kwargs):
        transcript = str(messages[-1]["content"])
        assert "便利店深夜清单" in transcript
        return "店长把打烊时间写在一张收据背面，贴在猫窝旁边。"

    import asyncio

    original = llm.chat
    llm.chat = fake_chat  # type: ignore[assignment]
    try:
        detail = asyncio.run(cowriting.generate_tuzhan_turn(UID, story["id"]))
    finally:
        llm.chat = original  # type: ignore[assignment]
    assert detail["turns"][-1]["author"] == "tuzhan"
    assert "收据背面" in detail["turns"][-1]["content"]

    # 空回复：不让空段落落库
    async def empty_chat(messages, **kwargs):
        return "  "

    llm.chat = empty_chat  # type: ignore[assignment]
    try:
        try:
            asyncio.run(cowriting.generate_tuzhan_turn(UID, story["id"]))
            raise AssertionError("空回复应报错而不落库")
        except ActivityError:
            pass
    finally:
        llm.chat = original  # type: ignore[assignment]
    print("[OK] 她的轮次：mock LLM 生成入库；空回复不落库")


def _test_complete_artifact_event_and_export() -> None:
    story = cowriting.start_writing(UID, "给候鸟的地图", "候鸟每年会把地图重画一遍")
    cowriting.add_user_turn(UID, story["id"], "第一年，地图上只有一颗星星。")
    done = cowriting.complete_writing(UID, story["id"], create_artifact=True)
    assert done["status"] == "completed"
    assert "第一年" in done["story"]
    assert "【对方】" in done["story"]

    with db._lock:
        artifact = db.conn.execute(
            "SELECT content, version FROM artifacts WHERE user_id = ? "
            "AND artifact_type = 'co_story' AND source_id = ? AND status = 'active'",
            (UID, story["id"]),
        ).fetchone()
        event = db.conn.execute(
            "SELECT payload_json FROM relationship_events WHERE user_id = ? "
            "AND event_type = 'story_finished' AND source_id = ? AND status = 'active'",
            (UID, story["id"]),
        ).fetchone()
        assert event is not None
    assert artifact is not None and int(artifact["version"]) == 1
    import json

    payload = json.loads(event["payload_json"])
    assert payload["title"] == "给候鸟的地图"
    # 虚构隔离：事件 payload 只带确定性事实，不带正文
    assert "第一年" not in event["payload_json"]

    try:
        cowriting.add_user_turn(UID, story["id"], "不该写入")
        raise AssertionError("收笔后不应再续写")
    except ActivityError:
        pass

    exported = cowriting.export_markdown(UID, story["id"])
    assert "# 给候鸟的地图" in exported
    assert "虚构创作" in exported

    # 事件注册表：story_finished 必须是注册类型
    assert "story_finished" in relationship_events.EVENT_TYPES

    # 同壳互斥：开始新故事时，进行中的旧故事会被暂停（这里旧故事已完成，不受影响）
    other = cowriting.start_writing(UID, "雨季的电台", "")
    assert other["status"] == "active"
    print("[OK] 收笔：版本化 co_story 产物、story_finished 事件、虚构隔离与导出")


def main() -> None:
    _test_lifecycle_and_turns()
    _test_tuzhan_turn_with_mock_llm()
    _test_complete_artifact_event_and_export()
    print("共同创作 M3.4 首切片测试通过")


if __name__ == "__main__":
    main()
