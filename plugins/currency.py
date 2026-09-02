# -*- coding: utf-8 -*-
"""示例插件：汇率换算（演示 plugins/*.py 插件系统 v2 的完整能力）。

在对话里说「100美元等于多少人民币」「汇率」即可触发工具。
同时演示：PLUGIN_META 元信息 / 定时任务 / 系统提示注入 / HTTP 路由。
"""
from __future__ import annotations

import json
import time
import urllib.request

from backend.tools.base import ToolRegistry

# 插件元信息（可选；管理界面/API 展示用）
PLUGIN_META = {
    "name": "汇率换算",
    "version": "2.0.0",
    "description": "USD→CNY 汇率换算工具，带 10 分钟汇率缓存、管理路由与系统提示注入演示",
    "author": "tuzhan",
}

# 汇率缓存（避免每次调用都打外部接口）
_rate_cache: dict = {"rate": None, "ts": 0.0}
_CACHE_TTL = 600  # 秒

# 最近一次换算记录（演示 ctx.route 的管理路由）
_last_convert: dict = {}


def _fetch_rate() -> float | None:
    """从 exchangerate 接口取 USD→CNY 汇率（带缓存，失败返回 None）。"""
    now = time.time()
    if _rate_cache["rate"] is not None and now - _rate_cache["ts"] < _CACHE_TTL:
        return _rate_cache["rate"]
    try:
        req = urllib.request.Request(
            "https://open.er-api.com/v6/latest/USD",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=15,
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            rate = float(data["rates"]["CNY"])
            _rate_cache.update(rate=rate, ts=now)
            return rate
    except Exception:
        return None


def _currency_convert(amount: float = 0, currency: str = "USD", target: str = "CNY") -> str:
    """汇率换算：把指定金额的币种换算成目标币种。"""
    if amount <= 0:
        return "（请提供大于 0 的金额）"
    if currency.upper() != "USD" or target.upper() != "CNY":
        return "（示例插件暂只支持 USD→CNY）"
    rate = _fetch_rate()
    if rate is None:
        return "（汇率获取失败，请稍后再试）"
    converted = amount * rate
    _last_convert.update(amount=amount, rate=rate, converted=converted, ts=now_str())
    return f"{amount:,.2f} USD ≈ {converted:,.2f} CNY（汇率 1 USD = {rate:.4f} CNY）"


def now_str() -> str:
    import datetime

    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def register(ctx) -> None:
    # 1) 注册工具（LLM 原生 Function Calling 调用）
    ToolRegistry.register_func(
        name="currency_convert",
        description="汇率换算（当前支持 USD→CNY）。用户提到美元、汇率、换算金额时使用",
        func=_currency_convert,
        is_async=False,
        owner="currency",
        input_schema={
            "type": "object",
            "properties": {
                "amount": {"type": "number", "description": "要换算的金额（正数）"},
                "currency": {"type": "string", "description": "源币种，默认 USD"},
                "target": {"type": "string", "description": "目标币种，默认 CNY"}
            },
            "required": ["amount"],
        },
    )

    # 2) 定时任务：每 10 分钟预热汇率缓存（演示 ctx.schedule）
    ctx.schedule(_CACHE_TTL, lambda: _fetch_rate(), name="rate-refresh")

    # 3) 系统提示注入：让菟菚"知道"自己带汇率换算能力（演示 ctx.on_system_prompt）
    ctx.on_system_prompt(lambda: "- 你带有汇率换算工具（currency_convert），"
                                 "用户问美元/汇率/换算时可以直接调用。")

    # 4) HTTP 管理路由：GET /plugins/currency/status（演示 ctx.route）
    def _status(request):
        return {
            "ok": True,
            "plugin": "currency",
            "rate_cached": _rate_cache["rate"],
            "cache_age_s": round(time.time() - _rate_cache["ts"], 1) if _rate_cache["ts"] else None,
            "last_convert": _last_convert or None,
        }

    ctx.route("GET", "/status", _status)
