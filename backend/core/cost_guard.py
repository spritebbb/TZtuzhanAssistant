# -*- coding: utf-8 -*-
"""D10 成本熔断：到档暂停自主 LLM 工作，用户聊天永不受影响。

契约（docs/D9-D12-DESIGN-2026-09-21.md §2，2026-09-21 用户拍板）：

- 档位按**当月全站实测开销**（usage_log 聚合，与账本同口径定价）：
  ok <70% / soft ≥70% / hard ≥100% / extreme ≥130%（预算默认 ¥30/月）；
- soft：非紧急主动 necessity 阈值 +0.15；
- hard：自主 LLM 工作（initiative/daily/situation）全部暂停，
  journal 记原因不产半成品；**用户主动聊天任何档位都不受影响**；
- extreme：hard 之上，感知/批处理文本路由降级到廉价模型；
- 本模块自身零 LLM 调用；月度聚合带 60s 缓存，热路径无额外 SQL。

注：原「叙事化」面（把超支包装成她「按日程去忙了」的行为帧文案）已移除——
系统约束从人设后面漏出来是最容易穿帮的谎。
"""
from __future__ import annotations

import time
from datetime import date, datetime

from .config import config
from .log import logger

TIERS = ("ok", "soft", "hard", "extreme")
# 自主消费方：hard/extreme 档暂停；chat 与未知模块永远放行（不误伤既有调用方）
_AUTONOMOUS_MODULES = {"initiative", "daily", "situation"}
# extreme 档降级的文本路由：用户对话（chat_*/tool/vision）不降，保住交互质量
_DOWNGRADE_TASKS = {"batch_diary", "batch_other", "extract", "judge"}

_SKIP_KEY = "cost:skipped_batch:{day}"

_SPEND_CACHE: tuple[str, float, float] | None = None  # (month_key, spend, cached_at)
_SPEND_TTL_SEC = 60.0


def _cost_cny(prompt_tokens: int, completion_tokens: int) -> float:
    """与账本面板/usage API 同口径的定价换算。"""
    return (
        int(prompt_tokens) * config.llm_price_input_per_mtok / 1_000_000
        + int(completion_tokens) * config.llm_price_output_per_mtok / 1_000_000
    )


def month_spend(*, now: datetime | None = None, refresh: bool = False) -> float:
    """当月全站实测开销（¥）。聚合全人格 usage_log（同一个钱包），60s 缓存。"""
    global _SPEND_CACHE
    moment = now or datetime.now()
    month_key = moment.strftime("%Y-%m")
    if (
        not refresh
        and _SPEND_CACHE is not None
        and _SPEND_CACHE[0] == month_key
        and time.time() - _SPEND_CACHE[2] < _SPEND_TTL_SEC
    ):
        return _SPEND_CACHE[1]
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0) "
            "FROM usage_log WHERE ts LIKE ?",
            (month_key + "-%",),
        ).fetchone()
    spend = _cost_cny(int(row[0]), int(row[1]))
    _SPEND_CACHE = (month_key, spend, time.time())
    return spend


def tier(*, spend: float | None = None) -> str:
    """ok/soft/hard/extreme。spend 可注入（测试/面板）。"""
    budget = max(0.01, float(config.cost_monthly_budget_cny))
    value = month_spend() if spend is None else float(spend)
    if value >= budget * float(config.cost_extreme_ratio):
        return "extreme"
    if value >= budget:
        return "hard"
    if value >= budget * float(config.cost_soft_ratio):
        return "soft"
    return "ok"


def check(module: str, *, user_id: str = "") -> bool:
    """某消费方此刻能否发起自主 LLM 工作。chat 永远 True。"""
    if module not in _AUTONOMOUS_MODULES:
        return True
    spend = month_spend()
    current = tier(spend=spend)
    if current in ("ok", "soft"):
        return True
    logger.info(
        "[成本闸] {} 暂停：当月实测 ¥{:.2f}/预算 ¥{:.2f}（{} 档）——用户聊天不受影响",
        module, spend, float(config.cost_monthly_budget_cny), current,
    )
    return False


def note_batch_skipped(user_id: str, day: date) -> None:
    """daily 批处理被熔断跳过时的 journal 记录（可观测，不产半成品）。"""
    from .userdb import kv_set

    try:
        kv_set(user_id, _SKIP_KEY.format(day=day.isoformat()),
               datetime.now().isoformat(timespec="seconds"))
    except Exception:
        pass


def necessity_bump() -> float:
    """soft 档的非紧急主动 necessity 阈值抬升（更挑剔地挑话说）。"""
    return 0.15 if tier() == "soft" else 0.0


def downgrade_model(task: str) -> str | None:
    """extreme 档的文本路由降级模型；未配置降级模型或非降级任务返回 None。"""
    if task not in _DOWNGRADE_TASKS or tier() != "extreme":
        return None
    return (getattr(config, "cost_downgrade_model", "") or "").strip() or None


def current_status(*, refresh: bool = False) -> dict:
    """面板/诊断用快照：开销、预算、档位（不含任何叙事文案）。"""
    spend = month_spend(refresh=refresh)
    return {
        "month_spend_cny": round(spend, 4),
        "budget_cny": float(config.cost_monthly_budget_cny),
        "tier": tier(spend=spend),
    }


def reset_cache_for_testing() -> None:
    global _SPEND_CACHE
    _SPEND_CACHE = None


__all__ = [
    "TIERS",
    "check",
    "current_status",
    "downgrade_model",
    "month_spend",
    "necessity_bump",
    "note_batch_skipped",
    "reset_cache_for_testing",
    "tier",
]
