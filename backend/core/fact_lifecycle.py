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


def delete_fact_everywhere(user_id: str, fact_id: int) -> bool:
    """硬删除事实、依赖它的待确认候选及所有对应向量。"""
    deleted_ids = delete_fact_cascade(user_id, fact_id)
    if not deleted_ids:
        return False
    _delete_vectors(user_id, deleted_ids)
    return True


def update_fact_everywhere(user_id: str, fact_id: int, content: str) -> bool:
    """改写事实并重建向量；数据库同时作废基于旧内容的冲突候选。"""
    if not update_fact(user_id, fact_id, content):
        return False
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
    result = resolve_fact_conflict(user_id, fact_id, accept_new)
    if result is None:
        return None
    if result["accepted"]:
        ids = [result["fact_id"]]
        if result["old_fact_id"] is not None:
            ids.append(result["old_fact_id"])
        _delete_vectors(user_id, ids)
        _index_vector(user_id, result["fact_id"], result["content"])
    else:
        _delete_vectors(user_id, [result["fact_id"]])
    return result
