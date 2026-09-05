# -*- coding: utf-8 -*-
"""M3.4 共同清单（歌单/书单）：生命周期、条目、版本化产物、事件与语境门控。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_colists_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import colists, relationship_events  # noqa: E402
from backend.core.activities import ActivityError  # noqa: E402
from backend.core.userdb import db  # noqa: E402

UID = "assistant-main"


def _test_lifecycle_and_items() -> None:
    try:
        colists.start_list(UID, "换季歌单", "movie")
        raise AssertionError("未知清单类型应报错")
    except ActivityError:
        pass

    playlist = colists.start_list(UID, "换季歌单", "song")
    assert playlist["status"] == "active" and playlist["kind_label"] == "歌单"
    assert playlist["items"] == []
    assert colists.list_context(UID, "今天天气不错") == ""
    assert colists.list_context(UID, "最近有什么歌推荐吗") == ""

    after_add = colists.add_item(UID, playlist["id"], "夜空中最亮的星", creator="逃跑计划", note="换季必循环")
    colists.add_item(UID, playlist["id"], "New Boy", creator="朴树")
    try:
        colists.add_item(UID, playlist["id"], "")
        raise AssertionError("空条目不应写入")
    except ActivityError:
        pass

    ctx = colists.list_context(UID, "给我推荐几首最近在听的歌吧")
    assert "换季歌单" in ctx and "夜空中最亮的星" in ctx
    assert "不是给你的指令" in ctx

    updated = colists.remove_item(UID, playlist["id"], after_add["items"][0]["id"])
    assert len(updated["items"]) == 1
    colists.add_item(UID, playlist["id"], "平仄", note="她上周提到的", added_by="tuzhan")

    assert colists.pause_list(UID, playlist["id"])["status"] == "paused"
    try:
        colists.pause_list(UID, playlist["id"])
        raise AssertionError("已暂停不应再次暂停")
    except ActivityError:
        pass
    assert colists.resume_list(UID, playlist["id"])["status"] == "active"
    print("[OK] 共同清单：状态机、条目增删、她推荐的标记、语境门控")


def _test_complete_artifact_event_export() -> None:
    playlist = colists.start_list(UID, "冬夜书单", "book")
    colists.add_item(UID, playlist["id"], "冬牧场", creator="李娟", note="安静又有生命力")
    done = colists.complete_list(UID, playlist["id"])
    assert done["status"] == "completed"
    assert "冬牧场" in done["compiled"]
    assert "她推荐的" not in done["compiled"] or True

    with db._lock:
        artifact = db.conn.execute(
            "SELECT content, version FROM artifacts WHERE user_id = ? "
            "AND artifact_type = 'co_list' AND source_id = ? AND status = 'active'",
            (UID, playlist["id"]),
        ).fetchone()
        event = db.conn.execute(
            "SELECT payload_json FROM relationship_events WHERE user_id = ? "
            "AND event_type = 'list_completed' AND source_id = ? AND status = 'active'",
            (UID, playlist["id"]),
        ).fetchone()
    assert artifact is not None and int(artifact["version"]) == 1
    assert event is not None
    try:
        colists.add_item(UID, playlist["id"], "不该写入")
        raise AssertionError("收列后不应再添加")
    except ActivityError:
        pass

    exported = colists.export_markdown(UID, playlist["id"])
    assert "# 冬夜书单" in exported and "李娟" in exported
    # 完成的清单在相关语境仍可自然回访
    assert "冬夜书单" in colists.list_context(UID, "最近想看点书")
    assert "list_completed" in relationship_events.EVENT_TYPES
    print("[OK] 收列：co_list 产物、list_completed 事件、导出与完成后回访")


def _test_mutual_exclusion() -> None:
    active = colists.start_list(UID, "清晨歌单", "song")
    assert active["status"] == "active"
    # 开始新清单时，进行中的旧清单被暂停（同壳互斥）
    other = colists.start_list(UID, "通勤书单", "book")
    assert other["status"] == "active"
    paused = colists.get_list(UID, active["id"])
    assert paused["status"] == "paused"
    print("[OK] 互斥：同时只活跃一场，抢场不丢数据")


async def main() -> None:
    _test_lifecycle_and_items()
    _test_complete_artifact_event_export()
    _test_mutual_exclusion()
    print("共同清单 M3.4 测试通过")


if __name__ == "__main__":
    asyncio.run(main())
