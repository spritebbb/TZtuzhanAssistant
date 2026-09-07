# -*- coding: utf-8 -*-
"""P3-01 全情绪 × 信任/亲密态度矩阵回归。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.behavior import _emotion_line
from backend.core.emotion_state import (
    ATTITUDE_AXES,
    ATTITUDE_VECTORS,
    attitude_instruction,
    attitude_summary,
)
from backend.core.state import AgentState


def test_all_emotions_have_bounded_vectors() -> None:
    assert set(ATTITUDE_VECTORS) == {
        "calm", "anger", "hurt", "joy", "sadness", "anxiety", "tenderness",
    }
    for name, vector in ATTITUDE_VECTORS.items():
        assert len(vector) == len(ATTITUDE_AXES), name
        assert all(0.0 <= value <= 1.0 for value in vector), name
        summary = attitude_summary({name: 1.0})
        assert set(summary) == set(ATTITUDE_AXES)
        assert all(0.0 <= value <= 1.0 for value in summary.values())


def test_relationship_dimensions_are_independent() -> None:
    emotion = {"tenderness": 0.7, "anxiety": 0.3}
    trusted = attitude_summary(emotion, trust=90, intimacy=20)
    intimate = attitude_summary(emotion, trust=20, intimacy=90)
    assert trusted["guard"] < attitude_summary(emotion, trust=20, intimacy=20)["guard"]
    assert intimate["followup"] > trusted["followup"]

    line = attitude_instruction(emotion, trust=90, intimacy=20)
    assert "当前关系分寸" in line and "越级昵称" in line
    assert "亲爱的" not in line and "恋人" not in line


def test_conflicting_emotions_and_energy_boundary() -> None:
    state = AgentState(
        affection=70, trust=75, intimacy=70, energy=80,
        discrete_emotions=[
            {"emotion": "hurt", "intensity": 0.7},
            {"emotion": "tenderness", "intensity": 0.6},
        ],
    )
    line = _emotion_line(state, low_energy_or_late=False)
    assert "矛盾" in line and "关心" in line and "菟菚" in line
    assert "patience" not in line and "guard" not in line and "0." not in line

    awake = attitude_summary({"joy": 1.0}, trust=80, intimacy=80)
    tired = attitude_summary(
        {"joy": 1.0}, trust=80, intimacy=80, low_energy_or_late=True,
    )
    assert tired["followup"] < awake["followup"]
    for axis in set(ATTITUDE_AXES) - {"followup"}:
        assert tired[axis] == awake[axis]


def test_state_projection_and_thirty_turn_stability() -> None:
    state = AgentState(
        affection=55, trust=85, intimacy=35, energy=25,
        discrete_emotions=[{"emotion": "anger", "intensity": 0.6}],
    )
    assert state.attitude_axes == attitude_summary(
        {"anger": 0.6}, trust=85, intimacy=35, low_energy_or_late=True,
    )
    outputs = {
        _emotion_line(state, low_energy_or_late=True)
        for _ in range(30)
    }
    assert len(outputs) == 1, "相同状态跨 30 轮不得随机漂移或累积永久偏移"
    output = outputs.pop()
    assert "不骂人" in output and "不要为了续聊硬追问" in output
    assert "越级昵称" in output and "菟菚" in output


def main() -> None:
    test_all_emotions_have_bounded_vectors()
    test_relationship_dimensions_are_independent()
    test_conflicting_emotions_and_energy_boundary()
    test_state_projection_and_thirty_turn_stability()
    print("[OK] P3-01 全情绪矩阵、双维修正、矛盾并存、边界与 30 轮稳定性")


if __name__ == "__main__":
    main()
