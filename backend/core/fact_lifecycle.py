# -*- coding: utf-8 -*-
"""事实记忆生命周期：SQLite 是权威源，向量索引只是可重建的派生缓存。"""
from __future__ import annotations

from .log import logger
from .userdb import delete_fact_cascade, resolve_fact_conflict, update_fact


def _delete_vectors(user_id: str, fact_ids: list[int]) -> None:
    from .vector_store import delete as vec_delete

    failed = False
    for fact_id in fact_ids:
        try:
            vec_delete(user_id, "facts", fact_id)
        except Exception:
            failed = True
    if failed:
        logger.warning("[记忆生命周期] 向量删除失败，召回层将按 SQLite 状态阻断残留")


def _index_vector(user_id: str, fact_id: int, content: str) -> None:
    try:
        from .vector_store import index as vec_index

        vec_index(user_id, fact_id, content, "facts")
    except Exception:
        logger.warning("[记忆生命周期] 向量更新失败，后续迁移任务可从 SQLite 重建")


def _fact_content(user_id: str, fact_id: int) -> str:
    from .userdb import db

    row = db.conn.execute(
        "SELECT content FROM facts WHERE user_id = ? AND id = ?", (user_id, fact_id)
    ).fetchone()
    return str(row["content"]) if row else ""


def _record_correction_event(
    user_id: str, fact_id: int, *, action: str, old_content: str, new_content: str
) -> None:
    try:
        from .relationship_events import record_memory_corrected

        record_memory_corrected(
            user_id, fact_id, action=action,
            old_content=old_content, new_content=new_content,
        )
    except Exception:
        logger.warning("[记忆生命周期] memory_corrected 事件记录失败：fact_id={}", fact_id)


def delete_fact_everywhere(user_id: str, fact_id: int) -> bool:
    """硬删除事实、依赖它的待确认候选及所有对应向量；事件库同步作废。"""
    from .relationship_events import invalidate_for_source

    deleted_ids = delete_fact_cascade(user_id, fact_id)
    if not deleted_ids:
        return False
    for deleted_id in deleted_ids:
        invalidate_for_source(user_id, "fact", deleted_id)
        try:
            from .pending_thoughts import forget_thoughts_for_source

            forget_thoughts_for_source(user_id, "fact", deleted_id)
        except Exception:
            logger.warning("[记忆生命周期] 心事级联清理失败：fact_id={}", deleted_id)
    _delete_vectors(user_id, deleted_ids)
    return True


def update_fact_everywhere(user_id: str, fact_id: int, content: str) -> bool:
    """改写事实并重建向量；数据库同时作废基于旧内容的冲突候选。"""
    old_content = _fact_content(user_id, fact_id)
    if not update_fact(user_id, fact_id, content):
        return False
    _record_correction_event(
        user_id, fact_id,
        action="rewrite", old_content=old_content, new_content=content,
    )
    _delete_vectors(user_id, [fact_id])
    _index_vector(user_id, fact_id, content)
    return True


def resolve_fact_conflict_everywhere(
    user_id: str,
    fact_id: int,
    *,
    accept_new: bool,
) -> dict | None:
    """原子切换事实状态后同步索引；残留索引会被 SQLite 召回闸门拦截。"""
    old_content = _fact_content(user_id, fact_id)
    result = resolve_fact_conflict(user_id, fact_id, accept_new)
    if result is None:
        return None
    _record_correction_event(
        user_id, result["fact_id"],
        action="conflict_accept" if result["accepted"] else "conflict_keep",
        old_content=old_content, new_content=str(result.get("content") or ""),
    )
    if result["accepted"]:
        ids = [result["fact_id"]]
        if result["old_fact_id"] is not None:
            ids.append(result["old_fact_id"])
        _delete_vectors(user_id, ids)
        _index_vector(user_id, result["fact_id"], result["content"])
    else:
        _delete_vectors(user_id, [result["fact_id"]])
    return result
