# -*- coding: utf-8 -*-
"""C1 可交互精力与关系修复：休息计时、冲突持久化和分级哄好。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core import state
from backend.core.behavior import build_behavior_frame
from backend.core.userdb import db, kv_del


def main() -> None:
    uid = "test_c1_state_interaction"
    db.ensure_user(uid)
    kv_del(uid, state._REST_KEY)
    kv_del(uid, state._TENSION_KEY)

    assert state.is_rest_request("你去睡会儿吧")
    assert state.is_rest_request("去休息一下吧")
    assert not state.is_rest_request("我先去睡了")
    assert not state.is_rest_request("我今天没休息好")

    now = datetime(2026, 9, 4, 12, 0, 0)
    rest = state.begin_rest(uid, minutes=90, now=now)
    base = 30
    start_energy, active, _ = state._rest_snapshot(uid, base, now)
    mid_energy, mid_active, _ = state._rest_snapshot(uid, base, now + timedelta(minutes=45))
    end_energy, end_active, _ = state._rest_snapshot(uid, base, now + timedelta(minutes=90))
    assert active and mid_active and not end_active
    assert start_energy >= base and mid_energy > start_energy and end_energy >= int(rest["target"]) - 1

    db.set_mood(uid, 80)
    hurt = state.apply_impulse(
        uid,
        emotion_delta=-10,
        affection_delta=-4,
        emotional_hit="被冒犯了",
        emotional_weight=0.9,
        text="你真讨厌",
    )
    initial_tension = hurt.tension
    assert initial_tension >= 45
    assert hurt.emotion <= 34, "未修复冲突应限制外显心情，不能自然秒好"
    assert "未修复" in build_behavior_frame(hurt).tension_line

    state.apply_impulse(uid, emotion_delta=8, emotional_hit="被夸了", emotional_weight=0.7)
    assert state.load_state(uid).tension == initial_tension, "普通正向消息不能直接抹掉冲突"

    gentle = state.repair_tension(uid, "别生气啦，给你抱抱")
    assert 0 < gentle["level"] < initial_tension
    assert gentle["last_repair"] == "温柔安抚"

    sincere = state.repair_tension(uid, "对不起，是我的错，我以后不会再这样")
    assert sincere["level"] == 0
    repaired = state.load_state(uid)
    assert "认真道歉并承担责任" in build_behavior_frame(repaired).tension_line

    state.handle_state_interaction(uid, "我们聊点别的")
    assert not build_behavior_frame(state.load_state(uid)).tension_line

    kv_del(uid, state._REST_KEY)
    kv_del(uid, state._TENSION_KEY)
    print("[OK] C1 休息真实回能、冲突持久化与分级修复")


if __name__ == "__main__":
    main()
