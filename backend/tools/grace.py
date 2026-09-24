# -*- coding: utf-8 -*-
"""权限中间态（§24.3-3）：工具级「限时免确认」授权（grace）。

契约：介于「每次点确认」与「全放行」之间——用户对某工具授予 N 分钟免确认
窗口（确认弹窗一键授予或 API 管理），到期自动恢复逐次确认。

安全边界：
- **高危永不豁免**：danger_level 为 high/critical 的工具（任意代码/命令执行、
  外部 Agent 派发等）不接受 grace，授予接口直接拒绝；
- **重启即清**：授权是进程内存态，后端重启后恢复逐次确认（安全默认）；
- **硬上限**：单次授予 ≤120 分钟，重复授予只刷新到期时间不叠加；
- **全程审计**：授予/命中放行/过期逐条写日志。
"""
from __future__ import annotations

import time

from ..core.log import logger

GRACE_MAX_MINUTES = 120
NON_GRACEABLE_DANGER = ("high", "critical")

# tool 名 → 到期 epoch 秒（进程内存态：重启即清，安全默认）
_grants: dict[str, float] = {}


def graceable(danger_level: str | None) -> bool:
    """该危险等级的工具是否可被豁免。"""
    return str(danger_level or "") not in NON_GRACEABLE_DANGER


def grant(tool: str, minutes: int, *, danger_level: str | None = None) -> dict:
    """授予限时免确认。返回 {"ok", "expires_at"|"error"}。"""
    tool = str(tool or "").strip()
    if not tool:
        return {"ok": False, "error": "缺少工具名"}
    if not graceable(danger_level):
        return {"ok": False, "error": f"高危工具（{danger_level}）不可免确认"}
    try:
        m = max(1, min(GRACE_MAX_MINUTES, int(minutes)))
    except (TypeError, ValueError):
        return {"ok": False, "error": "分钟数非法"}
    expires = time.time() + m * 60
    _grants[tool] = expires
    logger.info("[权限中间态] {} 免确认 {} 分钟（至 {}）", tool, m,
                time.strftime("%H:%M:%S", time.localtime(expires)))
    return {"ok": True, "tool": tool, "minutes": m, "expires_at": expires}


def granted(tool: str, danger_level: str | None = None) -> bool:
    """该工具此刻是否在有效免确认期内（惰性清理过期项）。"""
    expires = _grants.get(str(tool or ""))
    if expires is None:
        return False
    if time.time() >= expires:
        _grants.pop(str(tool), None)
        logger.info("[权限中间态] {} 免确认窗口已到期，恢复逐次确认", tool)
        return False
    if not graceable(danger_level):
        # 双保险：即便被错误授予（如降级前的残留），高危在判定处再拦一道
        return False
    return True


def revoke_all() -> int:
    """撤销全部免确认授权，返回撤销条数。"""
    n = len(_grants)
    if n:
        logger.info("[权限中间态] 撤销全部免确认授权（{} 项）", n)
    _grants.clear()
    return n


def list_grants() -> list[dict]:
    """当前有效授权（惰性清理过期）。"""
    now = time.time()
    expired = [t for t, exp in _grants.items() if now >= exp]
    for t in expired:
        _grants.pop(t, None)
    return [
        {"tool": t, "expires_at": exp, "remaining_min": round((exp - now) / 60, 1)}
        for t, exp in sorted(_grants.items(), key=lambda kv: kv[1])
    ]


def reset_for_testing() -> None:
    _grants.clear()


__all__ = [
    "GRACE_MAX_MINUTES",
    "grant",
    "graceable",
    "granted",
    "list_grants",
    "reset_for_testing",
    "revoke_all",
]
