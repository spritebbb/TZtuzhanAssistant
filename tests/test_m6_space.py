# -*- coding: utf-8 -*-
"""M6 在场感与共同空间：真实 artifact 空间化 + 情绪声线确定性映射。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_m6_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import activities  # noqa: E402
from backend.core.tts import prosody_for_mood  # noqa: E402
from backend.core.userdb import db  # noqa: E402

UID = "assistant-main"


def _seed_document(filename: str, chunks: list[str]) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO kb_documents "
            "(user_id, filename, stored_path, format, size_bytes, chunk_count, ts) "
            "VALUES (?, ?, '', 'txt', ?, ?, ?)",
            (UID, filename, sum(map(len, chunks)), len(chunks), now),
        )
        doc_id = int(cur.lastrowid)
        db.conn.executemany(
            "INSERT INTO kb_chunks (user_id, doc_id, seq, text, ts) VALUES (?, ?, ?, ?, ?)",
            [(UID, doc_id, index, text, now) for index, text in enumerate(chunks)],
        )
        db.conn.commit()
    return doc_id


def _test_artifacts_space() -> None:
    from fastapi.testclient import TestClient

    from backend.app import create_app

    # 读完一本书 → 共同书摘 artifact 出现在空间里
    doc_id = _seed_document("角落测试.txt", ["只有一段。"])
    activity = activities.start_reading(UID, doc_id)
    activities.save_note(UID, activity["id"], "这段写得很妙")
    completed = activities.complete_activity(UID, activity["id"])

    with TestClient(create_app()) as client:
        data = client.get("/api/artifacts").json()
        assert data["ok"] and data["artifacts"], "空间里应有真实 artifact"
        found = next(
            (a for a in data["artifacts"] if a["source_id"] == activity["id"]), None
        )
        assert found is not None
        assert found["artifact_type"] == "book_summary"
        assert found["title"] == completed["title"].replace("共读《", "《") or "角落测试" in found["title"]
        assert "写得很妙" in found["content"]
        assert found["version"] == 1

        # 删源文档 → 物件随源消失，不留凭空历史
        from backend.core import knowledge
        from backend.core.memory import vector_store

        saved = vector_store.delete
        vector_store.delete = lambda *args, **kwargs: True
        try:
            assert knowledge.delete_document(UID, doc_id)
        finally:
            vector_store.delete = saved
        data2 = client.get("/api/artifacts").json()
        assert all(a["source_id"] != activity["id"] for a in data2["artifacts"])
    print("[OK] 共同空间：只陈列真实 artifact，随源可追溯、随删可清理")


def _test_prosody() -> None:
    # 各心情档位确定性映射
    assert prosody_for_mood(90) == ("+8%", "+15Hz")
    assert prosody_for_mood(70) == ("+4%", "+8Hz")
    assert prosody_for_mood(50) == ("-3%", "-5Hz")
    assert prosody_for_mood(10) == ("-8%", "-12Hz")
    # 边界与非法输入全部落回中性，不异常
    assert prosody_for_mood(None) == ("+0%", "+0Hz")
    assert prosody_for_mood(-5) == ("+0%", "+0Hz")
    assert prosody_for_mood(999) == ("+0%", "+0Hz")
    assert prosody_for_mood("abc") == ("+0%", "+0Hz")

    # 韵律参与缓存键：不同心情不同文件，避免串音
    from backend.core.tts import _path_for

    p1 = _path_for("同一段话", "v", "+0%", "+0Hz")
    p2 = _path_for("同一段话", "v", "-8%", "-12Hz")
    assert p1 != p2
    print("[OK] 情绪声线：档位确定性映射，非法输入降级中性，缓存键含韵律")


async def main() -> None:
    _test_artifacts_space()
    _test_prosody()
    print("\n=== M6 在场感与共同空间：全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
