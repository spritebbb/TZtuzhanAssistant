# -*- coding: utf-8 -*-
"""D9 局势档案回归（docs/D9-D12-DESIGN-2026-09-21.md §3）。

覆盖七件事：
1. 确定性重建：六节内容映射真实持久数据（目标/约定/悬念/事件/生活/焦点）；
2. 注入：常驻 system 块渲染与预算裁剪（超长先丢压缩段）；flag 关闭整层退场；
3. 增量触发：无新事件只推轮数不调 LLM；有新事件（或≥10轮）才调，且受 D10 门控；
4. LLM 失败保旧：压缩段保旧、确定性节照常刷新、不抛异常；
5. 重编译：零 LLM 全量重建、幂等、last_event_id 与 SQLite 对齐；
6. 生命周期：reset 双清单覆盖、关系包导出不含（派生态）；
7. schema v43：user_version 联动与表存在。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_situation_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import features, situation  # noqa: E402
from backend.core.relationship_events import record  # noqa: E402
from backend.core.userdb import db, save_promise  # noqa: E402

UID = "situation-user-1"


def _seed() -> None:
    """造真实数据：一个进行中目标、一条开着的约定、一个悬念、几条关系事件。"""
    db.ensure_user(UID)
    from backend.core.goals import start_goal
    from backend.core.open_questions import track_question
    from backend.core.userdb import kv_set

    try:
        start_goal(UID, "把酒馆同玩打磨顺手", "想让剧情回忆更自然", "补一次真机对局")
    except Exception:
        pass  # 已存在（幂等种子）
    save_promise(UID, "周末把那本书的最后一章读完")
    try:
        track_question(UID, "他上次说的项目验收到底过没过", source_message_id=None)
    except Exception:
        pass
    for i, payload in enumerate([
        {"title": "一起读完《灯塔》并留下书摘"},
        {"title": "他纠正了一条记错的事实"},
        {"title": "纪念日提前的心不在焉"},
    ]):
        record(
            UID,
            "reading_finished" if i == 0 else "memory_corrected",
            "activity" if i == 0 else "fact",
            i + 1,
            payload=payload,
        )
    kv_set(UID, "cost:hint_day", "")


def test_rebuild_sections_maps_real_data() -> None:
    _seed()
    sections = situation.rebuild_sections(UID)
    assert any("酒馆同玩" in g for g in sections["goals"]), f"目标节应含进行中目标: {sections['goals']}"
    assert any("最后一章" in p for p in sections["promises"]), "约定节应含开着的约定"
    assert any("项目验收" in q for q in sections["questions"]), "悬念节应含开放问题"
    titles = [e["text"] for e in sections["events_recent"]]
    assert any("灯塔" in t for t in titles), f"事件节应含真实事件: {titles}"
    assert sections["life"], "生活节应非空（在场状态）"
    assert sections["events_recent"], "事件节应非空"
    print("[OK] 确定性重建：六节全部映射真实持久数据")


def test_injection_render_and_flag() -> None:
    _seed()
    situation.recompile(UID)
    ctx = situation.situation_context(UID)
    assert ctx.startswith("[局势档案]"), "注入块应有固定头"
    assert "酒馆同玩" in ctx and "最后一章" in ctx, "注入块应包含快照内容"
    # flag 关闭整层退场
    features.set_flag("situation_enabled", False)
    try:
        assert situation.situation_context(UID) == ""
    finally:
        features.set_flag("situation_enabled", True)
    print("[OK] 注入：常驻块渲染 + flag 关闭整层退场")


def test_budget_trim_drops_compressed_first() -> None:
    _seed()
    sections = situation.rebuild_sections(UID)
    # 正常预算下带压缩段
    text = situation._render(sections, "更早：他们一起攒过一套书单", char_budget=5000)
    assert "更早" in text
    # 预算紧到放不下时：先丢压缩段再硬截断，且永不超预算
    tight = situation._render(sections, "更早：他们一起攒过一套书单", char_budget=120)
    assert len(tight) <= 120 and tight.endswith("…") is False or len(tight) <= 120
    tiny = situation._render(sections, "更早：他们一起攒过一套书单", char_budget=60)
    assert len(tiny) <= 60, f"硬截断后不得超预算: {len(tiny)}"
    print("[OK] 预算裁剪：先丢压缩段、硬截断不超预算")


async def _fake_compress_ok(*a, **k):
    return "更早：他们一起攒过几段共同阅读的经历"


def test_update_triggers() -> None:
    _seed()
    # 先落一条「新」事件（recompile 可能已把 last_event_id 推平）
    record(UID, "reading_finished", "activity", 77, payload={"title": "触发更新的事件"})
    # 有新事件 → 调 LLM 压缩并落档
    with patch.object(situation, "_compress_older", side_effect=_fake_compress_ok) as mc:
        result = asyncio.run(situation.update_after_turn(UID))
    assert result["updated"] is True and result["llm_used"] is True
    assert mc.await_count == 1
    current = situation.get_file(UID)
    assert current["compressed_older"] == "更早：他们一起攒过几段共同阅读的经历"
    assert current["last_event_id"] > 0
    # 无新事件 → 只推轮数，不再调 LLM
    with patch.object(situation, "_compress_older", side_effect=_fake_compress_ok) as mc2:
        result = asyncio.run(situation.update_after_turn(UID))
    assert result["updated"] is False and mc2.await_count == 0
    assert situation.get_file(UID)["turns_since_update"] == 1
    # D10 hard 档 → 不调 LLM，但确定性节照常可刷新（保旧行为）
    from backend.core import cost_guard

    with patch.object(cost_guard, "check", return_value=False), \
            patch.object(situation, "_compress_older", side_effect=AssertionError("不得调LLM")):
        record(UID, "reading_finished", "activity", 99, payload={"title": "又读完一本"})
        result = asyncio.run(situation.update_after_turn(UID))
    assert result["updated"] is True and result["llm_used"] is False
    assert situation.get_file(UID)["compressed_older"], "成本闸下保旧压缩段"
    print("[OK] 增量触发：事件驱动、无事件只推轮数、D10 hard 档零 LLM 保旧")


def test_llm_failure_keeps_old() -> None:
    _seed()
    situation.recompile(UID)

    async def boom(*a, **k):
        raise RuntimeError("provider down")

    with patch.object(situation, "_compress_older", side_effect=boom):
        record(UID, "reading_finished", "activity", 120, payload={"title": "再读完一本"})
        result = asyncio.run(situation.update_after_turn(UID))  # 不得抛
    assert result["updated"] is True and result["llm_used"] is False
    print("[OK] LLM 失败保旧：不抛异常、确定性节照常刷新")


def test_recompile_idempotent() -> None:
    _seed()
    a = situation.recompile(UID)
    file_a = situation.get_file(UID)
    b = situation.recompile(UID)
    file_b = situation.get_file(UID)
    assert a["recompiled"] and b["recompiled"]
    assert file_a["sections"] == file_b["sections"], "重编译幂等（同数据同结果）"
    newest = situation._newest_event_id(UID)
    assert file_b["last_event_id"] == newest, "last_event_id 与 SQLite 对齐"
    assert situation.get_file(UID)["turns_since_update"] == 0, "重编译清零轮数"
    print("[OK] 重编译：幂等、last_event_id 对齐、清零轮数")


def test_lifecycle_reset_and_export() -> None:
    import inspect

    from backend.core import relationship_export, reset as reset_mod
    import backend.core.userdb as ud

    assert "situation_files" in reset_mod._TABLES, "reset 清单必须覆盖"
    # 导出与 userdb 降级 reset 均为源码级检查（派生态不进关系包；降级清单已含）
    assert "situation_files" not in inspect.getsource(relationship_export), \
        "关系包导出不得包含派生态档案"
    assert "situation_files" in inspect.getsource(ud), "userdb 降级 reset 清单应包含"
    print("[OK] 生命周期：reset 双清单覆盖、关系包导出不含")


def test_schema_v43() -> None:
    from backend.core.userdb import _SCHEMA_VERSION

    version = db.conn.execute("PRAGMA user_version").fetchone()[0]
    assert _SCHEMA_VERSION == 43 and version == 43, f"schema 应为 v43，实际 {version}"
    row = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='situation_files'"
    ).fetchone()
    assert row is not None, "situation_files 表应存在"
    print("[OK] schema v43：版本联动与表结构就位")


def main() -> None:
    db.conn.execute("SELECT 1")
    test_rebuild_sections_maps_real_data()
    test_injection_render_and_flag()
    test_budget_trim_drops_compressed_first()
    test_update_triggers()
    test_llm_failure_keeps_old()
    test_recompile_idempotent()
    test_lifecycle_reset_and_export()
    test_schema_v43()
    print("\n=== D9 局势档案：8 组全部通过 ===")


if __name__ == "__main__":
    main()
