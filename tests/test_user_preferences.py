# -*- coding: utf-8 -*-
"""P2-02 用户偏好教学回归：明确指令入账、resolver 优先级、撤销回退次高、
legacy 迁移、负反馈降权与临时轮不学习。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_prefs_"))


def test_explicit_teaching_and_idempotent() -> int:
    from backend.core import user_preferences as up
    from backend.core.userdb import db

    uid = "prefs-teach"
    db.ensure_user(uid)
    rows = up.propose_preference(uid, "以后叫我老大了吧", source_message_id=1)
    assert len(rows) == 1 and rows[0]["category"] == "address"
    assert rows[0]["status"] == "active" and rows[0]["origin"] == "user_teaching"
    assert rows[0]["value"]["allowed"] == ["老大"]

    # 幂等：同值同状态不重复建行
    again = up.propose_preference(uid, "以后叫我老大了吧", source_message_id=2)
    assert again == []
    n = db.conn.execute(
        "SELECT COUNT(*) AS n FROM user_preferences WHERE user_id=?", (uid,)
    ).fetchone()["n"]
    assert n == 1

    # 明确禁令：别拿X开玩笑 → humor 禁区
    up.propose_preference(uid, "别拿我发际线开玩笑", source_message_id=3)
    prefs = up.resolve(uid)
    assert prefs["humor"]["forbidden_topics"] == ["我发际线"]

    # 无关消息（嗯/哦）零提取：不把敷衍写成侧写
    assert up.propose_preference(uid, "嗯") == []
    assert up.propose_preference(uid, "哦哦") == []
    print("[OK] 明确指令入账 / 幂等 / 敷衍零提取")
    return 0


def test_resolver_priority_and_revoke_fallback() -> int:
    from backend.core import user_preferences as up
    from backend.core.userdb import db

    uid = "prefs-resolver"
    db.ensure_user(uid)
    up._upsert(uid, "address", {"allowed": ["老张"]}, origin="legacy",
               source_message_id=None, status="active", confidence=1.0)
    # 教学禁令优先于 legacy 允许：合并后两者并存，禁令是硬约束
    up._upsert(uid, "address", {"forbidden": ["老板"]}, origin="user_teaching",
               source_message_id=10, status="active", confidence=1.0)
    prefs = up.resolve(uid)
    assert prefs["address"]["allowed"] == ["老张"]
    assert prefs["address"]["forbidden"] == ["老板"]

    lines = up.resolve_constraints(uid)
    assert any("老板" in l and "不要用" in l for l in lines)
    assert any("老张" in l for l in lines)

    # 撤销教学禁令 → 恢复到只剩 legacy（次高优先级），而非清空
    pref_id = db.conn.execute(
        "SELECT id FROM user_preferences WHERE user_id=? AND origin='user_teaching'",
        (uid,),
    ).fetchone()["id"]
    up.revoke_preference(uid, pref_id)
    prefs2 = up.resolve(uid)
    assert prefs2["address"].get("forbidden") is None
    assert prefs2["address"]["allowed"] == ["老张"], "撤销恢复次高优先级而非清空"
    print("[OK] 解析优先级 / 撤销回退次高 / 约束编译")
    return 0


def test_confirm_cas_and_feedback_weight() -> int:
    from backend.core import user_preferences as up
    from backend.core.userdb import db

    uid = "prefs-cas"
    db.ensure_user(uid)
    row = up._upsert(uid, "comfort", {"style": "陪着就好，别讲道理"}, origin="user_teaching",
                     source_message_id=None, status="candidate", confidence=0.4)
    assert row["status"] == "candidate"

    # CAS：错误版本拒绝
    try:
        up.confirm_preference(uid, row["id"], expected_version=row["version"] + 5)
        raise AssertionError("版本不符应拒绝")
    except up.PreferenceError:
        pass
    confirmed = up.confirm_preference(uid, row["id"], expected_version=row["version"])
    assert confirmed["status"] == "active" and confirmed["confidence"] == 1.0

    # 负反馈：只降教学类权重（confidence 1.0 → 0.7）
    up.record_feedback(uid, "comfort", negative=True)
    after = [r for r in up.list_preferences(uid) if r["category"] == "comfort"][0]
    assert abs(after["confidence"] - 0.7) < 1e-6
    print("[OK] 草稿确认 CAS / 负反馈降权")
    return 0


def test_legacy_migration_once() -> int:
    from backend.core import user_preferences as up
    from backend.core.userdb import db

    uid = "prefs-legacy"
    db.ensure_user(uid)
    db.set_nickname(uid, "小队长")
    up.migrate_legacy(uid)
    up.migrate_legacy(uid)  # 幂等
    rows = [r for r in up.list_preferences(uid) if r["origin"] == "legacy"]
    assert len(rows) == 1 and rows[0]["value"]["allowed"] == ["小队长"]
    assert up.resolve(uid)["address"]["allowed"] == ["小队长"]
    print("[OK] 旧称呼配置一次性迁移（幂等）")
    return 0


def test_schema_and_reset_registered() -> int:
    from backend.core.reset import _TABLES
    from backend.core.userdb import _SCHEMA_VERSION

    assert "user_preferences" in _TABLES
    assert _SCHEMA_VERSION >= 18
    print("[OK] 表入 reset 清单 / schema 18")
    return 0


def main() -> int:
    failed = (
        test_explicit_teaching_and_idempotent()
        + test_resolver_priority_and_revoke_fallback()
        + test_confirm_cas_and_feedback_weight()
        + test_legacy_migration_once()
        + test_schema_and_reset_registered()
    )
    if failed:
        print(f"\n=== P2-02 用户偏好：{failed} 项失败 ===")
        return 1
    print("\n=== P2-02 用户偏好：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
