"""数据迁移：把既有 SQLite 记忆（long_memory / facts / triples / user_profile /
important_dates / manager_memories / kv 摘要）批量灌入 Chroma 向量库，保留现有记忆不丢。

运行时机：
- 启动时由 engine.ensure_backfill() 自动触发（检测到 Chroma 为空且 SQLite 有数据）
- 也可命令行手动跑：python -m backend.core.memory.migration

安全设计：
- 只读 SQLite，不删不改源数据
- 分批 embedding + 分批写，失败静默（下次启动补）
- 迁移完成后在 kv 记录游标（向量库已有数据则跳过）
"""
import asyncio
import sqlite3

from ..config import config
from ..log import logger
from ...storage.connect import connect_database

_BATCH = 64


_BATCH = 64
# topic 分区的 topic_memory 偏移 id 起点（与 topic_memory._TOPIC_VEC_ID_BASE 一致）
_TOPIC_VEC_ID_BASE = 1_000_000_000


def _connect_bot(*, row_factory: bool = False, timeout: float = 5.0):
    """连接 bot.db（P1-2：加密模式注入 runtime MK；锁定态抛 DatabaseLockedError）。

    此前三处 connect_database 均不带 key：加密态下首查即抛
    「file is not a database」，_needs_migration 把它当「无需迁移」吞掉——
    存量迁移静默不跑，rebuild 先删后灌失败时连正常向量也一并不复。
    """
    from ...storage import runtime

    return connect_database(
        config.data_dir / "bot.db",
        encrypted_key=runtime.database_key_or_none(),
        timeout=timeout,
        row_factory=row_factory,
    )


def _needs_migration() -> bool:
    """是否还有未灌入向量库的存量记忆（按表精确比较，半途失败可续迁）。"""
    try:
        from . import vector_store as vec

        if not vec.enabled():
            return False
        conn = _connect_bot(row_factory=True)
        # 各源表行数 → 对应向量分区
        table_kinds = {
            "long_memory": "lm",
            "facts": "facts",
            "triples": "triples",
            "user_profile": "profile",
            "important_dates": "topic",
            "manager_memories": "mem",
            "kb_chunks": "kb",
        }
        total = 0
        missing = False
        for table, kind in table_kinds.items():
            where = (
                " WHERE status = 'active' AND surface_policy != 'never_surface' "
                "AND (expires_at IS NULL OR expires_at > strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime'))"
                if table == "facts" else ""
            )
            cnt = conn.execute(f"SELECT COUNT(*) AS c FROM {table}{where}").fetchone()["c"] or 0
            total += cnt
            # P3-16：topic 分区混有 topic_memory 的 1e9+ 偏移向量，整仓计数
            # 会把「日期向量缺失」误判为齐全——按 id 上界精确计数。
            have = (
                vec.count_under(kind, _TOPIC_VEC_ID_BASE)
                if kind == "topic"
                else vec.count(kind)
            )
            if cnt and have < cnt:
                missing = True
        conn.close()
        logger.info("[迁移] 存量记忆 {} 条，{}", total, "存在未灌入分区，需要迁移" if missing else "向量库已齐全")
        return missing
    except Exception as exc:
        logger.debug("[迁移] 迁移判定跳过：{}", exc)
        return False


def _sqlite_rows(table: str) -> list[sqlite3.Row]:
    conn = _connect_bot(timeout=10, row_factory=True)
    where = (
        " WHERE status = 'active' AND surface_policy != 'never_surface' "
        "AND (expires_at IS NULL OR expires_at > strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime'))"
        if table == "facts" else ""
    )
    rows = conn.execute(f"SELECT * FROM {table}{where} ORDER BY id").fetchall()
    conn.close()
    return rows


def migrate(progress_cb=None) -> dict:
    """执行一次性迁移，返回统计 {table: count}。"""
    from . import vector_store as vec

    if not vec.enabled():
        logger.warning("[迁移] Chroma 不可用，跳过迁移（向量检索降级为 TF-IDF）")
        return {"skipped": True}

    # P1-2：加密模式下库锁定（MK 不在内存）时无法读取源表，明确跳过并留痕，
    # 让 rebuild/启动流程知晓「本次重灌没有发生」而不是拿到空统计。
    try:
        _connect_bot().close()
    except Exception as exc:
        logger.warning("[迁移] 数据库当前不可读（锁定态？），迁移跳过：{}", type(exc).__name__)
        return {"skipped": True}

    # 维度锁防线：embedding 模型未就绪时（过渡期哈希回退）不迁移——
    # 哈希向量不但无语义，还会把 collection 维度锁成 768，且写入计数会让
    # _needs_migration 误判「迁移完成」（模型就绪后真迁移被跳过）。
    from . import embedding as _emb

    if not _emb.is_loaded():
        try:
            from ..config import config as _cfg

            if not _cfg.memory_embed_force:
                logger.warning(
                    "[迁移] embedding 模型未就绪，暂缓迁移（待模型预热完成后自动触发）"
                )
                return {"skipped": True}
        except Exception:
            logger.warning("[迁移] embedding 模型未就绪，暂缓迁移")
            return {"skipped": True}

    stats: dict = {"skipped": False}

    # 1) long_memory → lm
    count = _migrate_table("long_memory", "lm", lambda r: r["content"], stats, progress_cb)
    # 2) facts → facts
    count += _migrate_table("facts", "facts", lambda r: r["content"], stats, progress_cb)
    # 3) triples → triples（拼接 主体+谓词+客体）
    count += _migrate_table(
        "triples",
        "triples",
        lambda r: f"{r['subject']} {r['predicate']} {r['object']}",
        stats,
        progress_cb,
    )
    # 4) user_profile → profile
    count += _migrate_table(
        "user_profile",
        "profile",
        lambda r: f"{r['content']}（{r['category']}）",
        stats,
        progress_cb,
    )
    # 5) important_dates → topic（日子算事件记忆，走 topic 分区）
    count += _migrate_table(
        "important_dates",
        "topic",
        lambda r: f"{r['label']}：{r['date']}",
        stats,
        progress_cb,
    )
    # 6) 摘要 → summary（kv_store 的 compact_summary）
    try:
        conn = _connect_bot()
        rows = conn.execute(
            "SELECT user_id, value FROM kv_store WHERE key='compact_summary'"
        ).fetchall()
        conn.close()
        for user_id, val in rows:
            if val:
                vec.add(user_id, "summary", 0, val, _allow_during_rebuild=True)
                stats["summary"] = stats.get("summary", 0) + 1
    except Exception:
        pass

    # 7) manager_memories → mem（P0-1：fallback 管理记忆的权威副本重灌。
    #    此前该分区只在写入时进向量，任何 rebuild 都会把降级期记忆清掉且
    #    无回填路径——现在它与 lm/facts 一样「SQLite 权威、向量可重建」。
    #    向量 rid 必须用内容哈希列 rid（与 _FallbackManager.add 一致），
    #    用自增 id 重灌会让检索权威闸门把全部 mem 向量当孤儿过滤掉。）
    count += _migrate_table(
        "manager_memories", "mem", lambda r: r["content"], stats, progress_cb,
        rid_fn=lambda r: r["rid"],
    )

    # 8) kb_chunks → kb（P1-3：rebuild 删除 kb 分区后原本没有任何重灌路径，
    #    文档列表还在、chunk_count 正常，但召回永远为空——「读过却想不起」。
    #    doc_id/filename 是 recall_knowledge 出参的一部分，必须随行补回。）
    try:
        conn = _connect_bot(row_factory=True)
        kb_rows = conn.execute(
            "SELECT c.id, c.user_id, c.text, d.filename, d.id AS doc_id "
            "FROM kb_chunks c JOIN kb_documents d ON d.id = c.doc_id AND d.user_id = c.user_id "
            "ORDER BY c.id"
        ).fetchall()
        conn.close()
        done_kb = 0
        for r in kb_rows:
            text = r["text"]
            if not text or not text.strip():
                continue
            if vec.add(
                r["user_id"], "kb", r["id"], text,
                extra={"doc_id": r["doc_id"], "filename": r["filename"]},
                _allow_during_rebuild=True,
            ):
                done_kb += 1
        stats["kb_chunks"] = done_kb
        count += done_kb
    except Exception:
        logger.warning("[迁移] kb_chunks 重灌失败（下次启动自动补灌）")

    logger.info("[迁移] 完成，共灌入 {} 条", count)
    return stats


def _migrate_table(table: str, kind: str, text_fn, stats: dict, progress_cb=None,
                   rid_fn=None) -> int:
    from . import vector_store as vec

    rows = _sqlite_rows(table)
    if not rows:
        stats[table] = 0
        return 0
    if rid_fn is None:
        rid_fn = lambda r: r["id"]  # noqa: E731
    done = 0
    for i in range(0, len(rows), _BATCH):
        batch = rows[i : i + _BATCH]
        for r in batch:
            text = text_fn(r)
            if not text or not text.strip():
                continue
            # 直接走 vec.add：其内部 EF 做 embedding（带 _emb_cache 缓存）。
            # 不再先 embed_batch 预判——那样每个文本会被推理两次（预判一次、
            # add 内部 EF 又一次），首轮存量迁移耗时近乎翻倍。
            # _allow_during_rebuild=True：本迁移可能由 rebuild_all 触发（_rebuilding
            # 已置位）。若走默认闸门，重建重灌的每一条都会被 add() 拒绝，导致清库后
            # 重灌 0 条、语义检索静默失效（P1 自锁）。迁移是批量全量重灌写方自身，
            # 不会被重建流程误删，必须放行。
            if vec.add(r["user_id"], kind, rid_fn(r), text, _allow_during_rebuild=True):
                done += 1
        if progress_cb:
            progress_cb(table, min(i + _BATCH, len(rows)), len(rows))
    stats[table] = done
    return done


async def async_migrate() -> dict:
    """异步版本（embedding 走线程池）。"""
    return await asyncio.to_thread(migrate)


if __name__ == "__main__":
    logger.info("开始迁移存量记忆到 Chroma 向量库…")
    stats = migrate()
    logger.info("迁移结果：{}", stats)
