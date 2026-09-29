# -*- coding: utf-8 -*-
"""P3-29 向量召回权威闸门：lm/kb 孤儿向量不再复活 + memory_search 分池防回错表。

回归锚点（docs/BUG-HUNT-2026-09-25.md P3-29 + 第二轮跨分区 id 碰撞）：
- 删除失败留下的孤儿向量被召回（已删对话原文/文档片段「复活」）→ 闸门丢弃并惰性清理；
- 闸门查库异常 fail-open（沿 mem 闸门取舍：只防孤儿，不阻断召回）；
- memory_search 跨分区 id 碰撞回错表 → 按 kind 分池，各查各的权威表；
- facts 分区既有闸门（recallable_fact_ids）回归不破坏。

假向量层：不跑真实 embedding/Chroma，search 返回预置 SearchHit（可含 SQLite 已无
对应行的「孤儿」），delete_many 记录调用供断言惰性清理确实发生。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_p329_"))

from backend.core import userdb
from backend.core.knowledge import recall_knowledge
from backend.core.memory import long_term as lt
from backend.core.memory import vector_store as vec
from backend.core.userdb import db
from backend.tools.builtin.memory import _pool_gated_hits
from backend.core.memory.vector_store import SearchHit

config = lt.config
config.memory_v2 = True
config.kb_enabled = True

# ---- 假向量层 ----

_FAKE_HITS: list[SearchHit] = []
_DELETED: list[tuple[str, str, list[int]]] = []


def _fake_search(user_id: str, query: str, top_k: int = 5, kind: str | None = None) -> list[SearchHit]:
    return [h for h in _FAKE_HITS if kind is None or h.meta.get("kind") == kind][:top_k]


def _fake_delete_many(user_id: str, kind: str, record_ids: list[int]) -> int:
    _DELETED.append((user_id, kind, list(record_ids)))
    return len(record_ids)


vec.search = _fake_search
vec.delete_many = _fake_delete_many


def _reset(fake_hits: list[SearchHit]) -> None:
    _FAKE_HITS[:] = fake_hits
    _DELETED.clear()


def _long_memory(uid: str, rid: int, content: str) -> None:
    with db._lock:
        db.conn.execute(
            "INSERT INTO long_memory (id, user_id, content, ts, pinned) VALUES (?, ?, ?, ?, 0)",
            (rid, uid, content, "2026-09-29T10:00:00"),
        )
        db.conn.commit()


def _kb(uid: str, chunk_id: int, doc_id: int, text: str, *, with_doc: bool = True) -> None:
    with db._lock:
        if with_doc:
            db.conn.execute(
                "INSERT INTO kb_documents (id, user_id, filename, stored_path, format, size_bytes, chunk_count, ts) "
                "VALUES (?, ?, '笔记.md', '', 'md', 10, 1, '2026-09-29T10:00:00')",
                (doc_id, uid),
            )
        db.conn.execute(
            "INSERT INTO kb_chunks (id, user_id, doc_id, seq, text, ts) VALUES (?, ?, ?, 0, ?, '2026-09-29T10:00:00')",
            (chunk_id, uid, doc_id, text),
        )
        db.conn.commit()


def test_lm_orphan_gate() -> int:
    uid = "p329-lm"
    db.ensure_user(uid)
    _long_memory(uid, 101, "温室手记读到第三章，主角发现了夹层")
    _reset([
        SearchHit(record_id=101, distance=0.2, text="温室手记读到第三章，主角发现了夹层", meta={"kind": "lm"}),
        SearchHit(record_id=999, distance=0.3, text="已删对话里的原话不该复活", meta={"kind": "lm"}),
    ])
    got = asyncio.run(lt.recall(uid, "温室手记"))
    assert any("第三章" in t for t in got), got
    assert not any("不该复活" in t for t in got), "纯孤儿（SQLite 无行）必须被闸门丢弃"
    assert any(999 in ids for _, kind, ids in _DELETED if kind == "lm"), _DELETED

    # 删掉 SQLite 行但向量仍在（模拟 delete 失败留孤儿）→ 命中被丢弃且被惰性清理
    with db._lock:
        db.conn.execute("DELETE FROM long_memory WHERE id=101 AND user_id=?", (uid,))
        db.conn.commit()
    _DELETED.clear()
    got = asyncio.run(lt.recall(uid, "温室手记"))
    assert not any("第三章" in t for t in got), "已删对话原文不得经向量召回复活"
    assert any(101 in ids for _, kind, ids in _DELETED if kind == "lm"), "闸门应惰性清理孤儿向量"
    # 语境融合路径（_with_expansion）同闸门
    got = asyncio.run(lt._with_expansion(uid, "温室手记", kind="lm", mock=True))
    assert not any("第三章" in t for t in got), got
    print("[OK] lm 闸门：纯孤儿与删除残留均被丢弃并惰性清理")
    return 0


def test_lm_gate_fail_open() -> int:
    uid = "p329-failopen"
    db.ensure_user(uid)
    _reset([SearchHit(record_id=201, distance=0.2, text="闸门故障时仍可召回的原文", meta={"kind": "lm"})])
    orig = userdb.UserDB.recallable_long_memory_ids
    userdb.UserDB.recallable_long_memory_ids = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down"))
    try:
        got = asyncio.run(lt.recall(uid, "任意"))
    finally:
        userdb.UserDB.recallable_long_memory_ids = orig
    assert any("仍可召回" in t for t in got), "fail-open：闸门故障不得阻断召回"
    assert _DELETED == [], "fail-open 时不得误判清理"
    print("[OK] lm 闸门 fail-open：查库异常放行且不清理")
    return 0


def test_kb_orphan_gate() -> int:
    uid = "p329-kb"
    db.ensure_user(uid)
    _kb(uid, chunk_id=7, doc_id=3, text="菟丝子是全寄生植物，靠吸器从寄主获取养分")
    _reset([
        SearchHit(record_id=7, distance=0.2, text="菟丝子是全寄生植物，靠吸器从寄主获取养分",
                  meta={"kind": "kb", "doc_id": 3, "filename": "笔记.md"}),
        SearchHit(record_id=8, distance=0.3, text="已删文档片段不该复活", meta={"kind": "kb"}),
    ])
    got = recall_knowledge(uid, "菟丝子")
    assert [g["text"] for g in got] and "菟丝子" in got[0]["text"], got
    assert not any("不该复活" in g["text"] for g in got), "纯孤儿 chunk 必须被闸门丢弃"
    assert any(8 in ids for _, kind, ids in _DELETED if kind == "kb"), _DELETED

    # 文档删除但 chunk 残留（JOIN 落空）→ 同样丢弃
    with db._lock:
        db.conn.execute("DELETE FROM kb_documents WHERE id=3 AND user_id=?", (uid,))
        db.conn.commit()
    _DELETED.clear()
    got = recall_knowledge(uid, "菟丝子")
    assert got == [], "所属文档已删的 chunk 残留向量必须被丢弃"
    assert any(7 in ids for _, kind, ids in _DELETED if kind == "kb"), _DELETED
    print("[OK] kb 闸门：孤儿 chunk 与文档残留均被丢弃并惰性清理")
    return 0


def test_memory_search_pool_split() -> int:
    uid = "p329-pool"
    db.ensure_user(uid)
    _long_memory(uid, 5, "真的是长记忆的原文")
    with db._lock:
        db.conn.execute(
            "INSERT INTO facts (id, user_id, content, ts, source_type, confidence) "
            "VALUES (5, ?, '用户喜欢菟丝子', '2026-09-29T10:00:00', 'conversation', 1.0)",
            (uid,),
        )
        db.conn.commit()
    # 同一 record_id=5 同时是 lm 行与 facts 行：旧逻辑回查 long_memory 会把
    # 事实命中替换成长记忆原文（跨分区碰撞回错表）。
    hits = [
        SearchHit(record_id=5, distance=0.2, text="用户喜欢菟丝子", meta={"kind": "facts"}),
        SearchHit(record_id=6, distance=0.3, text="话题分区命中不该出现", meta={"kind": "topic"}),
    ]
    gated = _pool_gated_hits(uid, hits)
    texts = [h.text for h in gated]
    assert texts == ["用户喜欢菟丝子"], texts
    # facts 行已删 → facts 池命中被闸门丢弃（而非回错表拿长记忆文本）
    with db._lock:
        db.conn.execute("DELETE FROM facts WHERE id=5 AND user_id=?", (uid,))
        db.conn.commit()
    gated = _pool_gated_hits(uid, hits)
    assert [h.text for h in gated] == [], "已删事实的命中不得借碰撞回成长记忆原文"
    print("[OK] memory_search 分池：同 id 跨分区不再回错表，无权威映射的分区宁少勿错")
    return 0


def test_facts_gate_regression() -> int:
    uid = "p329-facts"
    db.ensure_user(uid)
    with db._lock:
        db.conn.execute(
            "INSERT INTO facts (id, user_id, content, ts, source_type, confidence) "
            "VALUES (31, ?, '用户对芒果过敏', '2026-09-29T10:00:00', 'conversation', 1.0)",
            (uid,),
        )
        db.conn.commit()
    _reset([SearchHit(record_id=31, distance=0.2, text="用户对芒果过敏", meta={"kind": "facts"})])
    got = asyncio.run(lt.recall_facts(uid, "芒果"))
    assert any("芒果" in t for t in got), got
    with db._lock:
        db.conn.execute("DELETE FROM facts WHERE id=31 AND user_id=?", (uid,))
        db.conn.commit()
    got = asyncio.run(lt.recall_facts(uid, "芒果"))
    assert not any("芒果" in t for t in got), "facts 既有闸门回归：已删事实不得复活"
    print("[OK] facts 既有闸门回归不破坏")
    return 0


def main() -> int:
    failed = (
        test_lm_orphan_gate()
        + test_lm_gate_fail_open()
        + test_kb_orphan_gate()
        + test_memory_search_pool_split()
        + test_facts_gate_regression()
    )
    if failed:
        print(f"\n=== P3-29 向量闸门：{failed} 项失败 ===")
        return 1
    print("\n=== P3-29 向量闸门：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
