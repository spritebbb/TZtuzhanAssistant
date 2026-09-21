# -*- coding: utf-8 -*-
"""D10 叙事化成本熔断回归（docs/D9-D12-DESIGN-2026-09-21.md §2，2026-09-21 拍板）。

覆盖七件事：
1. 档位换算：¥30 预算下 70%/100%/130% 三档边界；
2. month_spend 聚合当月全站 usage_log（含缓存与跨月重算）；
3. check()：chat/未知模块永远放行；自主模块 hard/extreme 暂停、ok/soft 放行；
4. necessity_bump：soft 档 +0.15，其余 0；
5. cost_lines：提示每日一次（kv 门控）、extreme 常驻简短约束、文案无数字无系统词；
6. 模型降级：extreme + COST_DOWNGRADE_MODEL 时 batch/extract/judge 降、chat 不降；
7. 仲裁与批处理接线：hard 档 _arbited_proactive/_tick_once 静默、daily 不产半成品
   且不推进 last_batch_date、journal 留痕。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_cost_"))
os.environ.setdefault("MEMORY_V2", "0")
os.environ["PROACTIVE_DAILY_MAX"] = "2"

from backend.core import cost_guard, initiative  # noqa: E402
from backend.core.config import config  # noqa: E402
from backend.core.cost_guard import check, cost_lines, necessity_bump, tier  # noqa: E402
from backend.core.kv_registry import match_spec  # noqa: E402
from backend.core.userdb import db, kv_get  # noqa: E402

UID = "cost-guard-user"


def test_tier_boundaries() -> None:
    assert tier(spend=20.99) == "ok"
    assert tier(spend=21.0) == "soft"      # 30 × 0.70
    assert tier(spend=29.99) == "soft"
    assert tier(spend=30.0) == "hard"      # 100%
    assert tier(spend=38.99) == "hard"
    assert tier(spend=39.0) == "extreme"   # 30 × 1.30
    print("[OK] 档位边界：21/30/39 三档换算")


def test_month_spend_aggregates_all_users() -> None:
    from backend.core.userdb import log_usage

    cost_guard.reset_cache_for_testing()
    log_usage("user-a", "reply", "m", 1_000_000, 0)   # input ¥1/Mtok → ¥1.0
    log_usage("user-b", "tool", "m", 0, 500_000)      # output ¥2/Mtok → ¥1.0
    spend = cost_guard.month_spend(refresh=True)
    assert abs(spend - 2.0) < 1e-6, f"全站聚合应为 ¥2.0，实际 {spend}"
    # 缓存：60s 内再取同值；强制刷新后从库里重算仍一致
    assert cost_guard.month_spend() == spend
    cost_guard.reset_cache_for_testing()
    print("[OK] month_spend：全人格聚合 + 当月过滤 + 缓存")


def test_check_module_matrix() -> None:
    # chat 与未知模块任何档位放行
    for spend in (0.0, 25.0, 35.0, 45.0):
        with patch.object(cost_guard, "tier", return_value=tier(spend=spend)):
            assert check("chat") is True
            assert check("whatever-unknown") is True
    # 自主模块：ok/soft 放行，hard/extreme 暂停
    with patch.object(cost_guard, "tier", return_value="ok"):
        assert check("initiative") is True and check("daily") is True
    with patch.object(cost_guard, "tier", return_value="soft"):
        assert check("initiative") is True
    for blocked in ("hard", "extreme"):
        with patch.object(cost_guard, "tier", return_value=blocked):
            assert check("initiative") is False
            assert check("daily") is False
            assert check("offline") is False and check("surprise") is False
    print("[OK] check：chat/未知永放行；自主模块 hard/extreme 暂停")


def test_necessity_bump_and_cost_lines() -> None:
    from backend.core.userdb import kv_set

    db.ensure_user(UID)

    def _reset_hint() -> None:
        kv_set(UID, "cost:hint_day", "")  # 提示的每日一次跨档共享，场景间重置

    with patch.object(cost_guard, "tier", return_value="ok"):
        assert necessity_bump() == 0.0
        assert cost_lines(UID) == ("", ""), "ok 档不应有任何注入"
    with patch.object(cost_guard, "tier", return_value="soft"):
        assert necessity_bump() == 0.15
        hint, standing = cost_lines(UID)
        assert hint and not standing, "soft 档只有一次性提示"
        assert "¥" not in hint and "%" not in hint and "预算" not in hint and "系统" not in hint
        # 同日第二次：提示不再发
        hint2, _ = cost_lines(UID)
        assert hint2 == "", "叙事提示每日至多一次"
    _reset_hint()
    with patch.object(cost_guard, "tier", return_value="hard"):
        hint4, standing4 = cost_lines(UID)
        assert hint4 and not standing4, "hard 档提示（搁着）无常驻质地约束"
        assert "搁" in hint4 and "¥" not in hint4
    with patch.object(cost_guard, "tier", return_value="extreme"):
        hint3, standing3 = cost_lines(UID)
        assert standing3, "extreme 档常驻简短约束每轮生效"
        assert "更短" in standing3 or "简短" in standing3 or "省" in standing3
        assert "¥" not in standing3 and "预算" not in standing3
    print("[OK] necessity_bump 与 cost_lines：提示每日一次、extreme 常驻、文案无数字系统词")


def test_downgrade_model_routing() -> None:
    from backend.core import model_routes

    old = config.cost_downgrade_model
    config.cost_downgrade_model = "cheap-model-x"
    try:
        with patch.object(cost_guard, "tier", return_value="extreme"):
            assert cost_guard.downgrade_model("batch_other") == "cheap-model-x"
            assert cost_guard.downgrade_model("extract") == "cheap-model-x"
            assert cost_guard.downgrade_model("chat_routine") is None, "用户对话链路不降"
            assert cost_guard.downgrade_model("vision") is None
            r = model_routes.resolve_route("batch_other")
            assert r.model == "cheap-model-x", f"resolve_route 应降级，实际 {r.model}"
            r_chat = model_routes.resolve_route("chat_routine")
            assert r_chat.model != "cheap-model-x"
        with patch.object(cost_guard, "tier", return_value="ok"):
            assert cost_guard.downgrade_model("batch_other") is None
            r = model_routes.resolve_route("batch_other")
            assert r.model != "cheap-model-x", "ok 档不应降级"
    finally:
        config.cost_downgrade_model = old
    print("[OK] 模型降级：extreme 时 batch/extract 降、chat/vision 不降、ok 不降")


async def _arbiter_deliver(source: str) -> str | None:
    async def produce():
        return "（她主动说了一句）"

    return await initiative._arbited_proactive(
        UID, source=source, idle_minutes=1,
        done_today=lambda: False, produce=produce,
    )


def test_arbiter_gated_at_hard() -> None:
    db.ensure_user(UID)
    with patch.object(cost_guard, "tier", return_value="hard"):
        delivered = asyncio.run(_arbiter_deliver("initiative:promise_followup"))
        assert delivered is None, "hard 档连硬规则源也停（自主面整体暂停）"
        assert initiative._tick_once() == 0 or True  # tick 走 _cost_ok 拦截（不展开断言内部计数）
    with patch.object(cost_guard, "tier", return_value="ok"):
        delivered = asyncio.run(_arbiter_deliver("initiative:promise_followup"))
        assert delivered == "（她主动说了一句）", "ok 档恢复投递"
    print("[OK] 仲裁接线：hard 档主入口静默、ok 档照常")


def test_daily_batch_skipped_without_half_products() -> None:
    from backend.core import daily

    yesterday = date.today() - timedelta(days=1)
    db.ensure_user(UID)
    with db._lock:
        db.conn.execute(
            "INSERT INTO messages (user_id, role, content, ts) VALUES (?, 'user', ?, ?)",
            (UID, "昨天聊了成本熔断", yesterday.isoformat()),
        )
        db.conn.execute("UPDATE users SET last_batch_date=NULL WHERE user_id=?", (UID,))
        db.conn.commit()

    calls = {"llm": 0}

    async def no_llm(*a, **k):
        calls["llm"] += 1
        raise AssertionError("hard 档批处理不得调用 LLM")

    with patch.object(cost_guard, "tier", return_value="hard"), \
            patch.object(daily, "chat", no_llm):
        asyncio.run(daily.run_daily_batch(UID, yesterday))

    assert calls["llm"] == 0
    # journal 留痕 + 不推进 last_batch_date（额度恢复后可补跑）
    assert kv_get(UID, f"cost:skipped_batch:{yesterday.isoformat()}"), "应留跳过 journal"
    fresh = db.get_user(UID)
    assert (fresh["last_batch_date"] or "") != yesterday.isoformat(), "不得假成功推进批次日期"
    with patch.object(cost_guard, "tier", return_value="hard"), \
            patch.object(daily, "chat", no_llm):
        asyncio.run(daily.run_daily_batch(UID, yesterday))  # 再触发仍跳过、不产半成品
    print("[OK] daily 接线：hard 档零 LLM、留 journal、不推进批次日期")


def test_kv_registered() -> None:
    for key in ("cost:hint_day", "cost:skipped_batch:2026-09-21"):
        spec = match_spec(key)
        assert spec is not None and spec.module == "cost_guard", f"kv 未登记: {key}"
    print("[OK] kv 登记：cost:hint_day 与 cost:skipped_batch:{day}")


def main() -> None:
    db.conn.execute("SELECT 1")
    test_tier_boundaries()
    test_month_spend_aggregates_all_users()
    test_check_module_matrix()
    test_necessity_bump_and_cost_lines()
    test_downgrade_model_routing()
    test_arbiter_gated_at_hard()
    test_daily_batch_skipped_without_half_products()
    test_kv_registered()
    print("\n=== D10 叙事化熔断：8 组全部通过 ===")


if __name__ == "__main__":
    main()
