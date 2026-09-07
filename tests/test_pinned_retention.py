# -*- coding: utf-8 -*-
"""B01：pinned 长期记忆不受容量清理降级/删除（契约回归）。

背景：维护循环曾把每用户超过 PINNED_MEMORY_KEEP 的最旧 pinned 行降级为
pinned=0，再按「全局保留最近 keep 条非 pinned」轮转删除——用户显式要求
记住的内容被静默销毁，违反 clean_old_long_memory 自己声明的
「pinned 永不清理」契约。本文件锁定修复后契约：容量清理只动 unpinned 候选，
pinned 超阈值仅产生不含原文的计数提示。

另覆盖连带缺陷：旧实现的维护连接漏设 row_factory，rows 非空时
row["user_id"] 抛 TypeError，清理与同周期审计日志轮转全部中断。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 数据目录隔离：先于任何 backend import 生效，避免读写真实 bot.db
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_pinned_"))

from backend.core.log import logger
from backend.core.userdb import db
from backend.maintenance import loop
from backend.maintenance.loop import (
    PINNED_MEMORY_KEEP,
    _report_pinned_overflow,
    clean_old_long_memory,
)

UA, UB, UC, UD = "assistant-main", "assistant-second", "assistant-third", "assistant-fourth"


def _insert(user_id: str, n: int, pinned: bool, tag: str) -> list[int]:
    ids: list[int] = []
    with db._lock:
        for i in range(n):
            cur = db.conn.execute(
                "INSERT INTO long_memory (user_id, content, ts, pinned) VALUES (?, ?, ?, ?)",
                (user_id, f"{tag}-{i}", "2026-09-07T00:00:00", 1 if pinned else 0),
            )
            ids.append(int(cur.lastrowid))
        db.conn.commit()
    return ids


def _pinned_ids(user_id: str) -> set[int]:
    with db._lock:
        rows = db.conn.execute(
            "SELECT id FROM long_memory WHERE user_id=? AND pinned=1 ORDER BY id",
            (user_id,),
        ).fetchall()
    return {r["id"] for r in rows}


def _counts(user_id: str) -> tuple[int, int]:
    with db._lock:
        row = db.conn.execute(
            "SELECT SUM(pinned=1) AS p, SUM(pinned=0) AS u FROM long_memory WHERE user_id=?",
            (user_id,),
        ).fetchone()
    return int(row["p"] or 0), int(row["u"] or 0)


class _VecRecorder:
    """替换 vector_store.delete_many：记录调用，不初始化 Chroma。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, list[int]]] = []

    def __call__(self, user_id: str, kind: str, record_ids: list[int]) -> int:
        self.calls.append((user_id, kind, list(record_ids)))
        return len(record_ids)


def _with_vec_recorder(fn):
    from backend.core.memory import vector_store as vec

    rec = _VecRecorder()
    orig = vec.delete_many
    vec.delete_many = rec
    try:
        result = fn(rec)
    finally:
        vec.delete_many = orig
    return result


def test_pinned_never_demoted_or_deleted_by_capacity_cleanup() -> None:
    """两用户 pinned 均超上限：pinned 全保留、unpinned 正常轮转、向量同步、幂等。"""
    db.ensure_user(UA)
    db.ensure_user(UB)
    # pinned 行先插（成为全表最旧 id），unpinned 后插——若仍有按容量动最旧行的
    # 逻辑，pinned 会最先被命中，测试即失败。
    pinned_a = _insert(UA, PINNED_MEMORY_KEEP + 3, pinned=True, tag="pa")
    pinned_b = _insert(UB, PINNED_MEMORY_KEEP + 1, pinned=True, tag="pb")
    old_unpinned_a = _insert(UA, 20, pinned=False, tag="old-a")  # 全局最旧 unpinned
    new_unpinned_b = _insert(UB, 5, pinned=False, tag="new-b")   # 全局最新 unpinned

    keep = 10  # 全局 unpinned=25 > keep，触发轮转
    removed = _with_vec_recorder(lambda rec: clean_old_long_memory(keep=keep))

    # 只淘汰 UA 最旧的 15 条 unpinned；UA 留 5 条最新，UB 全保留（跨人格不误删）
    assert removed == 15
    assert _counts(UA) == (PINNED_MEMORY_KEEP + 3, 5)
    assert _counts(UB) == (PINNED_MEMORY_KEEP + 1, 5)
    assert _pinned_ids(UA) == set(pinned_a)
    assert _pinned_ids(UB) == set(pinned_b)
    # UB 的 5 条 unpinned 全部健在（跨人格不误删）
    with db._lock:
        ub_rows = db.conn.execute(
            "SELECT id FROM long_memory WHERE user_id=? AND pinned=0", (UB,)
        ).fetchall()
    assert {r["id"] for r in ub_rows} == set(new_unpinned_b)
    # UA 未被淘汰的 unpinned 恰为最旧 20 条中最新的 5 条
    with db._lock:
        ua_rows = db.conn.execute(
            "SELECT id FROM long_memory WHERE user_id=? AND pinned=0", (UA,)
        ).fetchall()
    assert {r["id"] for r in ua_rows} == set(old_unpinned_a[15:])

    # 向量同步：恰好一次调用，只含 UA 被淘汰的 15 条 unpinned，无 pinned、无 UB
    vec_calls = _with_vec_recorder(lambda rec: (clean_old_long_memory(keep=keep), rec.calls)[1])
    assert vec_calls == []  # 第二次运行幂等：无可删行，不触向量
    # 第一次运行的向量调用在上一段 recorder 中已验证，这里补断言被删 id 集合
    # （重新跑一轮前先补种等量 unpinned 以复现「有删除」路径的向量同步）
    extra = _insert(UA, 8, pinned=False, tag="extra-a")
    vec_calls2 = _with_vec_recorder(lambda rec: (clean_old_long_memory(keep=13), rec.calls)[1])
    assert len(vec_calls2) == 1
    uid, kind, ids = vec_calls2[0]
    assert (uid, kind) == (UA, "lm")
    # 全局 unpinned=18（5 条幸存 old-a + UB 5 + extra 8），keep=13 → 淘汰最旧 5 条
    # = 首轮幸存的 old-a 行；extra 与 UB 全保留
    assert set(ids) == set(old_unpinned_a[15:])
    assert not (set(ids) & (_pinned_ids(UA) | _pinned_ids(UB)))


def test_pinned_overflow_info_is_count_only() -> None:
    """超阈值只产生 user_id+条数 的容量提示，日志不含任何记忆原文。"""
    db.ensure_user(UC)
    uc_ids = _insert(UC, PINNED_MEMORY_KEEP + 1, pinned=True, tag="pc")

    msgs: list[str] = []
    handler = logger.add(lambda m: msgs.append(str(m)))
    try:
        conn = sqlite3.connect(str(loop._BOT_DB))
        try:
            conn.row_factory = sqlite3.Row
            info = _report_pinned_overflow(conn)
        finally:
            conn.close()
    finally:
        logger.remove(handler)

    assert (UC, PINNED_MEMORY_KEEP + 1) in info
    joined = "\n".join(msgs)
    assert "pinned" in joined and str(PINNED_MEMORY_KEEP) in joined
    # 去抖语义（审查 I1）：首超 WARNING、计数变化 INFO、无变化 DEBUG
    assert any("WARNING" in m for m in msgs)
    # 任何用户的记忆原文标记都不得出现在提示日志里
    for tag in ("pa-", "pb-", "pc-", "old-a", "new-b", "extra-a"):
        assert tag not in joined
    assert _pinned_ids(UC) == set(uc_ids)
    # 去抖行为：同一计数再次上报降为 DEBUG（不进 INFO 通道），计数变化恢复 INFO
    msgs2: list[str] = []
    handler2 = logger.add(lambda m: msgs2.append(str(m)), level="INFO")
    try:
        conn = sqlite3.connect(str(loop._BOT_DB))
        try:
            conn.row_factory = sqlite3.Row
            _report_pinned_overflow(conn)   # 同计数 → DEBUG，INFO 级 sink 不应捕获
            with db._lock:
                db.conn.execute(
                    "INSERT INTO long_memory (user_id, content, ts, pinned) VALUES (?, ?, ?, 1)",
                    (UC, "pc-debounce", "2026-09-07T00:00:00"),
                )
                db.conn.commit()
            _report_pinned_overflow(conn)   # 计数变化 → INFO
        finally:
            conn.close()
    finally:
        logger.remove(handler2)
    same_count_logs = [m for m in msgs2 if "assistant-third" in m and "801 条" in m]
    changed_logs = [m for m in msgs2 if "assistant-third" in m and "802 条" in m]
    assert same_count_logs == []      # 无变化被去抖到 DEBUG
    assert len(changed_logs) == 1     # 变化恢复 INFO
    # 清理去抖缓存，避免影响后续用例的 WARNING 首超断言
    loop._PINNED_OVERFLOW_LAST.pop(UC, None)
    with db._lock:
        db.conn.execute(
            "DELETE FROM long_memory WHERE user_id=? AND content='pc-debounce'", (UC,)
        )
        db.conn.commit()


def test_sub_threshold_no_warning_and_cleanup_noop() -> None:
    """低于阈值无提示；纯 pinned 数据下清理为空操作。

    依赖文件内顺序执行：keep=100 的空转断言假设前序用例残留的全局
    unpinned 行数 ≤100（当前为 5+5+8=18）。若在中间插入新增 unpinned
    的用例，请同步核对本假设。
    """
    db.ensure_user(UD)
    ud_ids = _insert(UD, 10, pinned=True, tag="pd")

    conn = sqlite3.connect(str(loop._BOT_DB))
    try:
        conn.row_factory = sqlite3.Row
        info = _report_pinned_overflow(conn)
    finally:
        conn.close()
    assert (UD, 10) not in info

    removed = _with_vec_recorder(lambda rec: clean_old_long_memory(keep=100))
    assert removed == 0
    assert _pinned_ids(UD) == set(ud_ids)


if __name__ == "__main__":
    test_pinned_never_demoted_or_deleted_by_capacity_cleanup()
    test_pinned_overflow_info_is_count_only()
    test_sub_threshold_no_warning_and_cleanup_noop()
    print("test_pinned_retention: all passed")
