# -*- coding: utf-8 -*-
"""P1-05 日历调制回归：阶段幂等、预热/当天/结束、并发优先级、自然季节
分 kind、正典播种、跨年闰日与删除失效。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_calendar_"))


def test_phase_stages_and_boundaries() -> int:
    from backend.core.calendar_modulation import active_phases
    from backend.core.userdb import db, save_important_date

    uid = "cal-stages"
    db.ensure_user(uid)
    save_important_date(uid, "12-25", "平安夜", "anniversary")

    far = active_phases(uid, date(2026, 12, 20))   # -5 天：窗口外
    assert not [p for p in far if p.stage != "post" or True] or all(
        p.phase_id.endswith(":post") is False for p in far), far
    assert far == [], far

    pre = active_phases(uid, date(2026, 12, 22))    # -3 天：预热
    assert len(pre) == 1 and pre[0].stage == "pre" and pre[0].mood_offset == 0

    day = active_phases(uid, date(2026, 12, 25))    # 当天
    assert len(day) == 1 and day[0].stage == "day"
    assert day[0].mood_offset == 5 and day[0].priority_offset == 0.1

    post = active_phases(uid, date(2026, 12, 26))   # +1 天：结束标记（零调制）
    assert len(post) == 1 and post[0].stage == "post" and post[0].tone_hint == ""
    assert post[0].mood_offset == 0, "结束后必须恢复基线（不累积偏移）"

    # 跨年：12-25 在 2027 年产生新 phase_id（年份入键，幂等按年）
    next_year = active_phases(uid, date(2027, 12, 25))
    assert len(next_year) == 1 and ":2027:" in next_year[0].phase_id
    print("[OK] 预热[-3,-1]/当天/结束恢复/跨年阶段键")
    return 0


def test_leap_day_and_invalid() -> int:
    from backend.core.calendar_modulation import active_phases, natural_season_of
    from backend.core.userdb import db, save_important_date

    uid = "cal-leap"
    db.ensure_user(uid)
    save_important_date(uid, "02-29", "四年一次", "anniversary")
    # 平年 2027：2-29 不存在 → 全年不触发、不崩
    assert active_phases(uid, date(2027, 2, 28)) == []
    assert active_phases(uid, date(2027, 3, 1)) == []
    # 闰年 2028 正常触发
    hit = active_phases(uid, date(2028, 2, 29))
    assert len(hit) == 1 and hit[0].stage == "day"
    # 自然季节：3-5 春 / 6-8 夏 / 9-11 秋 / 12-2 冬
    assert natural_season_of(date(2026, 9, 7)) == "autumn"
    assert natural_season_of(date(2026, 1, 1)) == "winter"
    print("[OK] 闰日边界 + 自然季节分档")
    return 0


def test_priority_boundary_first() -> int:
    from backend.core.calendar_modulation import effective_modulation
    from backend.core.userdb import db, save_important_date

    uid = "cal-priority"
    db.ensure_user(uid)
    save_important_date(uid, "09-10", "认识的日子", "anniversary")
    today = date(2026, 9, 10)

    normal = effective_modulation(uid, today, energy=80, tension=0)
    assert normal.kind == "calendar_day" and normal.mood_offset == 5
    assert "特殊日子" in normal.tone_hint

    # 冲突边界优先：语气与数值调制让位，只保留话题池
    conflict = effective_modulation(uid, today, energy=80, tension=40)
    assert conflict.mood_offset == 0 and conflict.tone_hint == ""
    assert conflict.topic_hint == "认识的日子", "冲突时话题池保留但不装气氛"

    # 勿扰：主动候选不加成
    quiet = effective_modulation(uid, today, energy=80, tension=0, quiet=True)
    assert quiet.priority_offset == 0.0

    # 低精力：幅度减半，语气改为收敛
    tired = effective_modulation(uid, today, energy=20, tension=0)
    assert tired.mood_offset == 2 and "没力气" in tired.tone_hint
    print("[OK] 并发确定性优先级（冲突>勿扰>低精力>日历）")
    return 0


def test_canon_seed_and_delete_invalidates() -> int:
    from backend.core.calendar_modulation import (active_phases, advance_calendar,
                                                  effective_modulation)
    from backend.core.userdb import db

    uid = "cal-canon"
    db.ensure_user(uid)
    # 播种：仅 default 人格；醒来日 8-27 为 character_fiction
    advance_calendar(uid, date(2026, 9, 7), persona_id="default")
    rows = db.conn.execute(
        "SELECT * FROM important_dates WHERE user_id=? AND namespace='character_fiction'",
        (uid,),
    ).fetchall()
    assert len(rows) == 1 and rows[0]["date"] == "08-27" and rows[0]["kind"] == "anniversary"

    # 非默认人格不播种
    uid2 = "cal-canon::persona::other"
    db.ensure_user(uid2)
    advance_calendar(uid2, date(2026, 9, 7), persona_id="other-persona")
    n2 = db.conn.execute(
        "SELECT COUNT(*) AS n FROM important_dates WHERE user_id=? AND namespace='character_fiction'",
        (uid2,),
    ).fetchone()["n"]
    assert n2 == 0

    # 角色纪念日当天：自嘲式轻提口径（不索要庆祝）
    awakening = effective_modulation(uid, date(2026, 8, 27), energy=80, tension=0)
    assert "自嘲" in awakening.tone_hint and "不索要庆祝" in awakening.tone_hint

    # 删除纪念日：阶段实时消失（无残留实例）
    db.conn.execute("DELETE FROM important_dates WHERE user_id=?", (uid,))
    db.conn.commit()
    assert active_phases(uid, date(2026, 8, 27)) == []
    print("[OK] 正典播种（人格隔离/口径）+ 删除即失效")
    return 0


def test_advance_idempotent_and_bounded() -> int:
    import json

    from backend.core.calendar_modulation import advance_calendar
    from backend.core.userdb import db, kv_get

    uid = "cal-idem"
    db.ensure_user(uid)
    day = date(2026, 8, 27)
    first = advance_calendar(uid, day)
    assert first["fresh_phases"], first
    second = advance_calendar(uid, day)
    assert second["fresh_phases"] == [], "重复 tick 必须幂等（无新候选）"
    assert second["active"] == first["active"]

    # kv 有界：只保留近 8 天
    state = json.loads(kv_get(uid, "state:calendar"))
    assert all(k >= (day - __import__("datetime").timedelta(days=8)).isoformat()
               for k in state["days"]), state["days"].keys()
    print("[OK] advance 幂等 + 状态有界（近 8 天）")
    return 0


def test_frame_line_and_no_numbers() -> int:
    from backend.core.calendar_modulation import compose_line, effective_modulation
    from backend.core.userdb import db, save_important_date

    uid = "cal-line"
    db.ensure_user(uid)
    save_important_date(uid, "09-07", "纪念日", "anniversary")
    mod = effective_modulation(uid, date(2026, 9, 7), energy=80, tension=0)
    line = compose_line(mod)
    assert line and line.endswith("。")
    assert not any(ch.isdigit() for ch in line), "运行时产物不得出现数值"
    assert compose_line(None) == ""
    print("[OK] 行为帧行渲染（无数值泄露）")
    return 0


def main() -> int:
    failed = (
        test_phase_stages_and_boundaries()
        + test_leap_day_and_invalid()
        + test_priority_boundary_first()
        + test_canon_seed_and_delete_invalidates()
        + test_advance_idempotent_and_bounded()
        + test_frame_line_and_no_numbers()
    )
    if failed:
        print(f"\n=== P1-05 日历调制：{failed} 项失败 ===")
        return 1
    print("\n=== P1-05 日历调制：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
