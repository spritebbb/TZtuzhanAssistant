# -*- coding: utf-8 -*-
"""38项#6 孤儿向量定期补灌：运行期 embedding 未就绪被 add() 拒写的行，
不再等下次重启 migration——维护循环定期按表计数补齐。

覆盖 lm（long_memory）与 facts 两个 record_id=int 主键的分区；topic（id 有
1e9 偏移）/mem（rid 为 md5）语义不同，V1 不碰。检测走 vector_store.missing_ids
批量查，缺口逐条 add()（upsert 语义，已有条目幂等无害）。
"""
from __future__ import annotations

from ..core.log import logger

_KIND_TABLES = {"lm": "long_memory", "facts": "facts"}


def backfill_user(user_id: str, *, kinds: tuple[str, ...] = ("lm", "facts")) -> int:
    """补齐单用户两个主分区的缺失向量，返回补灌条数。全程 fail-soft。"""
    from ..core.memory import vector_store as vs
    from ..core.userdb import db

    if not vs.enabled() or vs._rebuilding or not vs._embedding_ready_for_write():
        return 0
    total = 0
    try:
        for kind in kinds:
            table = _KIND_TABLES[kind]
            rows = db.conn.execute(
                f"SELECT id, content FROM {table} WHERE user_id=? ORDER BY id",
                (user_id,),
            ).fetchall()
            if not rows:
                continue
            miss = vs.missing_ids(user_id, kind, [r["id"] for r in rows])
            if not miss:
                continue
            by_id = {r["id"]: r["content"] for r in rows}
            n = 0
            for rid in miss:
                content = str(by_id.get(rid) or "").strip()
                if content and vs.add(user_id, kind, rid, content):
                    n += 1
            if n:
                logger.info("[向量补灌] {} {} 分区补 {} 条（缺口 {}）", user_id, kind, n, len(miss))
            total += n
    except Exception:
        logger.exception("[向量补灌] {} 失败（下一轮再试）", user_id)
    return total


def backfill_all() -> int:
    """遍历有记忆行的用户补灌；返回总条数（0=无缺口或前置不满足）。"""
    from ..core.userdb import db

    try:
        rows = db.conn.execute(
            "SELECT DISTINCT user_id FROM long_memory UNION SELECT DISTINCT user_id FROM facts"
        ).fetchall()
    except Exception:
        logger.exception("[向量补灌] 用户列表查询失败")
        return 0
    total = 0
    for r in rows:
        total += backfill_user(str(r["user_id"]))
    return total
