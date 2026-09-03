"""数据迁移：把既有 SQLite 记忆（long_memory / facts / triples / user_profile /
important_dates / kv 摘要）批量灌入 Chroma 向量库，保留现有记忆不丢。

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

_BATCH = 64


def _needs_migration() -> bool:
    """是否还有未灌入向量库的存量记忆（按表精确比较，半途失败可续迁）。"""
    try:
        from . import vector_store as vec

        if not vec.enabled():
            return False
        conn = sqlite3.connect(config.data_dir / "bot.db")
        conn.row_factory = sqlite3.Row
        # 各源表行数 → 对应向量分区
        table_kinds = {
            "long_memory": "lm",
            "facts": "facts",
            "triples": "triples",
            "user_profile": "profile",
            "important_dates": "topic",
        }
        total = 0
        missing = False
        for table, kind in table_kinds.items():
            cnt = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"] or 0
            total += cnt
            if cnt and vec.count(kind) < cnt:
                missing = True
        conn.close()
        logger.info("[迁移] 存量记忆 {} 条，{}", total, "存在未灌入分区，需要迁移" if missing else "向量库已齐全")
        return missing
    except Exception:
        return False


def _sqlite_rows(table: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(config.data_dir / "bot.db", timeout=10)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
    conn.close()
    return rows


def migrate(progress_cb=None) -> dict:
    """执行一次性迁移，返回统计 {table: count}。"""
    from . import vector_store as vec

    if not vec.enabled():
        logger.warning("[迁移] Chroma 不可用，跳过迁移（向量检索降级为 TF-IDF）")
        return {"skipped": True}

    stats: dict = {"skipped": False}

    # 1) long_memory → lm
    count = _migrate_table("long_memory", "lm", lambda r: r["content"], stats)
    # 2) facts → facts
    count += _migrate_table("facts", "facts", lambda r: r["content"], stats)
    # 3) triples → triples（拼接 主体+谓词+客体）
    count += _migrate_table(
        "triples",
        "triples",
        lambda r: f"{r['subject']} {r['predicate']} {r['object']}",
        stats,
    )
    # 4) user_profile → profile
    count += _migrate_table(
        "user_profile",
        "profile",
        lambda r: f"{r['content']}（{r['category']}）",
        stats,
    )
    # 5) important_dates → topic（日子算事件记忆，走 topic 分区）
    count += _migrate_table(
        "important_dates",
        "topic",
        lambda r: f"{r['label']}：{r['date']}",
        stats,
    )
    # 6) 摘要 → summary（kv_store 的 compact_summary）
    try:
        conn = sqlite3.connect(config.data_dir / "bot.db")
        rows = conn.execute(
            "SELECT user_id, value FROM kv_store WHERE key='compact_summary'"
        ).fetchall()
        conn.close()
        for user_id, val in rows:
            if val:
                vec.add(user_id, "summary", 0, val)
                stats["summary"] = stats.get("summary", 0) + 1
    except Exception:
        pass

    logger.info("[迁移] 完成，共灌入 {} 条", count)
    return stats


def _migrate_table(table: str, kind: str, text_fn, stats: dict, progress_cb=None) -> int:
    from . import vector_store as vec
    from .embedding import embed_batch

    rows = _sqlite_rows(table)
    if not rows:
        stats[table] = 0
        return 0
    done = 0
    for i in range(0, len(rows), _BATCH):
        batch = rows[i : i + _BATCH]
        texts = [text_fn(r) for r in batch]
        vecs = embed_batch(texts)
        if not vecs:
            continue
        for r, text, v in zip(batch, texts, vecs):
            if not text or not text.strip():
                continue
            ok = vec.add(r["user_id"], kind, r["id"], text)
            if ok:
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
