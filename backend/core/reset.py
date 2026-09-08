# -*- coding: utf-8 -*-
"""彻底重置（失忆重开）：让菟菚忘记对这个用户积累的一切，回到「初识」全新态。

仅用于用户明确选择「重新开始」时调用。范围：
- userdb（bot.db）：好感度/昵称/恋人确认、messages、long_memory、facts、triples、
  important_dates、stickers、user_profile、user_terms、user_style_map、diary、research_reports、
  affection_log、mood_log、activities、activity_notes、tasks、user_meta、kv_store（含 greeting 的 web_last_seen、
  initiative 的 daily 标记等）→ 全部清空。
- 向量库（memory v2）：按 user 前缀的语义向量 → 全清。
- Mem0 向量库（data/chroma_mem0，独立目录）：按 user_id 的管理记忆 → 全清。
- 当前会话（sessions.db 'current'）：气泡清空、标题复位为「新会话」。
- 用户图片：本次删除行引用、且删除后不再被任何行引用的 imgs 文件 → 删除。
- 物理层：两个库 checkpoint + VACUUM，避免旧内容留在空闲页/WAL 里。

默认不动的数据：archives（用户主动存的会话档案保留，可由侧栏归档区查看/删除）。
`deep=True` 时额外删除当前人格的全部归档、本机全部备份（data/backups）与语音缓存
（data/tts_cache）；备份与缓存不按人格拆分，属于整机级删除。

实现走「按表 DELETE」而非删 bot.db 文件重建：避免删除动作与正在打开的连接/WAL
冲突（reset() 的降级分支正是这个做法），且无需担心文件被占用。
"""
from __future__ import annotations

import asyncio
import shutil
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from .config import config
from .log import logger
from .userdb import db

# userdb 中需要清空的全部业务表（与 userdb.reset() 降级分支保持一致）
_TABLES = (
    "affection_log", "mood_log", "long_memory", "facts", "user_meta", "messages",
    "users", "kv_store", "important_dates", "stickers",
    "user_profile", "user_terms", "user_style_map", "diary", "research_reports", "triples",
    "tasks", "promises", "usage_log", "activity_notes", "activities",
    "activity_viewpoints", "activity_goals", "goal_progress",
    "activity_writings", "writing_turns", "activity_lists", "list_items",
    "relationship_events", "artifacts", "context_lifecycle",
    "character_life_events", "relationship_dimension_ledger", "relationship_style_evidence",
    "memory_policy", "memory_annotations", "first_occurrences",
    "user_preferences", "event_chains",
    "reunion_arcs",
    "knowledge_opinion_sources", "knowledge_opinions",
    "pending_thoughts", "future_letters", "relationship_snapshots", "dual_perspectives",
    "relationship_versions", "greeting_variant_usage", "open_questions",
    "companion_requests", "thought_context_receipts", "humor_usage", "source_links",
    "wrapup_outbox", "reading_segments", "reading_bookmarks",
    "activity_draft_receipts",
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


# ---- 媒体文件清扫：只删「本次被删行引用、删除后无人引用」的图片 ----

def _media_dir() -> Path:
    return config.data_dir / "imgs"


def _media_candidates() -> set[str]:
    """imgs 目录里现存的文件名集合（候选，只有落在其中的才可能被删）。"""
    directory = _media_dir()
    if not directory.is_dir():
        return set()
    try:
        return {p.name for p in directory.iterdir() if p.is_file()}
    except OSError:
        return set()


def _names_in(value: str, candidates: set[str]) -> set[str]:
    return {name for name in candidates if name in value}


def _collect_user_media(
    uid: str, session_id: str, persona_id: str, deep: bool, candidates: set[str]
) -> set[str]:
    """删除前记录：这个用户的哪些图片可能失去全部引用。

    图片引用分散在 bot.db（messages/stickers）与 sessions.db（气泡 image、归档
    messages_json），所以两边都要扫；只有「本次会删掉的行」引用的文件才进候选。
    """
    if not candidates:
        return set()
    names: set[str] = set()
    for db_name in ("bot.db", "sessions.db"):
        path = config.data_dir / db_name
        if not path.exists():
            continue
        try:
            conn = sqlite3.connect(str(path), timeout=10.0)
        except sqlite3.Error:
            logger.warning("[重置] {} 打开失败，图片引用记录不完整", db_name)
            continue
        try:
            if db_name == "bot.db":
                queries = (
                    ("SELECT * FROM messages WHERE user_id=?", (uid,)),
                    ("SELECT * FROM stickers WHERE user_id=?", (uid,)),
                )
            else:
                queries = [("SELECT * FROM messages WHERE session_id=?", (session_id,))]
                if deep:
                    queries.append(
                        ("SELECT messages_json FROM archives WHERE persona_id=?", (persona_id,))
                    )
            for sql, params in queries:
                try:
                    rows = conn.execute(sql, params).fetchall()
                except sqlite3.Error:
                    # 表不存在/列不匹配时跳过：图片引用不是重置的强依赖
                    continue
                for row in rows:
                    for value in row:
                        if isinstance(value, str) and value:
                            names |= _names_in(value, candidates)
        finally:
            conn.close()
    return names


def _referenced_media_names(candidates: set[str], conns: list) -> set[str]:
    """扫描各库全部文本列，收集仍被引用的文件名（保住他人仍在用的素材）。"""
    pending = set(candidates)
    found: set[str] = set()
    if not pending:
        return found
    for conn in conns:
        if not pending:
            break
        try:
            tables = [
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master"
                    " WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
        except sqlite3.Error:
            continue
        for table in tables:
            if not pending:
                break
            try:
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            except sqlite3.Error:
                continue
            for row in rows:
                for value in row:
                    if not isinstance(value, str) or not value:
                        continue
                    hit = _names_in(value, pending)
                    if hit:
                        found |= hit
                        pending -= hit
                        if not pending:
                            break
                if not pending:
                    break
    return found


def _sweep_media(names: set[str]) -> int:
    """删除给定的图片文件，返回实际删除数。"""
    if not names:
        return 0
    directory = _media_dir()
    removed = 0
    for name in sorted(names):
        path = directory / name
        try:
            if path.is_file():
                path.unlink()
                removed += 1
        except OSError:
            logger.warning("[重置] 图片删除失败：{}", path)
    return removed


def _sweep_user_media(user_names: set[str]) -> int:
    """删除用户图片里「已无人引用」的部分（其余行的引用一律保留）。

    在工作线程里执行，因此另开连接而不是复用 db.conn（后者由事件循环持有）。
    """
    if not user_names:
        return 0
    conns: list = []
    try:
        for name in ("bot.db", "sessions.db"):
            path = config.data_dir / name
            if not path.exists():
                continue
            try:
                conns.append(sqlite3.connect(str(path), timeout=10.0))
            except sqlite3.Error:
                logger.warning("[重置] {} 打开失败，图片引用只按其余库判定", name)
        if not conns:
            return 0
        still_referenced = _referenced_media_names(user_names, conns)
    finally:
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:
                pass
    return _sweep_media(user_names - still_referenced)


def _clear_tts_cache() -> int:
    """清空语音缓存目录（可再生成），返回删除文件数。"""
    directory = config.data_dir / "tts_cache"
    if not directory.is_dir():
        return 0
    removed = 0
    for path in directory.iterdir():
        try:
            if path.is_file():
                path.unlink()
                removed += 1
        except OSError:
            logger.warning("[重置] 语音缓存删除失败：{}", path)
    return removed


def _delete_backups() -> int:
    """删除本机全部备份目录（整机级，不按人格拆分），返回删除条目数。"""
    root = config.data_dir / "backups"
    if not root.is_dir():
        return 0
    removed = 0
    for entry in root.iterdir():
        try:
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            removed += 1
        except OSError:
            logger.warning("[重置] 备份删除失败：{}", entry)
    return removed


def _compact() -> str:
    """checkpoint + VACUUM：DELETE 只回收逻辑页，物理残留要靠重写库文件清掉。"""
    parts: list[str] = []
    for path in (config.data_dir / "bot.db", config.data_dir / "sessions.db"):
        if not path.exists():
            continue
        try:
            conn = sqlite3.connect(str(path), timeout=30)
            try:
                conn.execute("PRAGMA busy_timeout=30000")
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.execute("VACUUM")
            finally:
                conn.close()
            parts.append(f"{path.name}=ok")
        except sqlite3.Error as exc:
            # 物理回收是尽力而为：失败不改判「用户数据是否已清空」。
            logger.warning("[重置] {} 物理回收失败：{}", path.name, exc)
            parts.append(f"{path.name}=failed")
    return ",".join(parts)


async def reset_everything(*, deep: bool = False) -> dict:
    """执行彻底失忆重置，返回逐步骤状态；任何失败都明确返回 ``ok=False``。

    ``deep=True`` 额外删除当前人格的归档、本机全部备份与语音缓存（整机级）。
    """
    global _resetting, _reset_epoch

    from .persona_profiles import active_id, active_user_id, session_storage_id

    uid = active_user_id()
    active_persona = active_id()
    session_id = session_storage_id("current")
    async with _reset_lock:
        _resetting = True
        _reset_epoch += 1
        stats: dict = {
            "ok": False,
            "deep": deep,
            "userdb_tables": 0,
            "vector": 0,
            "mem0": 0,
            "media": 0,
            "archives": 0,
            "backups": 0,
            "tts_cache": 0,
            "compact": "",
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

            # 图片清扫要在删除前记录引用：删完再扫就分不清哪些文件是本用户的。
            media_candidates = await asyncio.to_thread(_media_candidates)
            user_media = await asyncio.to_thread(
                _collect_user_media, uid, session_id, active_persona, deep, media_candidates
            )

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
                        # P1-04 任务认领记录按人格作用域清理（job_runs 无 user_id 列）
                        db.conn.execute(
                            "DELETE FROM job_runs WHERE scope_key=?",
                            (f"persona::{active_persona}",),
                        )
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

            # Mem0 是独立向量库（data/chroma_mem0），不归 vector_store 管；
            # 漏掉它会让重置后的长期记忆召回继续命中旧记忆。
            try:
                from .memory.memory_manager import manager as memory_manager

                stats["mem0"] = await asyncio.to_thread(memory_manager.clear_user, uid)
                logger.info("[重置] Mem0 管理记忆已清空：{}", stats["mem0"])
            except Exception as exc:
                stats["failures"].append(f"Mem0 管理记忆清空失败：{exc}")
                logger.warning("[重置] Mem0 管理记忆清空失败：{}", exc)

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

            if deep:
                try:
                    from ..session import store as _store

                    stats["archives"] = await _store.delete_archives(active_persona)
                    logger.info("[重置] 归档已清空：{} 份", stats["archives"])
                except Exception as exc:
                    stats["failures"].append(f"归档清空失败：{exc}")
                    logger.warning("[重置] 归档清空失败：{}", exc)

                try:
                    stats["tts_cache"] = await asyncio.to_thread(_clear_tts_cache)
                    stats["backups"] = await asyncio.to_thread(_delete_backups)
                    logger.info(
                        "[重置] 深度清除：语音缓存 {} 个、备份 {} 项",
                        stats["tts_cache"], stats["backups"],
                    )
                except Exception as exc:
                    stats["failures"].append(f"缓存/备份清除失败：{exc}")
                    logger.warning("[重置] 缓存/备份清除失败：{}", exc)

            # 图片：只删「本次删掉的行引用过、且现在无人引用」的文件
            try:
                stats["media"] = await asyncio.to_thread(_sweep_user_media, user_media)
            except Exception as exc:
                stats["failures"].append(f"图片清扫失败：{exc}")
                logger.warning("[重置] 图片清扫失败：{}", exc)

            # 物理回收放最后：把 DELETE 留下的空闲页与 WAL 一起清掉
            stats["compact"] = await asyncio.to_thread(_compact)

            stats["ok"] = not stats["failures"]
            return stats
        finally:
            _resetting = False
