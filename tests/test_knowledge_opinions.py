# -*- coding: utf-8 -*-
"""P3-02C 有来源知识观点：生命周期、语境、删除与导出恢复。"""
from __future__ import annotations

import os
import sys
import tempfile
import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_p3_opinions_"))

from backend.core import knowledge
from backend.core.context_registry import collect_context
from backend.core.relationship_export import export_bundle, restore_bundle
from backend.core.userdb import db

UID = "opinion-user"


def _seed_document(user_id: str = UID) -> tuple[int, list[int]]:
    db.ensure_user(user_id)
    now = datetime.now().isoformat(timespec="seconds")
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO kb_documents (user_id,filename,stored_path,format,size_bytes,chunk_count,ts) "
            "VALUES (?,?,?,'txt',10,2,?)",
            (user_id, "植物笔记.txt", str(Path(os.environ["TZTUZHAN_DATA_DIR"]) / "missing.txt"), now),
        )
        doc_id = int(cur.lastrowid)
        chunks = []
        for seq, text in enumerate(("菟丝子是一种寄生植物。", "它会缠绕在其他植物上生长。")):
            cur = db.conn.execute(
                "INSERT INTO kb_chunks (user_id,doc_id,seq,text,ts) VALUES (?,?,?,?,?)",
                (user_id, doc_id, seq, text, now),
            )
            chunks.append(int(cur.lastrowid))
        db.conn.commit()
    return doc_id, chunks


def test_save_recall_revoke_and_source_validation() -> tuple[int, list[int], int]:
    doc_id, chunks = _seed_document()
    opinion = knowledge.save_opinion(
        UID, doc_id, "我觉得菟丝子的生存方式很顽强。",
        [{"chunk_id": chunks[0], "start": 0, "end": 11}],
        origin="assistant", confidence=0.8,
    )
    assert opinion["origin"] == "assistant" and opinion["source_spans"][0]["chunk_id"] == chunks[0]
    same = knowledge.save_opinion(
        UID, doc_id, "我觉得菟丝子的生存方式很顽强。",
        [{"chunk_id": chunks[0], "start": 0, "end": 11}], origin="assistant", confidence=0.8,
    )
    assert same["id"] == opinion["id"] and len(knowledge.list_opinions(UID)) == 1
    assert knowledge.relevant_opinions(UID, "你怎么看菟丝子的生存方式")[0]["id"] == opinion["id"]
    assert knowledge.relevant_opinions(UID, "量子物理") == []

    try:
        knowledge.save_opinion(UID, doc_id, "错误引用", [{"chunk_id": 999999}])
        raise AssertionError("跨文档/不存在的分块必须拒绝")
    except knowledge.KnowledgeError:
        pass

    assert knowledge.revoke_opinion(UID, opinion["id"])
    assert knowledge.relevant_opinions(UID, "菟丝子的生存方式") == []
    revived = knowledge.save_opinion(
        UID, doc_id, "我觉得菟丝子的生存方式很顽强。",
        [{"chunk_id": chunks[0], "start": 0, "end": 11}], origin="assistant", confidence=0.9,
    )
    assert revived["id"] == opinion["id"] and revived["version"] == 3
    return doc_id, chunks, int(opinion["id"])


def test_registry_export_restore_and_delete() -> None:
    doc_id, chunks, opinion_id = test_save_recall_revoke_and_source_validation()
    selection = collect_context(UID, "菟丝子的生存方式", turn_id=1)
    assembled = selection.assemble()
    assert "角色观点" in assembled and "不是对方的事实" in assembled
    assert "knowledge_opinions" in selection.providers_used

    from backend.api import knowledge as knowledge_api

    with patch.object(knowledge_api, "active_user_id", return_value=UID):
        response = asyncio.run(knowledge_api.api_opinion_list(doc_id))
    assert response["ok"] and response["opinions"][0]["id"] == opinion_id

    bundle = export_bundle(UID, ["knowledge"])
    assert len(bundle["data"]["knowledge_opinions"]) == 1
    restored = restore_bundle(bundle, "opinion-restored")
    assert restored["ok"]
    restored_opinions = knowledge.list_opinions("opinion-restored")
    assert len(restored_opinions) == 1
    restored_source = restored_opinions[0]["source_spans"][0]
    assert restored_source["chunk_id"] != chunks[0], "恢复必须重映射分块 id"

    # 来源正文发生变化时 hash 校验使观点立即失效，不注入幽灵观点。
    with db._lock:
        db.conn.execute("UPDATE kb_chunks SET text='被修改的正文' WHERE id=?", (chunks[0],))
        db.conn.commit()
    assert knowledge.get_opinion(UID, opinion_id) is None
    assert knowledge.relevant_opinions(UID, "菟丝子的生存方式") == []

    assert knowledge.delete_document("opinion-restored", restored_opinions[0]["document_id"])
    assert knowledge.list_opinions("opinion-restored") == []
    with db._lock:
        remains = db.conn.execute(
            "SELECT COUNT(*) FROM knowledge_opinion_sources WHERE user_id='opinion-restored'"
        ).fetchone()[0]
    assert remains == 0


def main() -> None:
    test_registry_export_restore_and_delete()
    print("[OK] P3-02C 观点来源、幂等/撤销、相关注入、hash 失效、导出恢复与删除")


if __name__ == "__main__":
    main()
