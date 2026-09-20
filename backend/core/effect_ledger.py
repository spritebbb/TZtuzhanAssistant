# -*- coding: utf-8 -*-
"""本轮对话「旁路副作用」的统一登记与失败记账。

背景：pipeline 里曾散落 90 个内联的 ``try / except Exception: logger.exception(...)``
（全后端 514 处 ``except Exception``、85 处直接 ``pass``）。这类块把「不影响回复的
旁路动作」包成了静默区：失败只留一行日志，既不聚合、也无法在健康检查或 UI 里看见，
于是「测试全绿」这个信号被稀释成噪声。

这里把这套语义显式化：

- 一个副作用要么成功（记入 ``applied``），要么失败（记入 ``failures`` + 进程级累计）；
- 失败**永不**向上抛出，主流程照旧返回回复——行为与旧代码一致；
- 同一轮内同名副作用重复失败只详细记一次日志（流式回调断连会连爆几百次，
  不能把日志刷爆），但每次都计数，保证「吞掉了多少次」可查。

进程级累计通过 :func:`effect_stats` 暴露，挂在 ``/api/meta`` 的 ``effect_stats``
字段上，让「被吞掉的错误」从不可见变成可观测。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

from .log import logger


@dataclass
class EffectFailure:
    """一次被登记的副作用失败。"""

    name: str
    error_type: str
    message: str


@dataclass
class EffectLedger:
    """单个作用域（通常是一轮对话）内的副作用台账。

    用法::

        ledger = EffectLedger("turn")
        ledger.run("重逢状态推进", observe_user_turn, user_id, turn_id, text)
        await ledger.run_async("活动草稿", make_draft, user_id)

    失败时返回 ``None``，调用方无需再包 try/except。
    """

    scope: str
    applied: list[str] = field(default_factory=list)
    failures: list[EffectFailure] = field(default_factory=list)
    _logged: set[str] = field(default_factory=set, repr=False)

    # ---- 同步 ----
    def run(self, name: str, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        try:
            value = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 —— 旁路副作用失败不得中断主流程
            self._fail(name, exc)
            return None
        self.applied.append(name)
        return value

    # ---- 异步 ----
    async def run_async(
        self, name: str, fn: Callable[..., Any], /, *args: Any, **kwargs: Any
    ) -> Any:
        try:
            value = await fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            self._fail(name, exc)
            return None
        self.applied.append(name)
        return value

    # ---- 显式登记一次失败（调用方自己 catch 时用）----
    def fail(self, name: str, exc: BaseException) -> None:
        self._fail(name, exc)

    def _fail(self, name: str, exc: BaseException) -> None:
        self.failures.append(
            EffectFailure(name=name, error_type=type(exc).__name__, message=str(exc)[:200])
        )
        _TOTALS[name] += 1
        _LAST_ERROR[name] = f"{type(exc).__name__}: {str(exc)[:200]}"
        if name in self._logged:
            # 同一轮内重复失败（典型是流式回调在客户端断连后连爆）：只计数不刷日志
            logger.debug("[effect] {} 再次失败：{}", name, type(exc).__name__)
            return
        self._logged.add(name)
        logger.exception("[effect] {} 失败（不影响本轮回复）", name)

    # ---- 汇总 ----
    @property
    def failed(self) -> bool:
        return bool(self.failures)

    def summary(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "applied": list(self.applied),
            "failures": [
                {"name": f.name, "error": f.error_type, "detail": f.message}
                for f in self.failures
            ],
        }


# 进程级累计：name -> 失败次数；附最近一次错误摘要，供 /api/meta 观测。
_TOTALS: Counter[str] = Counter()
_LAST_ERROR: dict[str, str] = {}


def effect_stats() -> dict[str, Any]:
    """返回进程内旁路副作用的累计失败统计（按名聚合，不记用户内容）。"""
    return {
        "total_failures": sum(_TOTALS.values()),
        "by_effect": [
            {"name": name, "count": count, "last_error": _LAST_ERROR.get(name, "")}
            for name, count in _TOTALS.most_common(20)
        ],
    }


def reset_effect_stats() -> None:
    """清空累计统计（测试隔离用）。"""
    _TOTALS.clear()
    _LAST_ERROR.clear()
