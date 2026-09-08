# -*- coding: utf-8 -*-
"""M1 过期事实自然衰减：只清理到期且未固定的事实。"""
from __future__ import annotations

import asyncio
from datetime import datetime

from .log import logger
from .userdb import db


def decay_expired_facts(user_id: str, *, now: datetime | None = None) -> list[int]:
    """物理删除已到期的 active 事实，返回成功删除的根事实 ID。

    选择条件与召回闸门保持一致：``expires_at <= now`` 即不再可召回。
    ``pinned=1`` 永远不会被自动衰减。实际删除统一走 fact_lifecycle，确保
    冲突候选、关系事件、pending thought 与向量缓存一起收敛。
    """
    moment = (now or datetime.now()).isoformat(timespec="seconds")
    with db._lock:
        rows = db.conn.execute(
            "SELECT id FROM facts WHERE user_id = ? AND status = 'active' "
            "AND pinned = 0 AND expires_at IS NOT NULL AND expires_at <= ? "
            "ORDER BY id",
            (user_id, moment),
        ).fetchall()
    from .memory_salience import policy_expired_ids

    expired_ids = sorted({int(row["id"]) for row in rows} | policy_expired_ids(user_id, now=now))
    if not expired_ids:
        return []

    from .fact_lifecycle import delete_fact_everywhere

    deleted = []
    # 用户可能在扫描后固定/重评了记忆；删除前在同一写锁下复查，避免迟到清理。
    with db._lock:
        policy_expired = policy_expired_ids(user_id, now=now)
        for fact_id in expired_ids:
            row = db.conn.execute(
                "SELECT status, pinned, expires_at FROM facts WHERE user_id=? AND id=?",
                (user_id, fact_id),
            ).fetchone()
            if row is None or row["pinned"] or row["status"] != "active":
                continue
            if not ((row["expires_at"] and row["expires_at"] <= moment)
                    or fact_id in policy_expired):
                continue
            if delete_fact_everywhere(user_id, fact_id):
                deleted.append(fact_id)
    if deleted:
        logger.info("[事实衰减] {} 清理 {} 条过期事实", user_id, len(deleted))
    return deleted


async def decay_expired_facts_async(user_id: str) -> list[int]:
    """事件循环友好的清理入口。"""
    return await asyncio.to_thread(decay_expired_facts, user_id)
