# -*- coding: utf-8 -*-
"""B5 回复解释快照：状态、行为帧、记忆去重与长度边界。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.behavior import build_behavior_frame
from backend.core.explainability import build_reply_explanation
from backend.core.state import AgentState


def main() -> None:
    state = AgentState(emotion=72, energy=31, affection=58, stage="亲密")
    frame = build_behavior_frame(state)
    snapshot = build_reply_explanation(
        state,
        frame,
        memory_rows=[
            ("长期事实", "用户喜欢雨天"),
            ("长期事实", "用户喜欢雨天"),
            ("相关对话", "很久以前" * 80),
        ],
        search_used=True,
        media="sticker",
    )
    assert snapshot["version"] == 1
    assert snapshot["state"] == {
        "affection": 58, "stage": "亲密", "mood": 72, "mood_label": "开心", "energy": 31,
        "resting": False, "rest_until": None, "tension": 0,
    }
    assert snapshot["behavior"] and snapshot["behavior"][0]["label"] == "情绪与精力"
    assert len(snapshot["memories"]) == 2, "重复记忆应去重"
    assert len(snapshot["memories"][1]["text"]) <= 180
    assert snapshot["tools"] == {"search": True, "media": "sticker"}
    print("[OK] B5 解释快照状态、行为、记忆与工具边界")


if __name__ == "__main__":
    main()
