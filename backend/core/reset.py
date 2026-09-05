# -*- coding: utf-8 -*-
"""彻底重置（失忆重开）：让菟菚忘记对这个用户积累的一切，回到「初识」全新态。

仅用于用户明确选择「重新开始」时调用。范围：
- userdb（bot.db）：好感度/昵称/恋人确认、messages、long_memory、facts、triples、
  important_dates、stickers、user_profile、user_terms、user_style_map、diary、research_reports、
  affection_log、mood_log、activities、activity_notes、tasks、user_meta、kv_store（含 greeting 的 web_last_seen、
  initiative 的 daily 标记等）→ 全部清空。
- 向量库（memory v2）：按 user 前缀的语义向量 → 全清。
- 当前会话（sessions.db 'current'）：气泡清空、标题复位为「新会话」。

不动的数据：archives（用户主动存的会话档案保留，可由侧栏归档区查看/删除）。

实现走「按表 DELETE」而非删 bot.db 文件重建：避免删除动作与正在打开的连接/WAL
冲突（reset() 的降级分支正是这个做法），且无需担心文件被占用。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from .log import logger
from .userdb import db

# userdb 中需要清空的全部业务表（与 userdb.reset() 降级分支保持一致）
_TABLES = (
    "affection_log", "mood_log", "long_memory", "facts", "user_meta", "messages",
    "users", "kv_store", "important_dates", "stickers",
    "user_profile", "user_terms", "user_style_map", "diary", "research_reports", "triples",
    "tasks", "promises", "usage_log", "activity_notes", "activities",
    "kb_documents", "kb_chunks", "unlocks",
)

_reset_lock = asyncio.Lock()
_resetting = False
_reset_epoch = 0
_QUIESCE_TIMEOUT = 120.0


def reset_in_progress() -> bool:
    return _resetting


def reset_epoch() -> int:
    return _reset_epoch


def epoch_is_current(epoch: int) -> bool:
    return epoch == _reset_epoch and not _resetting


class ResetSuperseded(RuntimeError):
    pass


@asynccontextmanager
async def user_write_guard(epoch: int):
    """让一次用户数据写与重置互斥，并拒绝跨越过重置边界的旧请求。"""
    async with _reset_lock:
        if not epoch_is_current(epoch):
            raise ResetSuperseded("request superseded by reset")
        yield


async def _cancel_tasks(tasks: list[asyncio.Task]) -> None:
    current = asyncio.current_task()
    active = [t for t in tasks if t is not current and not t.done()]
    for task in active:
        task.cancel()
    if active:
        await asyncio.gather(*active, return_exceptions=True)


async def _quiesce_user_writers() -> None:
    """停止生成任务，并等待所有已开始的用户记忆写任务真正结束。"""
    from ..api import agent as agent_api
    from ..api import chat as chat_api
    from ..api import remote as remote_api
    from ..agent import session as agent_session
    from . import pipeline, tasks as core_tasks
    from .memory import engine

    # 先落取消状态，再取消执行协程；命令类工具会在 CancelledError 中杀进程树。
    for task_id, task in list(agent_api._agent_bg_by_id.items()):
        if not task.done():
            agent_session.cancel_task(task_id)
    await _cancel_tasks(list(agent_api._agent_bg_by_id.values()))
    await _cancel_tasks(list(chat_api._bg_tasks))
    # 远程工具循环同样会写 userdb/记忆；不停止它就会在清库后回写数据。
    await _cancel_tasks(list(remote_api._remote_bg_by_id.values()))
    await _cancel_tasks(list(core_tasks._tasks))

    # pipeline/Mem0 中可能含 asyncio.to_thread。取消外层 Task 无法杀工作线程，
    # 所以必须等待真实完成后再清库；超时则拒绝重置，避免“清完又写回来”。
    writers = [
        task for task in [*pipeline._memory_tasks, *engine._message_tasks]
        if not task.done()
    ]
    if writers:
        done, pending = await asyncio.wait(writers, timeout=_QUIESCE_TIMEOUT)
        if pending:
            raise TimeoutError(f"仍有 {len(pending)} 个记忆任务未结束，请稍后重试")
        for task in done:
            if task.cancelled():
                continue
            exc = task.exception()
            if exc is not None:
                logger.warning("[重置] 后台记忆任务以异常结束：{}", exc)


async def reset_everything() -> dict:
    """执行彻底失忆重置，返回逐步骤状态；任何失败都明确返回 ``ok=False``。"""
    global _resetting, _reset_epoch

    from .persona_profiles import active_user_id

    uid = active_user_id()
    async with _reset_lock:
        _resetting = True
        _reset_epoch += 1
        stats: dict = {
            "ok": False,
            "userdb_tables": 0,
            "vector": 0,
            "session_msgs": 0,
            "agent_tasks": 0,
            "failures": [],
        }
        try:
            try:
                await _quiesce_user_writers()
            except Exception as exc:
                stats["failures"].append(f"后台任务未安全停止：{exc}")
                logger.warning("[重置] 后台任务未安全停止：{}", exc)
                return stats

            # userdb 全部业务表用单事务清理，避免中途失败留下半张库。
            try:
                # 知识库原文在数据库外；先按当前人格删除文件和 kb 向量。
                from .knowledge import clear_user_documents

                await asyncio.to_thread(clear_user_documents, uid)
                with db._lock:
                    db.conn.execute("BEGIN IMMEDIATE")
                    try:
                        for table in _TABLES:
                            db.conn.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
                        db.conn.commit()
                    except Exception:
                        db.conn.rollback()
                        raise
                stats["userdb_tables"] = len(_TABLES)
                logger.info("[重置] userdb 业务表已清空（{} 张）", len(_TABLES))
            except Exception as exc:
                stats["failures"].append(f"userdb 清空失败：{exc}")
                logger.warning("[重置] userdb 清空失败：{}", exc)

            try:
                from .memory import vector_store as _vec

                stats["vector"] = await asyncio.to_thread(_vec.clear_user, uid)
                logger.info("[重置] 向量库已清空：{}", stats["vector"])
            except Exception as exc:
                stats["failures"].append(f"向量库清空失败：{exc}")
                logger.warning("[重置] 向量库清空失败：{}", exc)

            try:
                from ..session import store as _store

                stats["session_msgs"] = await _store.clear_current()
                logger.info("[重置] 当前会话已清空：{} 条", stats["session_msgs"])
            except Exception as exc:
                stats["failures"].append(f"当前会话清空失败：{exc}")
                logger.warning("[重置] 当前会话清空失败：{}", exc)

            try:
                from ..agent import session as agent_session

                stats["agent_tasks"] = agent_session.clear_user_tasks(uid)
            except Exception as exc:
                stats["failures"].append(f"Agent 任务清空失败：{exc}")
                logger.warning("[重置] Agent 任务清空失败：{}", exc)

            stats["ok"] = not stats["failures"]
            return stats
        finally:
            _resetting = False
