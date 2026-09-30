# -*- coding: utf-8 -*-
"""岗位一回归：知识观点一致性判定（add/supersede/corroborate/fail-open）。

覆盖：supersede 演化链（不物理删）、corroborate 佐证（confidence 上调+span 并入
+不新增行）、决策日志 append-only、fail-open=旧行为、非法 target 降级、
superseded 不入召回。LLM 全 mock（vi 脚本式，无网络无 key）。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztzhan_judge_"))

UID = "u_judge"


def _fake_doc(user_id: str, doc_tag: str) -> tuple[int, int]:
    """造一份文档 + 一个真实分块，返回 (document_id, chunk_id)。"""
    from backend.core.userdb import db

    now = "2026-09-30T12:00:00"
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO kb_documents (user_id,filename,stored_path,format,size_bytes,chunk_count,ts) "
            "VALUES (?,?,?,?,?,?,?)",
            (user_id, f"{doc_tag}.txt", f"documents/{doc_tag}.txt", "txt", 100, 1, now),
        )
        doc_id = int(cur.lastrowid)
        cur = db.conn.execute(
            "INSERT INTO kb_chunks (user_id,doc_id,seq,text,ts) VALUES (?,?,?,?,?)",
            (user_id, doc_id, 0, "吞吐与延迟的实测数据表明结论如文本所述，段落足够长以覆盖span。", now),
        )
        db.conn.commit()
    return doc_id, int(cur.lastrowid)


def _opinions(user_id: str) -> list[dict]:
    from backend.core.knowledge import list_opinions

    return list_opinions(user_id, active_only=False)


def test_supersede_chain() -> int:
    from backend.core.knowledge import save_opinion

    doc1, chunk1 = _fake_doc(UID, "v1")
    old = save_opinion(UID, doc1, "框架X的吞吐显著优于Y", [{"chunk_id": chunk1, "start": 0, "end": 10}])
    doc2, chunk2 = _fake_doc(UID, "v2")
    verdict = {"action": "supersede", "target_id": old["id"], "reason": "复测推翻旧结论"}
    new = save_opinion(UID, doc2, "复测显示Y反超X约四成", [{"chunk_id": chunk2, "start": 0, "end": 10}],
                       verdict=verdict)
    rows = {o["id"]: o for o in _opinions(UID)}
    assert rows[old["id"]]["status"] == "superseded", rows[old["id"]]
    assert rows[old["id"]]["superseded_by"] == new["id"]
    assert rows[new["id"]]["status"] == "active"
    from backend.core.userdb import db
    with db._lock:
        logs = db.conn.execute(
            "SELECT action,target_opinion_id FROM knowledge_opinion_decisions "
            "WHERE user_id=? ORDER BY id", (UID,)).fetchall()
    assert any(l["action"] == "supersede" and l["target_opinion_id"] == old["id"] for l in logs), [dict(l) for l in logs]
    print("[OK] supersede 演化链：旧观点标 superseded+superseded_by，不物理删，决策日志入册")
    return 0


def test_corroborate() -> int:
    from backend.core.knowledge import get_opinion, save_opinion

    UID2 = UID + "_corr"
    doc1, chunk1 = _fake_doc(UID2, "src-a")
    base = save_opinion(UID2, doc1, "用户作息偏晚，常在深夜活跃",
                        [{"chunk_id": chunk1, "start": 0, "end": 8}], confidence=0.5)
    doc2, chunk2 = _fake_doc(UID2, "src-b")
    before = len(_opinions(UID2))
    verdict = {"action": "corroborate", "target_id": base["id"], "reason": "多源一致"}
    save_opinion(UID2, doc2, "另一来源同样显示作息偏晚", [{"chunk_id": chunk2, "start": 0, "end": 8}],
                 verdict=verdict)
    assert len(_opinions(UID2)) == before, "corroborate 不应新增行"
    after = get_opinion(UID2, base["id"])
    assert after["confidence"] > 0.5, after["confidence"]
    from backend.core.userdb import db
    with db._lock:
        spans = db.conn.execute(
            "SELECT COUNT(*) AS n FROM knowledge_opinion_sources WHERE user_id=? AND opinion_id=?",
            (UID2, base["id"])).fetchone()
    assert spans["n"] == 2, "佐证 span 应并入目标（跨文档）"
    print("[OK] corroborate：不新增行、confidence 上调、跨文档 span 并入")
    return 0


def test_realpath_corroborate_effective() -> int:
    """真实路径回归（四原则·验证后落码）：extract_opinions 初始 0.6 时
    corroborate 的 +0.1 必须实际生效（修前主路径 confidence=1.0 被 MIN 封死）。"""
    import asyncio
    from unittest.mock import patch, AsyncMock

    from backend.core import knowledge as kb

    UID4 = UID + "_real"
    doc, chunk = _fake_doc(UID4, "real-a")
    first = kb.save_opinion(UID4, doc, "立场A的初始版本", [{"chunk_id": chunk, "start": 0, "end": 8}],
                            origin="assistant", confidence=0.6)
    assert first["confidence"] == 0.6
    # 模拟 extract_opinions 的完整链路（两次 LLM：①提炼 opinions JSON ②judge 判定）
    opinions_json = '{"opinions": [{"stance": "立场A的强化表述", "spans": [0]}]}'
    verdict_json = '{"action": "corroborate", "target_id": %d, "reason": "同源佐证"}' % first["id"]
    with patch.object(kb, "relevant_opinions", return_value=[first]), \
         patch("backend.core.llm.chat", new=AsyncMock(side_effect=[opinions_json, verdict_json])):
        saved = asyncio.run(kb.extract_opinions(UID4, doc))
    assert saved, "extract_opinions 应产出（佐证路径返回目标观点）"
    after = kb.get_opinion(UID4, first["id"])
    assert after["confidence"] == 0.7, f"佐证后应 0.6→0.7，实际 {after['confidence']}"
    print("[OK] 真实路径 corroborate：初始 0.6 下 +0.1 实际生效（0.6→0.7）")
    return 0


def test_failopen_and_invalid_target() -> int:
    from backend.core.knowledge import save_opinion

    UID3 = UID + "_fo"
    doc, chunk = _fake_doc(UID3, "fo")
    # verdict 为 None / 非法 target 的 supersede → 均按普通 add 落库
    a = save_opinion(UID3, doc, "无判定直接入库的观点", [{"chunk_id": chunk, "start": 0, "end": 6}])
    assert a["status"] == "active"
    b = save_opinion(UID3, doc, "判定指向不存在目标的观点", [{"chunk_id": chunk, "start": 6, "end": 12}],
                     verdict={"action": "supersede", "target_id": 999999, "reason": "坏目标"})
    assert b["status"] == "active"
    rows = {o["id"]: o for o in _opinions(UID3)}
    assert rows[a["id"]]["status"] == "active" and rows[b["id"]]["status"] == "active"
    print("[OK] fail-open：verdict 缺失/非法目标 → 旧行为（普通 add）")
    return 0


def test_judge_failopen_unit() -> int:
    import asyncio

    from backend.core import consistency_judge as cj

    async def run() -> dict:
        with patch("backend.core.llm.chat", new=AsyncMock(side_effect=RuntimeError("boom"))):
            return await cj.judge_opinion("新观点", [{"id": 1, "stance": "旧", "confidence": 0.5}])

    v = asyncio.run(run())
    assert v["action"] == "add" and v["target_id"] is None, v
    # 非法动作 / 坏 JSON 同样降级
    assert cj._parse_verdict('{"action":"delete_all"}', {1})["action"] == "add"
    assert cj._parse_verdict("not json", {1})["action"] == "add"
    assert cj._parse_verdict('{"action":"supersede","target_id":42}', {1})["action"] == "add"
    print("[OK] 判定器单元：LLM 异常/非法输出/越界 target 一律 fail-open=add")
    return 0


def test_superseded_not_recalled() -> int:
    from backend.core.knowledge import relevant_opinions

    hits = relevant_opinions(UID, "吞吐 复测", limit=4)
    for h in hits:
        assert h["status"] == "active", h
    superseded_ids = {o["id"] for o in _opinions(UID) if o["status"] == "superseded"}
    assert superseded_ids and not (superseded_ids & {h["id"] for h in hits}), \
        "superseded 观点不得进入召回"
    print("[OK] 召回闸门：superseded 不入 relevant_opinions（检索侧零改动达成）")
    return 0


def main() -> int:
    test_supersede_chain()
    test_corroborate()
    test_realpath_corroborate_effective()
    test_failopen_and_invalid_target()
    test_judge_failopen_unit()
    test_superseded_not_recalled()
    print("\n=== 岗位一·知识观点一致性：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
