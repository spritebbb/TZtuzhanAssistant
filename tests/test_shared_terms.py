# -*- coding: utf-8 -*-
"""D1 人格微演化：共同语言提炼（daily）与注入（≥2 次才用、初识不用）。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 数据目录隔离：先于任何 backend import 生效，避免读写真实 bot.db
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_terms_"))

from backend.core import affection, daily
from backend.core.pipeline import process
from backend.core.userdb import db

UID = "terms-test-user"


async def test_extract_terms() -> None:
    db.ensure_user(UID)

    async def fake_chat(messages, **kwargs):
        assert "语言观察员" in messages[0]["content"]
        return json.dumps({"terms": [
            {"term": "走外包", "category": "slang", "meaning": "把活丢给 AI 干"},
            {"term": "搓一个", "category": "catchphrase", "meaning": ""},
            {"term": "", "category": "catchphrase", "meaning": ""},  # 空词丢弃
            {"term": "糊弄学", "category": "bad_category", "meaning": ""},  # 非法类别兜底
        ]}, ensure_ascii=False)

    with patch.object(daily, "chat", new=fake_chat):
        added = await daily.extract_terms(UID, date.today(), "user: 你又走外包\nbot: 啧")
    assert added == 3, added

    terms = {t["term"]: t for t in db.get_terms(UID)}
    assert terms["走外包"]["meaning"] == "把活丢给 AI 干"
    assert terms["搓一个"]["category"] == "catchphrase"
    assert terms["糊弄学"]["category"] == "catchphrase"  # 非法类别归一

    # 幂等：再次提炼同词不新增，但次数累加（演化幅度的第一道闸）
    with patch.object(daily, "chat", new=fake_chat):
        added2 = await daily.extract_terms(UID, date.today(), "user: 又走外包\nbot: 行吧")
    assert added2 == 0, added2
    assert db.get_terms(UID)[0]["count"] >= 2
    print("[OK] 共同语言提炼：解析/兜底/幂等累加")


async def _capture(uid: str) -> list[dict]:
    captured: list[list[dict]] = []

    async def fake_chat(messages, **kwargs):
        captured.append(messages)
        return "【思考】内部\n【回复】嗯"

    with patch("backend.core.pipeline.chat", new=fake_chat):
        await process(uid, "在吗", mock=True)
    assert captured
    return captured[0]


async def test_terms_injection_gating() -> None:
    # 只出现 1 次的词不注入（不稳定不演化）
    db.add_term(UID, "一面之缘词", "catchphrase", "")
    affection.set_affection(UID, 60)  # 亲密
    db.set_first_chat_done(UID)

    messages = await _capture(UID)
    systems = [m["content"] for m in messages if m["role"] == "system"]
    hit = [s for s in systems if "沉淀下来的说法" in s]
    assert hit, "熟人阶段共同语言注入缺失"
    assert "走外包" in hit[0] and "把活丢给 AI 干" in hit[0], "≥2 次的梗应带含义注入"
    assert "一面之缘词" not in hit[0], "只出现 1 次的词不应注入"
    idx = next(i for i, m in enumerate(messages) if m.get("content") == hit[0])
    last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
    assert idx < last_user, "注入必须 user-last"

    # 初识阶段不注入（关系没到，默契还没长出来）
    uid2 = "terms-test-stranger"
    db.ensure_user(uid2)
    db.add_term(uid2, "走外包", "slang", "把活丢给 AI 干")
    db.add_term(uid2, "走外包", "slang", "把活丢给 AI 干")  # count=2
    affection.set_affection(uid2, 5)
    db.set_first_chat_done(uid2)
    messages2 = await _capture(uid2)
    systems2 = [m["content"] for m in messages2 if m["role"] == "system"]
    assert not any("沉淀下来的说法" in s for s in systems2), "初识不应注入共同语言"
    print("[OK] 注入闸口：≥2 次才用、带含义、初识不用、user-last")


async def main() -> None:
    await test_extract_terms()
    await test_terms_injection_gating()
    print("\n=== D1 人格微演化：全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
