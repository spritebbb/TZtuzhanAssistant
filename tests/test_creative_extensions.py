# -*- coding: utf-8 -*-
"""L02 世界观/角色共创 + 观察日志：subtype、结构化大纲、观察条目与产物。

验收锚点（docs/Zcode技术指导.md L02 + 调度文档批次 10）：
- 共创壳支持 story/world/character；world/character 有结构化大纲（propose→confirm，版本化）；
- 收尾产物按 subtype 为 co_story/co_world/co_character，重复完成不增版而是 version+1；
- 观察日志复用 activities(kind='observation')：observer/来源/置信度，无源如实标注；
- 完成产出 observation_log（只汇编真实条目）；取消清条目；
- 活动壳互斥；导出/恢复含观察条目。
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_l02_"))

from backend.core import cowriting, observations
from backend.core.userdb import db

NOW = datetime(2026, 9, 8, 21, 0)


def test_writing_subtypes_and_outline() -> int:
    uid = "l02-world"
    db.ensure_user(uid)
    detail = cowriting.start_writing(uid, "云上城", "一座漂在云上的城市", subtype="world")
    assert detail["subtype"] == "world" and detail["subtype_label"] == "世界观"
    draft = cowriting.propose_outline(uid, int(detail["id"]))
    assert draft["fields"] == ["locations", "rules", "timeline"]
    assert draft["version"] == 0
    saved = cowriting.confirm_outline(
        uid, int(detail["id"]),
        {"locations": ["钟楼", "浮桥"], "rules": ["不能向下看"], "timeline": []},
        expected_version=0,
    )
    assert saved["version"] == 1
    assert saved["draft"]["locations"] == ["钟楼", "浮桥"]
    # 版本不符拒绝（避免覆盖并发修改）
    try:
        cowriting.confirm_outline(uid, int(detail["id"]), {"rules": ["x"]}, expected_version=0)
        raise AssertionError("版本不符应拒绝")
    except cowriting.ActivityError:
        pass
    # 故事壳没有结构化大纲
    story = cowriting.start_writing(uid, "小故事")
    try:
        cowriting.propose_outline(uid, int(story["id"]))
        raise AssertionError("story 不应有大纲")
    except cowriting.ActivityError:
        pass
    print("[OK] 共创 subtype：world 大纲 propose→confirm 版本化；story 无大纲")
    return 0


def test_subtype_artifact_types() -> int:
    uid = "l02-artifact"
    db.ensure_user(uid)
    world = cowriting.start_writing(uid, "深渊志", "海沟之下", subtype="world")
    cowriting.add_user_turn(uid, int(world["id"]), "深渊的入口在一座灯塔下面。")
    cowriting.complete_writing(uid, int(world["id"]))
    row = db.conn.execute(
        "SELECT artifact_type, title FROM artifacts WHERE user_id=? AND source_id=?",
        (uid, int(world["id"]))).fetchone()
    assert row["artifact_type"] == "co_world", row["artifact_type"]
    assert "世界观" in row["title"]

    char = cowriting.start_writing(uid, "蕾拉", "", subtype="character")
    cowriting.add_user_turn(uid, int(char["id"]), "她说话总是先扬下巴。")
    cowriting.complete_writing(uid, int(char["id"]))
    row2 = db.conn.execute(
        "SELECT artifact_type FROM artifacts WHERE user_id=? AND source_id=?",
        (uid, int(char["id"]))).fetchone()
    assert row2["artifact_type"] == "co_character"
    print("[OK] 收尾产物按 subtype 落 co_world / co_character")
    return 0


def test_observation_log() -> int:
    uid = "l02-obs"
    db.ensure_user(uid)
    detail = observations.start_observation(uid, "楼下那棵树")
    aid = int(detail["id"])
    observations.add_entry(uid, aid, "今天叶子开始黄了", observer="user",
                           source_type="event", source_id=11)
    observations.add_entry(uid, aid, "她注意到有人每天在同一时间路过", observer="assistant",
                           confidence=0.6)
    detail = observations.get_observation(uid, aid)
    assert len(detail["entries"]) == 2
    assert detail["entries"][1]["observer"] == "assistant"
    assert detail["entries"][1]["confidence"] == 0.6
    done = observations.complete_observation(uid, aid)
    assert "今天叶子开始黄了" in done["log"]
    assert "来源：event#11" in done["log"]
    assert "无外部来源" in done["log"], "无源条目必须如实标注"
    row = db.conn.execute(
        "SELECT artifact_type, content FROM artifacts WHERE user_id=? AND source_id=?",
        (uid, aid)).fetchone()
    assert row["artifact_type"] == "observation_log"
    # 已结束不能再记
    try:
        observations.add_entry(uid, aid, "又看了一次")
        raise AssertionError("已结束应拒绝")
    except observations.ActivityError:
        pass
    # 取消清条目
    other = observations.start_observation(uid, "另一本日志")
    observations.add_entry(uid, int(other["id"]), "记一条")
    observations.cancel_observation(uid, int(other["id"]))
    assert observations.forget_for_activity(uid, int(other["id"])) == 1
    print("[OK] 观察日志：条目/来源/置信度、产物只汇编真实条目、取消清条目")
    return 0


def test_export_roundtrip() -> int:
    from backend.core.relationship_export import export_bundle, restore_bundle

    uid, target = "l02-export", "l02-import"
    db.ensure_user(uid)
    detail = observations.start_observation(uid, "导出用日志")
    observations.add_entry(uid, int(detail["id"]), "一条可导出的观察")
    bundle = export_bundle(uid, ["activities"])
    assert "observation_entries" in bundle["data"], list(bundle["data"].keys())
    restore_bundle(bundle, target)
    row = db.conn.execute(
        "SELECT content, user_id FROM observation_entries WHERE user_id=?", (target,)).fetchone()
    assert row and "一条可导出的观察" in row["content"]
    print("[OK] LC-1：观察条目随 activities 类别导出→恢复")
    return 0


def main() -> int:
    failed = (
        test_writing_subtypes_and_outline()
        + test_subtype_artifact_types()
        + test_observation_log()
        + test_export_roundtrip()
    )
    if failed:
        print(f"\n=== L02 世界观/角色共创 + 观察日志：{failed} 项失败 ===")
        return 1
    print("\n=== L02 世界观/角色共创 + 观察日志：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
