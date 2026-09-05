# -*- coding: utf-8 -*-
"""D6 边界场景：深夜情绪守护（全员）+ 健康边界（熟人以上）的注入逻辑。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime as _RealDt
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 数据目录隔离：先于任何 backend import 生效，避免读写真实 bot.db
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_night_"))

from backend.core import affection
from backend.core.pipeline import process
from backend.core.userdb import db

UID = "night-test-user"


class _FakeDt(_RealDt):
    """固定当前时刻的子类（保持构造器行为，只改 now）。"""

    _fixed = _RealDt(2026, 9, 4, 2, 30)

    @classmethod
    def now(cls, tz=None):
        return cls._fixed


def _set_hour(hour: int) -> None:
    _FakeDt._fixed = _RealDt(2026, 9, 4, hour, 30)


async def _run_and_capture() -> list[dict]:
    captured: list[list[dict]] = []

    async def fake_chat(messages, **kwargs):
        captured.append(messages)
        return "【思考】内部\n【回复】嗯"

    with patch("backend.core.pipeline.chat", new=fake_chat), \
         patch("backend.core.pipeline.datetime", _FakeDt):
        await process(UID, "在吗", mock=True)
    assert captured, "pipeline 未调用主 chat"
    return captured[0]


def _systems(messages: list[dict]) -> list[str]:
    return [m["content"] for m in messages if m["role"] == "system"]


def _has_late_night_guard(systems: list[str]) -> bool:
    """匹配跨人格通用的深夜守护语义，不绑定菟菚专属措辞。"""
    return any("暂时收起可能伤人的玩笑" in s and "先接住情绪" in s for s in systems)


async def test_late_night_intimate() -> None:
    db.ensure_user(UID)
    affection.set_affection(UID, 60)  # 亲密
    db.set_first_chat_done(UID)
    _set_hour(2)
    systems = _systems(await _run_and_capture())
    assert _has_late_night_guard(systems), "深夜情绪守护缺失"
    assert any("催他去睡" in s for s in systems), "熟人健康边界缺失"
    print("[OK] 凌晨 2 点·亲密：emo 守护 + 催睡都在")


async def test_late_night_stranger() -> None:
    affection.set_affection(UID, 5)  # 初识
    _set_hour(2)
    systems = _systems(await _run_and_capture())
    assert _has_late_night_guard(systems), "初识也应有深夜情绪守护"
    assert not any("催他去睡" in s for s in systems), "初识不应催睡（关系没到）"
    print("[OK] 凌晨 2 点·初识：只守护、不催睡")


async def test_daytime_none() -> None:
    _set_hour(14)
    systems = _systems(await _run_and_capture())
    assert not _has_late_night_guard(systems), "白天不应有深夜情绪守护"
    assert not any("催他去睡" in s for s in systems), "白天不应催睡"
    print("[OK] 下午 2 点：两条边界都不注入")


async def test_evening_23() -> None:
    _set_hour(23)
    affection.set_affection(UID, 60)
    systems = _systems(await _run_and_capture())
    assert any("催他去睡" in s for s in systems), "23 点应进入深夜边界"
    print("[OK] 23 点：深夜边界生效")


async def main() -> None:
    await test_late_night_intimate()
    await test_late_night_stranger()
    await test_daytime_none()
    await test_evening_23()
    print("\n=== D6 边界场景：全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
