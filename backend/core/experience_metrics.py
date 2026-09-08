# -*- coding: utf-8 -*-
"""P3-05B 本地质量统计：只记必要统计，默认本地、可关可清。

契约（docs/Zcode技术指导.md P3-05 + 总纲批次 12）：

- 只记录五类必要统计：延迟（调用耗时档）、失败规则（哪类规则失败）、重复率
  （重复内容占比）、来源选择（各语境来源命中计数）、用户明确反馈
  （用户主动确认/否决的信号）——不复制聊天/日记正文当遥测；
- 临时轮（ephemeral）不写统计；用户可在设置里关闭（`experience_metrics_enabled`
  flag）与清理（按 user 清空）；
- 一次统计变化不自动改人格；问题候选交审，系统不以留存/依赖为目标自调参；
- 运行质量统计**不属于关系包**（不进 relationship_export），reset 清空。
"""
from __future__ import annotations

from datetime import datetime

from .log import logger

KINDS: tuple[str, ...] = (
    "latency",        # value=毫秒档位标签（如 "<1s" "1-5s" ">5s"）
    "rule_failure",   # value=规则名
    "repetition",     # value=命中去重窗口
    "source_pick",    # value=来源 entry id
    "user_feedback",  # value=confirm/reject + 对象类型
)
_MAX_RECENT = 200  # 每用户内存保留的最近计数窗（防止无限增长）


def enabled() -> bool:
    try:
        from .features import flag

        return flag("experience_metrics_enabled")
    except Exception:
        return False


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def record(user_id: str, kind: str, value: str, *, count: int = 1) -> bool:
    """记一条统计（计数聚合，不存正文）。kind 不在白名单/功能关闭时静默丢弃。"""
    if kind not in KINDS or not value:
        return False
    if not enabled():
        return False
    from .userdb import db

    with db._lock:
        db.conn.execute(
            "INSERT INTO experience_metrics (user_id, kind, value, count, day, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, kind, value, day) DO UPDATE SET "
            "count = count + excluded.count, updated_at = excluded.updated_at",
            (user_id, kind, value[:120], int(count), datetime.now().date().isoformat(), _now()),
        )
        db.conn.commit()
    return True


def summary(user_id: str, *, days: int = 30) -> list[dict]:
    """聚合计数（给质量面板复用既有入口；不含任何正文）。"""
    from .userdb import db

    cutoff = datetime.now().date().isoformat()
    with db._lock:
        rows = db.conn.execute(
            "SELECT kind, SUM(count) AS total FROM experience_metrics "
            "WHERE user_id=? AND day >= date(?, ?) GROUP BY kind ORDER BY kind",
            (user_id, cutoff, f"-{int(days)} days"),
        ).fetchall()
    return [{"kind": r["kind"], "total": int(r["total"])} for r in rows]


def clear_user(user_id: str) -> int:
    """用户主动清理：清空本人格全部统计。"""
    from .userdb import db

    with db._lock:
        cur = db.conn.execute(
            "DELETE FROM experience_metrics WHERE user_id=?", (user_id,)
        )
        db.conn.commit()
    logger.info("[统计] 已清理 {} 的 {} 条统计", user_id, cur.rowcount)
    return int(cur.rowcount)
