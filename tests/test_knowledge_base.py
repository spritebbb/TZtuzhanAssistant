# -*- coding: utf-8 -*-
"""D2 知识库（RAG）：解析/分块/入库/检索门控/删除/重置覆盖/API 闭环。

数据目录隔离（TZTUZHAN_DATA_DIR 指向临时目录），不读写真实 bot.db 与向量库；
向量层用 monkeypatch 替换为确定性假实现，不依赖 embedding 模型，
保证测试快速、确定、可独立运行。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 数据目录隔离：先于任何 backend import 生效
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_kb_"))

from backend.core import knowledge  # noqa: E402
from backend.core.knowledge import KnowledgeError  # noqa: E402


def _test_chunking() -> None:
    # 短文本：一整块，不切
    assert knowledge.chunk_text("短短的一段", 600, 120) == ["短短的一段"]
    # 空文本：无块
    assert knowledge.chunk_text("", 600, 120) == []
    # 长文本：多块且相邻块有重叠（前块尾部出现在后块开头附近）
    text = "\n\n".join(f"第{i}段。" + "甲" * 120 for i in range(20))
    chunks = knowledge.chunk_text(text, 300, 60)
    assert len(chunks) >= 5, f"应切出多块，实际 {len(chunks)}"
    assert all(len(c) <= 400 for c in chunks), "块长不应显著超过 size"
    # 重叠断言：相邻两块存在公共子串（边界自然切断时重叠可能略短，放宽到 10 字符）
    for prev, nxt in zip(chunks, chunks[1:]):
        assert prev[-10:] in nxt or nxt[:10] in prev, "相邻块应有内容重叠"
    # 碎渣合并：尾巴不足 size//3 时不单独成块
    tail_text = "乙" * 500 + "\n\n短尾"
    tail_chunks = knowledge.chunk_text(tail_text, 300, 60)
    assert all(len(c) >= 50 for c in tail_chunks), "不应出现碎渣块"
    # overlap >= size 时自动收敛，不死循环
    assert knowledge.chunk_text("丙" * 100, 100, 200)
    print("[OK] 分块：边界/重叠/碎渣合并/参数收敛")


def _test_parse() -> None:
    # utf-8 文本
    assert knowledge.parse_document("txt", "你好，菟菚".encode("utf-8")) == "你好，菟菚"
    # gb18030 文本
    assert knowledge.parse_document("txt", "中文编码".encode("gb18030")) == "中文编码"
    # 多余空行压缩
    assert knowledge.parse_document("md", "a\n\n\n\n\nb".encode()) == "a\n\nb"
    # 空内容报错
    try:
        knowledge.parse_document("txt", b"   \n\n  ")
        raise AssertionError("空文档应抛 KnowledgeError")
    except KnowledgeError:
        pass
    # 不支持的格式
    try:
        knowledge.detect_format("报告.docx")
        raise AssertionError("docx 应抛 KnowledgeError")
    except KnowledgeError:
        pass
    assert knowledge.detect_format("笔记.MD") == "md"
    print("[OK] 解析：utf-8/gb18030/空行压缩/空文档/格式白名单")


def _fake_vector_add(user_id, kind, record_id, text, extra=None):
    _FAKE_STORE[(user_id, kind, record_id)] = (text, extra or {})
    return True


def _fake_vector_search(user_id, query, top_k=5, kind=None):
    from backend.core.memory.vector_store import SearchHit

    hits = []
    for (uid, k, rid), (text, meta) in _FAKE_STORE.items():
        if uid != user_id or (kind and k != kind):
            continue
        # 确定性假检索：查询词出现在文本里则距离 0.2（相关），否则 0.9（无关）
        dist = 0.2 if query[:4] in text else 0.9
        hits.append(SearchHit(record_id=rid, distance=dist, text=text, meta=meta))
    hits.sort(key=lambda h: h.distance)
    return hits[:top_k]


def _fake_vector_delete(user_id, kind, record_id):
    _FAKE_STORE.pop((user_id, kind, record_id), None)
    return True


_FAKE_STORE: dict = {}


def _test_ingest_and_recall() -> None:
    from backend.core.memory import vector_store
    from backend.core.userdb import db

    uid = "assistant-main"
    db.ensure_user(uid)

    # kb kind 必须已进白名单
    assert "kb" in vector_store._KINDS, "kb 未加入向量库 kind 白名单"

    original_add, original_search = vector_store.add, vector_store.search
    original_delete = vector_store.delete
    vector_store.add = _fake_vector_add
    vector_store.search = _fake_vector_search
    vector_store.delete = _fake_vector_delete
    try:
        content = ("菟丝子是一种寄生植物，依靠吸器从宿主获取养分。" * 30).encode("utf-8")
        doc = knowledge.ingest_document(uid, "寄生植物笔记.md", content)
        assert doc["chunk_count"] >= 2
        assert doc["indexed"] == doc["chunk_count"], "全部分块应向量化"
        assert Path(knowledge.get_document(uid, doc["id"])["stored_path"]).is_file(), "原文应落盘"

        # 列表
        docs = knowledge.list_documents(uid)
        assert len(docs) == 1 and docs[0]["filename"] == "寄生植物笔记.md"

        # 检索命中：查询词在文本中 → 距离 0.2 < 阈值 0.55 → 注入
        hits = knowledge.recall_knowledge(uid, "菟丝子是怎么")
        assert hits and hits[0]["filename"] == "寄生植物笔记.md"
        assert all(h["distance"] <= 0.55 for h in hits)

        # 检索门控：查询与内容无关 → 距离 0.9 > 阈值 → 一条都不注入
        assert knowledge.recall_knowledge(uid, "量子力学入门") == []

        # 删除：文档行/分块行/向量/落盘原文全部清掉
        assert knowledge.delete_document(uid, doc["id"]) is True
        assert knowledge.get_document(uid, doc["id"]) is None
        assert not any(k[2] for k in _FAKE_STORE if k[0] == uid), "向量应同步删除"
        assert knowledge.delete_document(uid, doc["id"]) is False, "重复删除应返回 False"

        # 数量上限检查与插入必须同锁完成；两个并发上传不能同时越过上限。
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        from backend.core.config import config

        old_max_documents = config.kb_max_documents
        original_parse = knowledge.parse_document
        barrier = Barrier(2)

        def synchronized_parse(fmt: str, data: bytes) -> str:
            result = original_parse(fmt, data)
            barrier.wait(timeout=5)
            return result

        config.kb_max_documents = 1
        knowledge.parse_document = synchronized_parse
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(knowledge.ingest_document, uid, f"race-{i}.txt", b"content")
                    for i in range(2)
                ]
                outcomes = []
                for future in futures:
                    try:
                        outcomes.append(future.result())
                    except KnowledgeError:
                        outcomes.append(None)
            assert sum(item is not None for item in outcomes) == 1
            docs = knowledge.list_documents(uid)
            assert len(docs) == 1
            assert knowledge.delete_document(uid, docs[0]["id"])
        finally:
            knowledge.parse_document = original_parse
            config.kb_max_documents = old_max_documents

        # 超限拒绝
        try:
            knowledge.ingest_document(uid, "big.txt", b"x" * (config.kb_max_file_mb * 1024 * 1024 + 1))
            raise AssertionError("超限文件应抛 KnowledgeError")
        except KnowledgeError:
            pass
    finally:
        vector_store.add, vector_store.search = original_add, original_search
        vector_store.delete = original_delete
        with db._lock:
            db.conn.execute("DELETE FROM kb_documents WHERE user_id = ?", (uid,))
            db.conn.execute("DELETE FROM kb_chunks WHERE user_id = ?", (uid,))
            db.conn.commit()
    print("[OK] 入库/检索门控/删除/超限拒绝/并发上限")


def _test_reset_coverage() -> None:
    from backend.core import reset as reset_mod
    from backend.core.userdb import db

    # 新表必须进两处 reset 清单，否则"失忆重开"残留脏表
    assert "kb_documents" in reset_mod._TABLES and "kb_chunks" in reset_mod._TABLES
    # 表结构已随 schema 建好（临时库）
    with db._lock:
        db.conn.execute("SELECT id FROM kb_documents LIMIT 1")
        db.conn.execute("SELECT id FROM kb_chunks LIMIT 1")
    print("[OK] 重置覆盖：kb 两表在 reset 清单且 schema 已建")


async def _test_api() -> None:
    from fastapi.testclient import TestClient

    from backend.app import create_app
    from backend.core.config import config
    from backend.core.memory import vector_store

    original_add, original_search = vector_store.add, vector_store.search
    vector_store.add = _fake_vector_add
    vector_store.search = _fake_vector_search
    try:
        with TestClient(create_app()) as client:
            # 上传
            resp = client.post(
                "/api/knowledge/upload",
                files={"file": ("藤蔓研究.txt", "缠绕、吸器与宿主的关系。".encode("utf-8"), "text/plain")},
            )
            assert resp.status_code == 200 and resp.json()["ok"], resp.text
            doc_id = resp.json()["document"]["id"]

            # 列表
            docs = client.get("/api/knowledge/documents").json()["documents"]
            assert any(d["id"] == doc_id for d in docs)

            # 不支持的格式走业务错误（ok=False 而非 500）
            bad = client.post(
                "/api/knowledge/upload",
                files={"file": ("幻灯片.pptx", b"xxxx", "application/octet-stream")},
            )
            assert bad.json()["ok"] is False and "不支持的格式" in bad.json()["error"]

            # 上传接口必须在读入全部请求体前按配置拒绝超大文件。
            old_max = config.kb_max_file_mb
            try:
                config.kb_max_file_mb = 1
                too_large = client.post(
                    "/api/knowledge/upload",
                    files={
                        "file": (
                            "超大.txt",
                            b"x" * (1024 * 1024 + 1),
                            "text/plain",
                        )
                    },
                )
                assert too_large.status_code == 413
                assert too_large.json()["ok"] is False
            finally:
                config.kb_max_file_mb = old_max

            # 删除
            assert client.delete(f"/api/knowledge/documents/{doc_id}").json()["ok"] is True
            assert client.delete(f"/api/knowledge/documents/{doc_id}").json()["ok"] is False
    finally:
        vector_store.add, vector_store.search = original_add, original_search
    print("[OK] API：上传/列表/格式拒绝/大小门控/删除闭环")


async def _run() -> None:
    _test_chunking()
    _test_parse()
    _test_ingest_and_recall()
    _test_reset_coverage()
    await _test_api()


if __name__ == "__main__":
    asyncio.run(_run())
