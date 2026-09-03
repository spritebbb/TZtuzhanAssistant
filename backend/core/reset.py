# -*- coding: utf-8 -*-
"""彻底重置（失忆重开）：让菟菚忘记对这个用户积累的一切，回到「初识」全新态。

仅用于用户明确选择「重新开始」时调用。范围：
- userdb（bot.db）：好感度/昵称/恋人确认、messages、long_memory、facts、triples、
  important_dates、stickers、user_profile、user_terms、user_style_map、diary、
  affection_log、tasks、user_meta、kv_store（含 greeting 的 web_last_seen、
  initiative 的 daily 标记等）→ 全部清空。
- 向量库（memory v2）：按 user 前缀的语义向量 → 全清。
- 当前会话（sessions.db 'current'）：气泡清空、标题复位为「新会话」。

不动的数据：archives（用户主动存的会话档案保留，可由侧栏归档区查看/删除）。

实现走「按表 DELETE」而非删 bot.db 文件重建：避免删除动作与正在打开的连接/WAL
冲突（reset() 的降级分支正是这个做法），且无需担心文件被占用。
"""
from __future__ import annotations

from .log import logger
from .userdb import db

# userdb 中需要清空的全部业务表（与 userdb.reset() 降级分支保持一致）
_TABLES = (
    "affection_log", "long_memory", "facts", "user_meta", "messages",
    "users", "kv_store", "important_dates", "stickers",
    "user_profile", "user_terms", "user_style_map", "diary", "triples",
    "tasks",
)


async def reset_everything() -> dict:
    """执行彻底失忆重置，返回清理统计。所有子步骤失败均不阻断主流程。"""
    from .memory import vector_store as _vec

    stats: dict = {"userdb_tables": 0, "vector": 0, "session_msgs": 0}

    # 1) 清 userdb 业务表
    try:
        with db._lock:
            for table in _TABLES:
                db.conn.execute(f"DELETE FROM {table}")
            db.conn.commit()
        stats["userdb_tables"] = len(_TABLES)
        logger.info("[重置] userdb 业务表已清空（%d 张）", len(_TABLES))
    except Exception as e:
        logger.warning("[重置] userdb 清空失败: %s", e)

    # 2) 清向量库（memory v2）
    try:
        stats["vector"] = _vec.clear()
        logger.info("[重置] 向量库已清空: %d", stats["vector"])
    except Exception as e:
        logger.warning("[重置] 向量库清空失败: %s", e)

    # 3) 清空当前会话气泡（不归档）
    try:
        from ..session import store as _store

        stats["session_msgs"] = await _store.clear_current()
        logger.info("[重置] 当前会话已清空: %d 条", stats["session_msgs"])
    except Exception as e:
        logger.warning("[重置] 当前会话清空失败: %s", e)

    return stats
