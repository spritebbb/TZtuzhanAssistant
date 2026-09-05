# -*- coding: utf-8 -*-
"""M4 双向关系与修复：冲突→修复多轮确定性回归、初识不越界、迎合防线、偏好可查看/重置。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_m4_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core.her_profile import her_profile  # noqa: E402
from backend.core.state import (  # noqa: E402
    apply_impulse,
    handle_state_interaction,
    load_state,
)
from backend.core.userdb import db  # noqa: E402

UID = "assistant-main"


def _tension() -> int:
    return int(load_state(UID).tension or 0)


def _test_conflict_repair_multi_turn() -> None:
    # 初识用户：好感度压低
    db.set_affection_absolute(UID, 12)

    # 第一轮：冒犯 → 张力出现（来自真实互动，非随机生成）
    apply_impulse(
        UID, emotion_delta=-15, affection_delta=-8,
        emotional_hit="被冒犯了", emotional_weight=1.2, text="你真没用", reply="……",
    )
    t1 = _tension()
    assert t1 > 0, "真实冒犯必须形成关系张力"
    assert load_state(UID).stage == "初识", "初识阶段不因冲突或修复越级"

    # 第二轮：只说软话不够——普通闲聊不清零
    handle_state_interaction(UID, "别生气了嘛")
    assert _tension() > 0, "一句软话不该瞬间清零张力"

    # 第三轮：真诚道歉+承担责任 → 明显下降
    handle_state_interaction(UID, "对不起，是我的错，我不会再这样了")
    t3 = _tension()
    assert t3 < t1, "认真修复必须降低张力"

    # 第四轮：修复到零为止，不会变成负数
    handle_state_interaction(UID, "真的很抱歉，我会改，下次我会先听你说")
    handle_state_interaction(UID, "对不起，我错了")
    assert _tension() == 0

    # 上限：连续多次冒犯封顶 100
    for _ in range(8):
        apply_impulse(
            UID, emotion_delta=-5, affection_delta=-5,
            emotional_hit="被冷落了", emotional_weight=1.5, text="……", reply="……",
        )
    assert _tension() == 100
    print("[OK] 冲突→修复多轮：张力有来源/有上限/可重复修复，初识不越级")


def _test_stage_frame_and_guard() -> None:
    # 初识行为帧保持距离感，不因季节/张力注入暧昧
    from backend.core.behavior import build_behavior_frame

    db.set_affection_absolute(UID, 12)
    state = load_state(UID)
    frame = build_behavior_frame(state)
    assert "疏离" in frame.stage_line or "距离" in frame.stage_line

    # 迎合防线进入 system prompt（观点连续性）
    from backend.core.persona import build_system_prompt

    prompt = build_system_prompt(
        stage="初识", address="你", lover_confirm=False,
        first_chat=False, affection=12, user_id=UID,
    )
    assert "不要为了迎合对方而瞬间推翻" in prompt
    assert "不要把你的偏好说成对方的客观事实" in prompt
    print("[OK] 初识行为帧不越界；迎合防线已注入（可友好分歧、不瞬间推翻）")


def _test_profile_and_style_api() -> None:
    from fastapi.testclient import TestClient

    from backend.app import create_app

    with TestClient(create_app()) as client:
        # 双向了解：她的侧面来自人格卡，结构稳定
        sections = client.get("/api/memory/her-profile").json()["sections"]
        keys = {item["key"] for item in sections}
        assert {"traits", "likes", "dislikes", "landmines", "stances"} <= keys
        assert all(item["items"] for item in sections)

        # 互动偏好：可查看
        db.set_style(UID, "喜欢短句、偶尔用省略号")
        db.add_term(UID, "菟丝子", "slang", "我们的黑话")
        term_id = next(t["id"] for t in db.get_terms(UID) if t["term"] == "菟丝子")
        data = client.get("/api/memory/interaction-style").json()
        assert "短句" in data["style"]
        assert any(term["term"] == "菟丝子" for term in data["terms"])

        # 可重置 / 可逐条删除
        assert client.delete("/api/memory/interaction-style").status_code == 200
        assert db.get_style(UID) == ""
        assert client.delete(f"/api/memory/terms/{term_id}").status_code == 200
        assert all(term["id"] != term_id for term in db.get_terms(UID))
        assert client.delete("/api/memory/terms/999999").status_code == 404
    print("[OK] 双向了解/互动偏好：她的侧面可查看；说话风格可重置、共同语言可逐条删除")


async def main() -> None:
    _test_conflict_repair_multi_turn()
    _test_stage_frame_and_guard()
    _test_profile_and_style_api()
    print("\n=== M4 双向关系与修复：全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
