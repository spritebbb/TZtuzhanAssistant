# -*- coding: utf-8 -*-
"""38项#31 软勿扰回归：IDE+输入密集 → 次级源 necessity 阈值软抬升（非硬静默）。

四场景：code+密集(30s)→置位；code+没动(300s)→不置位；browse+密集→不置位；
fullscreen → 硬静默语义不变（return 0 且不置位）。bump 取值语义单测。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tz_softgate_"))

from backend.core import initiative  # noqa: E402
from backend.core import config as _config  # noqa: E402


async def _run_tick_with_probe(probe_ret: dict) -> int:
    """以给定探测结果跑一轮 _tick_once，返回其返回值。"""
    _config.desktop_awareness = True
    initiative._last_global_run = 0.0  # 绕开全局冷却门
    initiative._soft_focus_active = False
    orig = initiative.probe_foreground if hasattr(initiative, "probe_foreground") else None
    import backend.core.desktop_probe as dp

    dp_orig = dp.probe_foreground
    dp.probe_foreground = lambda: probe_ret
    try:
        return await initiative._tick_once()
    finally:
        dp.probe_foreground = dp_orig
        _config.desktop_awareness = False


def test_bump_semantics() -> int:
    initiative._soft_focus_active = True
    assert initiative._soft_focus_bump() == initiative._SOFT_FOCUS_BUMP
    initiative._soft_focus_active = False
    assert initiative._soft_focus_bump() == 0.0
    print("[OK] bump 取值语义：置位给幅度、复位归零")
    return 0


def test_tick_scenarios() -> int:
    # code + 输入密集 30s → 软勿扰置位（tick 正常执行不 return 0）
    initiative._soft_focus_active = False
    n = asyncio.run(_run_tick_with_probe({"category": "code", "idle_seconds": 30.0}))
    assert initiative._soft_focus_active is True, "code+密集应置位"
    assert n == 0  # 无用户时 tick 自然零产出，但不是被硬静默拦截
    print("[OK] code + idle<60s → 软勿扰置位（非硬静默）")

    # code + 没动 300s → 不置位（开着 IDE 看文档不该被打扰降级之外的影响）
    n = asyncio.run(_run_tick_with_probe({"category": "code", "idle_seconds": 300.0}))
    assert initiative._soft_focus_active is False
    # browse + 密集 → 不置位
    asyncio.run(_run_tick_with_probe({"category": "browse", "idle_seconds": 10.0}))
    assert initiative._soft_focus_active is False
    print("[OK] code+300s / browse+密集 → 不降级")

    # fullscreen → 硬静默 return 0 且不进入软勿扰置位分支
    n = asyncio.run(_run_tick_with_probe({"category": "fullscreen", "idle_seconds": 5.0}))
    assert n == 0
    assert initiative._soft_focus_active is False  # helper 进门已复位，fullscreen 早退不置位
    print("[OK] fullscreen → 硬静默语义不变")
    return 0


def main() -> None:
    test_bump_semantics()
    test_tick_scenarios()
    print("\n=== 38项#31 软勿扰: 2 组全部通过 ===")


if __name__ == "__main__":
    main()
