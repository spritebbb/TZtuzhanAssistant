# -*- coding: utf-8 -*-
"""A3 主动引擎共享闸门：跨通道去重、并发占位与失败冷却。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core import proactive_policy as policy


def _memory_store():
    data: dict[tuple[str, str], str] = {}
    policy._kv_get = lambda uid, key: data.get((uid, key))
    policy._kv_set = lambda uid, key, value: data.__setitem__((uid, key), value)
    policy._kv_del = lambda uid, key: data.pop((uid, key), None) is not None
    policy._daily_max = lambda: 1
    policy._failure_cooldown_sec = lambda: 900
    return data


def test_cross_channel_dedup() -> None:
    _memory_store()
    token = policy.try_claim_active("u", "greeting", day="2026-09-04", now=1000)
    assert token
    assert policy.finish_active_claim(
        "u", token, success=True, source="greeting", day="2026-09-04", now=1001
    )
    assert policy.active_done_today("u", day="2026-09-04")
    assert policy.try_claim_active("u", "initiative-poll", day="2026-09-04", now=1002) is None
    print("[OK] 问候成功后轮询主动共享每日额度")


def test_concurrent_claim() -> None:
    _memory_store()
    first = policy.try_claim_active("u", "initiative-loop", day="2026-09-04", now=2000)
    second = policy.try_claim_active("u", "initiative-poll", day="2026-09-04", now=2000)
    assert first and second is None
    assert not policy.finish_active_claim(
        "u", "wrong-token", success=True, source="initiative-poll", day="2026-09-04", now=2001
    )
    assert policy.finish_active_claim(
        "u", first, success=True, source="initiative-loop", day="2026-09-04", now=2001
    )
    print("[OK] loop/poll 同时到达时只有一个生成者拿到占位")


def test_failure_cooldown_and_retry() -> None:
    _memory_store()
    token = policy.try_claim_active("u", "initiative-poll", day="2026-09-04", now=3000)
    assert token
    assert policy.finish_active_claim(
        "u", token, success=False, source="initiative-poll", day="2026-09-04", now=3010
    )
    assert policy.try_claim_active("u", "initiative-poll", day="2026-09-04", now=3500) is None
    assert policy.try_claim_active("u", "initiative-poll", day="2026-09-04", now=4000)
    print("[OK] 失败后冷却期内不重试，冷却结束可恢复")


def test_stale_claim_recovers() -> None:
    _memory_store()
    assert policy.try_claim_active("u", "initiative-loop", day="2026-09-04", now=5000)
    assert policy.try_claim_active("u", "initiative-poll", day="2026-09-04", now=5299) is None
    assert policy.try_claim_active("u", "initiative-poll", day="2026-09-04", now=5301)
    print("[OK] 崩溃遗留的占位超时后自动恢复")


def test_initiative_loop_actually_ticks() -> None:
    """回归：loop 第一轮必须真的执行 _tick_once。

    P1 缺陷是循环体内 `from .storage import runtime` 导入路径写错，ImportError 被
    兜底 except 吞掉，于是 loop 永远空转、主动性完全不出牌。这里断言首轮确实进入
    tick：若导入再次失败，calls 为空即失败；`asyncio.sleep` 被替换成立刻取消，
    保证失败时快速退出而不是无限空转。
    """
    import asyncio
    from unittest.mock import patch

    from backend.core import initiative
    from backend.core.config import config as core_config

    calls: list[str] = []

    async def fake_tick() -> int:
        calls.append("tick")
        return 0

    async def fake_sleep(_delay: float) -> None:
        raise asyncio.CancelledError()

    async def drive() -> None:
        await initiative.initiative_loop()

    original_tick = initiative._tick_once
    original_interval = core_config.proactive_check_interval_sec
    initiative._tick_once = fake_tick
    core_config.proactive_check_interval_sec = 0.01
    try:
        with patch("backend.core.initiative.asyncio.sleep", new=fake_sleep):
            try:
                asyncio.run(drive())
            except asyncio.CancelledError:
                pass
    finally:
        initiative._tick_once = original_tick
        core_config.proactive_check_interval_sec = original_interval

    assert calls == ["tick"], f"initiative_loop 未进入 _tick_once：{calls}"
    print("[OK] initiative_loop 首轮确实进入 tick（导入失败不再被静默吞掉）")


def main() -> None:
    test_cross_channel_dedup()
    test_concurrent_claim()
    test_failure_cooldown_and_retry()
    test_stale_claim_recovers()
    test_initiative_loop_actually_ticks()
    print("\n=== A3 主动引擎闸门：5 项全部通过 ===")


if __name__ == "__main__":
    main()

