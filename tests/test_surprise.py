# -*- coding: utf-8 -*-
"""M5 惊喜编排：只基于真实产物、低频门控、可关闭、经统一仲裁器消耗额度。"""
from __future__ import annotations

import asyncio
import datetime
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_surprise_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import proactive_policy as policy, surprise  # noqa: E402

from backend.core import features  # noqa: E402
# D12：惊喜走意愿骰子，本组断言投递成功——关掉骰子保持确定性（意愿层另有专测）。
features.set_flag("willingness_enabled", False)
from backend.core.config import config  # noqa: E402
from backend.core.userdb import db, kv_get  # noqa: E402

UID = "surprise-user"


def _make_user() -> None:
    db.ensure_user(UID)
    db.set_affection_absolute(UID, 60)  # 熟悉


def _insert_artifact(title: str = "《灯塔看守人的猫》共同故事", source_id: int = 1) -> int:
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO artifacts (user_id, artifact_type, source_type, source_id, title, "
            "content, version, created_at, updated_at) "
            "VALUES (?, 'co_story', 'activity', ?, ?, '【对方】猫在第七天开始学着数浪。', 1, ?, ?)",
            (UID, source_id, title, now, now),
        )
        db.conn.commit()
        return int(cur.lastrowid)


async def _test_gates() -> None:
    _make_user()
    # 开关门控
    original = config.proactive_surprise_enabled
    config.proactive_surprise_enabled = False
    try:
        assert await surprise.maybe_orchestrate_surprise(UID, roll=0) is None
    finally:
        config.proactive_surprise_enabled = original
    # 无素材
    assert await surprise.maybe_orchestrate_surprise(UID, roll=0) is None
    # 有素材但概率未中
    _insert_artifact()
    assert await surprise.maybe_orchestrate_surprise(UID, roll=99) is None
    print("[OK] 门控：开关/无素材/概率未中都不出牌")


async def _test_delivery_and_grounding() -> None:
    captured: dict = {}

    async def fake_chat(messages, **kwargs):
        captured["messages"] = messages
        return "那个故事我后来又想了一遍——第八天，灯塔其实亮了两次。"

    sent: list[str] = []

    async def fake_enqueue(user_id, text, image=None, epoch=None):
        sent.append(text)
        return True

    with patch("backend.core.llm.chat", new=fake_chat), \
         patch("backend.core.initiative.enqueue_proactive", new=fake_enqueue):
        text = await surprise.maybe_orchestrate_surprise(UID, roll=0)
    assert text and "第八天" in text
    prompt = captured["messages"][-1]["content"]
    assert "《灯塔看守人的猫》共同故事" in prompt, "惊喜 prompt 必须如实引用真实产物"
    assert "数浪" in prompt
    assert "绝不编造" in prompt
    assert policy.active_count_today(UID) == 1, "惊喜经统一仲裁器消耗共享每日额度"
    # 低频：刚送过，间隔期内即使概率命中也不再出牌
    assert await surprise.maybe_orchestrate_surprise(UID, roll=99) is None
    assert kv_get(UID, "surprise:last") == datetime.date.today().isoformat()
    print("[OK] 出牌：只基于真实产物、走统一额度、间隔期生效")


async def _test_material_rotation() -> None:
    second_id = _insert_artifact("《便利店深夜清单》共同故事", source_id=2)
    picked = surprise._pick_material(UID)
    assert picked is not None and picked["id"] == second_id, "优先选没用过的产物"
    print("[OK] 素材轮换：上次用过的产物让位给新的")


async def main() -> None:
    await _test_gates()
    await _test_delivery_and_grounding()
    await _test_material_rotation()
    print("惊喜编排 M5 测试通过")


if __name__ == "__main__":
    asyncio.run(main())
