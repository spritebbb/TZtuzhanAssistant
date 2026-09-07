# -*- coding: utf-8 -*-
"""P2-04 链式反应最小引擎：明确约定 → 一次跟进 → 完成/取消后收束。

契约（docs/Zcode技术指导.md §6 P2-04 / §14.7）：
- 只做一条链：promise_due（明确约定到期）→ 一次跟进（复用 initiative 既有
  maybe_follow_up_promise 的表达通道）→ 完成/取消后一次正向回望，即收束；
- 链实例落表 event_chains（唯一 user/source_event/rule），不用多个 KV；
- 消费已提交事件（promise 完成事件），确定性判断资格；probability=1 首链，
  不掷骰子；attempt≤2、expiry 7 天、depth 恒 1（无链中链）；
- due 节点只产生候选，经统一仲裁表达；visible_delivery 按 (user, chain,
  local_date) 唯一，日上限 1，先共享额度认领再表达；
- 用户「算了/不想聊」可取消；对话正常缺席不当失约；负向链硬熔断（本版无
  负向规则——惩罚用户的规则永远不建）；
- 源过期/纠正/reset 使实例失效；LLM 只写台词，不产生 next_node。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .userdb import db

RULE_ID = "promise_aftermath"
RULE_VERSION = 1
FOLLOW_UP_DELAY_MIN = 20          # 完成事件后 20 分钟内可表达回望（同日内）
EXPIRY_DAYS = 7
MAX_ATTEMPT = 2
# visible_delivery 按 (user, chain_id, local_date) 唯一，日上限 1（14.7）


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def on_promise_completed(user_id: str, promise_id: int, *, now: datetime | None = None) -> int | None:
    """消费「约定完成」事件：建链实例（回望节点）。

    幂等：同 (user, promise, rule) 一生一条；用户已取消同类回望时尊重取消。
    """
    now = now or datetime.now()
    due = now + timedelta(minutes=FOLLOW_UP_DELAY_MIN)
    with db._lock:
        # 该约定被用户明确取消过回望 → 不再建
        cancelled = db.conn.execute(
            "SELECT 1 FROM event_chains WHERE user_id=? AND source_event_id=? "
            "AND rule_id=? AND status='cancelled'",
            (user_id, int(promise_id), RULE_ID),
        ).fetchone()
        if cancelled:
            return None
        cur = db.conn.execute(
            "INSERT OR IGNORE INTO event_chains "
            "(user_id, source_event_id, rule_id, rule_version, node, status, due_at, "
            "attempt, created_at, updated_at) VALUES (?, ?, ?, ?, 'aftermath', 'waiting', ?, 0, ?, ?)",
            (user_id, int(promise_id), RULE_ID, RULE_VERSION,
             due.isoformat(timespec="seconds"), _now(), _now()),
        )
        db.conn.commit()
        chain_id = int(cur.lastrowid) if cur.rowcount else None
    if chain_id is not None:
        from .pending_thoughts import _add

        _add(
            user_id,
            "chain_aftermath",
            "event_chain",
            chain_id,
            "一起完成的那件事有了结果，可以自然地回望一句",
            earliest_at=due.isoformat(timespec="seconds"),
            priority=6,
        )
    return chain_id


def due_chains(user_id: str, now: datetime | None = None) -> list[dict]:
    """到点的回望链（waiting 且 due_at 已到，未超尝试上限/过期）。"""
    now = now or datetime.now()
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM event_chains WHERE user_id=? AND rule_id=? "
            "AND status='waiting' AND attempt<? AND due_at<=? AND due_at>=?",
            (user_id, RULE_ID, MAX_ATTEMPT,
             now.isoformat(timespec="seconds"),
             (now - timedelta(days=EXPIRY_DAYS)).isoformat(timespec="seconds")),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_expressed(chain_id: int, thought_id: int | None = None) -> None:
    """回望已表达 → 链收束（done）。一次回望后整条链结束。"""
    with db._lock:
        db.conn.execute(
            "UPDATE event_chains SET status='done', result_id=?, updated_at=?, "
            "attempt=attempt+1 WHERE id=?",
            (thought_id, _now(), chain_id),
        )
        db.conn.commit()


def mark_failed_retry(chain_id: int) -> None:
    """表达失败：记一次尝试，未超上限则保留待重试（不复制实例）。"""
    with db._lock:
        db.conn.execute(
            "UPDATE event_chains SET attempt=attempt+1, updated_at=? "
            "WHERE id=? AND status='waiting'",
            (_now(), chain_id),
        )
        db.conn.commit()


def cancel_chain(user_id: str, source_event_id: int, *, by_user: bool = True) -> bool:
    """用户「算了/不想聊」可取消；取消永久（该来源不再建链）。"""
    with db._lock:
        cur = db.conn.execute(
            "UPDATE event_chains SET status='cancelled', updated_at=? "
            "WHERE user_id=? AND source_event_id=? AND rule_id=? AND status IN ('waiting','due')",
            (_now(), user_id, int(source_event_id), RULE_ID),
        )
        db.conn.commit()
    return cur.rowcount > 0


def expire_stale(now: datetime | None = None) -> int:
    """过期与上限用尽的链收尾（tick/批处理调用；幂等）。"""
    now = now or datetime.now()
    with db._lock:
        cur = db.conn.execute(
            "UPDATE event_chains SET status='expired', updated_at=? "
            "WHERE status='waiting' AND (attempt>=? OR due_at<?)",
            (_now(), MAX_ATTEMPT,
             (now - timedelta(days=EXPIRY_DAYS)).isoformat(timespec="seconds")),
        )
        db.conn.commit()
    return int(cur.rowcount or 0)


def chain_for_source(user_id: str, source_event_id: int) -> dict | None:
    with db._lock:
        row = db.conn.execute(
            "SELECT * FROM event_chains WHERE user_id=? AND source_event_id=? AND rule_id=?",
            (user_id, int(source_event_id), RULE_ID),
        ).fetchone()
    return dict(row) if row else None
