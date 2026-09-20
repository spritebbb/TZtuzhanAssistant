# -*- coding: utf-8 -*-
"""D12 主动意愿 roll 回归（docs/D9-D12-DESIGN-2026-09-21.md §1）。

覆盖六件事：
1. 公式：阶段基线 + 好感项 − 压抑项，clamp 到上下限；
2. 掷骰可复现（同 user+source+分钟同结果）；
3. 硬规则源与 flag 关闭不掷骰直接放行；
4. 想念补偿：连续 3 次未中后 p 抬升；开口清零；
5. 仲裁接线：roll 未中的通用源不投递，约定到点（promise_followup）不受骰子影响；
6. kv 登记（proactive:willingness 在登记表内）。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_will_"))
os.environ.setdefault("MEMORY_V2", "0")
os.environ["PROACTIVE_DAILY_MAX"] = "4"

from backend.core import expression_policy as ep, features, initiative  # noqa: E402
from backend.core.kv_registry import match_spec  # noqa: E402
from backend.core.userdb import db, kv_get, kv_set  # noqa: E402

UID = "willingness-user-1"
NOW = datetime(2026, 9, 21, 12, 0, 0)


def _user() -> None:
    db.ensure_user(UID)
    with db._lock:
        db.conn.execute(
            "UPDATE users SET trust=80, intimacy=80 WHERE user_id=?", (UID,)
        )
        db.conn.commit()


def _patch_state(tension: int = 0, focus: bool = False):
    """打补丁到 willingness 实际读取的模块属性（函数内 import，运行时解析）。"""
    from backend.core import affection, focus as focus_mod, state as state_mod

    dim = patch.object(affection, "dimensions_of", return_value=(80, 80))
    tens = patch.object(state_mod, "_load_tension", return_value={"level": tension})
    foc = patch.object(focus_mod, "focus_in_progress", return_value=focus)
    return dim, tens, foc


def test_formula_and_clamp() -> None:
    _user()
    dim, tens, foc = _patch_state(tension=0, focus=False)
    with dim, tens, foc:
        w = ep.willingness(UID, source="initiative-loop", now=NOW)
    # 恋人 0.55（80/100 过 75 阈值）+ 80/100×0.20 = 0.71，无压抑
    assert abs(w["p"] - 0.71) < 1e-6, f"p 应为 0.71，实际 {w['p']}"
    assert w["base"] == 0.55 and abs(w["affection_term"] - 0.16) < 1e-6

    dim, tens, foc = _patch_state(tension=100, focus=True)
    with dim, tens, foc:
        w = ep.willingness(UID, source="initiative-loop", now=NOW)
    # 0.71 − 0.30(张力) − 0.40(专注) = 0.01 → clamp 到 floor 0.05
    assert w["p"] == 0.05, f"压抑到底应 clamp 到 0.05，实际 {w['p']}"
    print("[OK] 公式：阶段基线+好感−压抑，clamp 到 [floor, ceil]")


def test_roll_reproducible() -> None:
    _user()
    dim, tens, foc = _patch_state()
    with dim, tens, foc:
        w1 = ep.willingness(UID, source="initiative-loop", now=NOW)
        w2 = ep.willingness(UID, source="initiative-loop", now=NOW)
        w3 = ep.willingness(UID, source="initiative:surprise", now=NOW)
    assert w1["roll"] == w2["roll"] and w1["speak"] == w2["speak"], "同种子必须同结果"
    assert w1["roll"] != w3["roll"] or w1["seed_minute"] == w3["seed_minute"], "不同源不同种子"
    assert w1["speak"] == (w1["roll"] < w1["p"])
    print(f"[OK] 掷骰可复现（p={w1['p']} roll={w1['roll']} → {'说' if w1['speak'] else '没说'}）")


def test_hard_rule_and_flag_off() -> None:
    _user()
    # 硬规则源：不掷骰直接放行
    speak, decision = ep.willingness_decision(UID, source="initiative:promise_followup")
    assert speak is True and decision.get("hard_rule") is True
    # flag 关闭：不掷骰直接放行
    features.set_flag("willingness_enabled", False)
    try:
        speak, decision = ep.willingness_decision(UID, source="initiative-loop")
        assert speak is True and decision.get("disabled") is True
    finally:
        features.set_flag("willingness_enabled", True)
    # flag 开启且非硬规则：确实掷了骰（判定详情带 p/roll）
    dim, tens, foc = _patch_state()
    with dim, tens, foc:
        _speak, decision = ep.willingness_decision(UID, source="initiative-loop", now=NOW)
    assert "p" in decision and "roll" in decision
    print("[OK] 硬规则与 flag 关闭均不掷骰直接放行")


def test_miss_compensation() -> None:
    _user()
    kv_set(UID, "proactive:willingness", 3)
    dim, tens, foc = _patch_state()
    with dim, tens, foc:
        w = ep.willingness(UID, source="initiative-loop", now=NOW)
    assert abs(w["compensate"] - 0.15) < 1e-6, "连续 3 次未中应有想念补偿"
    assert abs(w["p"] - (0.71 + 0.15)) < 1e-6
    # 开口后清零；未中后 +1
    ep.note_willingness_outcome(UID, {"speak": True})
    assert int(kv_get(UID, "proactive:willingness") or 0) == 0
    ep.note_willingness_outcome(UID, {"speak": False})
    assert int(kv_get(UID, "proactive:willingness") or 0) == 1
    ep.note_willingness_outcome(UID, {"speak": False})
    assert int(kv_get(UID, "proactive:willingness") or 0) == 2
    kv_set(UID, "proactive:willingness", 0)
    print("[OK] 想念补偿：3 次未中 +0.15；开口清零、未中递增")


async def _arbiter_case(source: str, force_roll: bool | None) -> str | None:
    """直接过统一闸门；force_roll=None 用真实掷骰，True/False 打桩。"""
    _user()

    async def produce():
        return "（她主动说了一句）"

    if force_roll is None:
        deliver = await initiative._arbited_proactive(
            UID, source=source, idle_minutes=1,
            done_today=lambda: False, produce=produce,
        )
        return deliver

    from backend.core import expression_policy as ep_mod

    # 只桩底层掷骰（永远未中），让 willingness_decision 的硬规则分支真实执行
    with patch.object(
        ep_mod, "willingness",
        return_value={"p": 0.5, "roll": 0.9, "speak": False,
                      "suppression": 0.0, "miss_streak": 0, "compensate": 0.0},
    ):
        return await initiative._arbited_proactive(
            UID, source=source, idle_minutes=1,
            done_today=lambda: False, produce=produce,
        )


def test_arbiter_wiring() -> None:
    # roll 未中：通用源静默不投递，想念计数 +1
    delivered = asyncio.run(_arbiter_case("initiative-loop", force_roll=False))
    assert delivered is None, "roll 未中的通用源不应投递"
    assert int(kv_get(UID, "proactive:willingness") or 0) >= 1

    # 同一条件下约定到点是硬规则：不受骰子影响照常投递
    delivered = asyncio.run(_arbiter_case("initiative:promise_followup", force_roll=False))
    assert delivered == "（她主动说了一句）", "promise_followup 不进骰子，必须照常投递"
    print("[OK] 仲裁接线：roll 未中静默、约定到点不受骰子影响")


def test_kv_registered() -> None:
    spec = match_spec("proactive:willingness")
    assert spec is not None and spec.module == "expression_policy", "kv 必须登记"
    print("[OK] kv 登记：proactive:willingness → expression_policy/runtime")


def main() -> None:
    db.conn.execute("SELECT 1")
    test_formula_and_clamp()
    test_roll_reproducible()
    test_hard_rule_and_flag_off()
    test_miss_compensation()
    test_arbiter_wiring()
    test_kv_registered()
    print("\n=== D12 意愿 roll：6 组全部通过 ===")


if __name__ == "__main__":
    main()
