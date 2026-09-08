# -*- coding: utf-8 -*-
"""P1-03 离散情绪回归：确定性消退、并发上限、修复一次性、行为锚点与 P1-01 闭环。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_emotion_"))

BASE = datetime(2026, 9, 7, 20, 0, 0)


def test_decay_deterministic_and_read_projection() -> int:
    from backend.core.emotion_state import EmotionItem, advance_emotions

    items = [EmotionItem(emotion="anger", intensity=0.8, updated_at=BASE.isoformat())]
    # anger 半衰期 4h：4 小时后 0.4，8 小时后 0.2
    one_step = advance_emotions(items, BASE + timedelta(hours=4))
    assert abs(one_step[0].intensity - 0.4) < 0.01, one_step[0].intensity
    two_step = advance_emotions(advance_emotions(items, BASE + timedelta(hours=2)),
                                BASE + timedelta(hours=4))
    assert abs(two_step[0].intensity - 0.4) < 0.015, "一次 4h 与两次 2h 应近似同值"
    # 纯函数：重复推进同一快照不改变原列表（读取不扣减）
    assert items[0].intensity == 0.8
    # 低于 0.05 移除：0.8 * 2^(-h/4) < 0.05 → h ≈ 17.2h
    gone = advance_emotions(items, BASE + timedelta(hours=18))
    assert gone == []
    print("[OK] 指数消退确定性（两步≈一步、阈值移除、读取纯函数）")
    return 0


def test_apply_merge_and_cap() -> int:
    from backend.core.emotion_state import (apply_emotion, load_emotions)
    from backend.core.userdb import db

    uid = "emotion-cap"
    db.ensure_user(uid)
    apply_emotion(uid, "joy", 0.6, now=BASE)
    apply_emotion(uid, "tenderness", 0.7, now=BASE)
    apply_emotion(uid, "hurt", 0.9, now=BASE)
    items = apply_emotion(uid, "anxiety", 0.3, now=BASE)  # 第 4 条最弱，应被挤出
    names = [i.emotion for i in items]
    assert len(items) == 3 and "anxiety" not in names, names
    assert names == ["hurt", "tenderness", "joy"], "按强度稳定排序"
    # 同情绪重复入账：取较大强度并刷新时间锚，不叠加超过 1
    merged = apply_emotion(uid, "hurt", 0.5, now=BASE)
    assert max(i.intensity for i in merged if i.emotion == "hurt") == 0.9
    # calm 是无主态回退，不入列
    assert apply_emotion(uid, "calm", 0.9, now=BASE) == load_emotions(uid, now=BASE)
    print("[OK] 并发上限 3 / 稳定排序 / 合并不叠加 / calm 拒绝")
    return 0


def test_repair_once_per_source() -> int:
    from backend.core.emotion_state import apply_emotion, apply_repair, load_emotions
    from backend.core.userdb import db

    uid = "emotion-repair"
    db.ensure_user(uid)
    apply_emotion(uid, "hurt", 0.8, cause_type="message", cause_id="m1", now=BASE)
    after1 = apply_repair(uid, cause_type="message", cause_id="m1", now=BASE)
    hurt1 = next(i.intensity for i in after1 if i.emotion == "hurt")
    assert abs(hurt1 - 0.6) < 0.01, hurt1
    # 同一来源第二次修复无效
    after2 = apply_repair(uid, cause_type="message", cause_id="m1", now=BASE)
    hurt2 = next(i.intensity for i in after2 if i.emotion == "hurt")
    assert abs(hurt2 - 0.6) < 0.01, "每来源只修复一次"
    # 不同来源各一次
    after3 = apply_repair(uid, cause_type="message", cause_id="m2", now=BASE)
    hurt3 = next(i.intensity for i in after3 if i.emotion == "hurt")
    assert abs(hurt3 - 0.4) < 0.01, hurt3
    assert load_emotions(uid, now=BASE) == after3, "读取与写入后一致（无重复扣减）"
    print("[OK] 修复 -0.2 / 每来源一次性")
    return 0


def test_attitude_summary_boundaries() -> int:
    from backend.core.emotion_state import attitude_summary

    # 高信任降 guard（≤0.2），深夜/低精力只降 followup
    base = attitude_summary({"anger": 1.0}, trust=25, intimacy=25)
    high_trust = attitude_summary({"anger": 1.0}, trust=85, intimacy=25)
    assert base["guard"] - high_trust["guard"] <= 0.2 + 1e-9
    assert base["guard"] > high_trust["guard"]
    late = attitude_summary({"joy": 1.0}, trust=25, intimacy=25, low_energy_or_late=True)
    normal = attitude_summary({"joy": 1.0}, trust=25, intimacy=25)
    assert late["followup"] < normal["followup"] and late["patience"] == normal["patience"]
    # 全轴 clamp [0,1]；高亲密不升级 followup
    hi_int = attitude_summary({"tenderness": 1.0}, trust=25, intimacy=90, low_energy_or_late=True)
    assert all(0.0 <= v <= 1.0 for v in hi_int.values())
    assert hi_int["followup"] < 0.35, "低精力/深夜不得借亲密抬升追问"
    print("[OK] 态度摘要边界（trust/intimacy/深夜只降不升、全轴 clamp）")
    return 0


def test_behavior_anchor_and_compat() -> int:
    from backend.core.behavior import _emotion_line
    from backend.core.state import AgentState

    s = AgentState()
    assert _emotion_line(s) == "", "无离散情绪 → 行为帧完全走旧逻辑"
    assert _emotion_line(s, emotions=[]) == ""
    hard = _emotion_line(s, emotions=[{"emotion": "anger", "intensity": 0.7}])
    assert "硬" in hard and "不骂人" in hard
    soft = _emotion_line(s, emotions=[{"emotion": "tenderness", "intensity": 0.6}])
    assert "软" in soft
    both = _emotion_line(s, emotions=[{"emotion": "hurt", "intensity": 0.6},
                                      {"emotion": "tenderness", "intensity": 0.6}])
    assert "矛盾" in both, "关心与受伤并存时不抹掉矛盾感"
    for line in (hard, soft, both):
        assert "0." not in line and "强度" not in line, "运行时产物不得出现数值/术语"
    print("[OK] 行为锚点三场景 + 无情绪兼容 + 无数值泄露")
    return 0


def test_state_projection_and_persona_gate() -> int:
    from backend.core.emotion_state import apply_emotion
    from backend.core.persona_slices import build_state_view, compile_slices
    from backend.core.state import load_state
    from backend.core.userdb import db

    uid = "emotion-integration"
    db.ensure_user(uid)
    assert load_state(uid).discrete_emotions == [], "无情绪时派生快照为空"

    # P1-01 闭环：恋人 + trust 95 + hurt 0.6 → 深水区切片被真实状态激活
    # 集成门控读取使用真实当前时刻；不能把固定历史锚写入后再按墙钟衰减，
    # 否则测试会随执行日期从通过变失败。
    apply_emotion(uid, "hurt", 0.6, now=datetime.now())
    state = load_state(uid)
    assert state.discrete_emotions and state.discrete_emotions[0]["emotion"] == "hurt"
    emotions = {i["emotion"]: i["intensity"] for i in state.discrete_emotions}
    view = build_state_view(stage="恋人", affection=95, now=datetime(2026, 9, 7, 21, 0),
                            emotions=emotions)
    out = compile_slices(view, profile_id="default")
    assert "PS-DEEP-01" in out.dynamic_ids, out.dynamic_ids
    print("[OK] load_state 派生投影 + P1-01 情绪门控闭环（深水区按真实情绪激活）")
    return 0


def main() -> int:
    failed = (
        test_decay_deterministic_and_read_projection()
        + test_apply_merge_and_cap()
        + test_repair_once_per_source()
        + test_attitude_summary_boundaries()
        + test_behavior_anchor_and_compat()
        + test_state_projection_and_persona_gate()
    )
    if failed:
        print(f"\n=== P1-03 离散情绪：{failed} 项失败 ===")
        return 1
    print("\n=== P1-03 离散情绪：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
