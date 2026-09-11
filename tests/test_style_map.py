# -*- coding: utf-8 -*-
"""D1 复活切片：场景化表达观察（user_style_map）——提炼（daily）、注入（≥2 次/初识不用/开关）、主权（API 删除）。"""
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
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_style_map_"))

from backend.core import affection, daily
from backend.core.features import set_flag
from backend.core.pipeline import process
from backend.core.userdb import db

UID = "style-map-test-user"


async def test_extract_style_map() -> None:
    db.ensure_user(UID)

    async def fake_chat(messages, **kwargs):
        assert "表达观察员" in messages[0]["content"]
        return json.dumps({"styles": [
            {"situation": "倾诉烦恼时", "style": "喜欢用短句+省略号"},
            {"situation": "开玩笑时", "style": "会接梗反杀"},
            {"situation": "", "style": "空场景丢弃"},          # 空场景丢弃
            {"situation": "没特征时", "style": ""},             # 空风格丢弃
        ]}, ensure_ascii=False)

    with patch.object(daily, "chat", new=fake_chat):
        added = await daily.extract_style_map(UID, date.today(), "user: 唉…算了…\nbot: 说吧")
    assert added == 2, added

    entries = {s["situation"]: s for s in db.get_style_map(UID)}
    assert entries["倾诉烦恼时"]["style"] == "喜欢用短句+省略号"
    assert entries["开玩笑时"]["count"] == 1

    # 幂等：同场景不新增行，次数累加且风格更新为最新（演化幅度的第一道闸）
    async def fake_chat_again(messages, **kwargs):
        return json.dumps({"styles": [
            {"situation": "倾诉烦恼时", "style": "短句为主，偶尔省略号"},
        ]}, ensure_ascii=False)

    with patch.object(daily, "chat", new=fake_chat_again):
        added2 = await daily.extract_style_map(UID, date.today(), "user: 还是不想说…\nbot: 嗯")
    assert added2 == 0, added2
    rows = db.get_style_map(UID)
    assert len(rows) == 2, rows
    vent = next(s for s in rows if s["situation"] == "倾诉烦恼时")
    assert vent["count"] == 2
    assert vent["style"] == "短句为主，偶尔省略号"
    print("[OK] 场景化表达提炼：解析/兜底丢弃/同场景累加幂等")


async def _capture(uid: str) -> list[dict]:
    captured: list[list[dict]] = []

    async def fake_chat(messages, **kwargs):
        captured.append(messages)
        return "【思考】内部\n【回复】嗯"

    with patch("backend.core.pipeline.chat", new=fake_chat):
        await process(uid, "在吗", mock=True)
    assert captured
    return captured[0]


async def test_style_map_injection_gating() -> None:
    # 只观察到 1 次的场景不注入（不稳定不演化）
    db.add_style_map(UID, "一次性的场景", "一次性风格")
    affection.set_affection(UID, 60)  # 亲密
    db.set_first_chat_done(UID)

    messages = await _capture(UID)
    systems = [m["content"] for m in messages if m["role"] == "system"]
    hit = [s for s in systems if "表达习惯" in s]
    assert hit, "熟人阶段场景化表达注入缺失"
    assert "倾诉烦恼时" in hit[0] and "短句为主，偶尔省略号" in hit[0], "≥2 次的场景应注入"
    assert "一次性的场景" not in hit[0], "只观察到 1 次的场景不应注入"
    idx = next(i for i, m in enumerate(messages) if m.get("content") == hit[0])
    last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
    assert idx < last_user, "注入必须 user-last"

    # 初识阶段不注入（关系没到）
    uid2 = "style-map-stranger"
    db.ensure_user(uid2)
    db.add_style_map(uid2, "倾诉烦恼时", "短句为主")
    db.add_style_map(uid2, "倾诉烦恼时", "短句为主")  # count=2
    affection.set_affection(uid2, 5)
    db.set_first_chat_done(uid2)
    messages2 = await _capture(uid2)
    systems2 = [m["content"] for m in messages2 if m["role"] == "system"]
    assert not any("表达习惯" in s for s in systems2), "初识不应注入表达观察"

    # 开关关闭：已积累的观察也不再注入（提炼与删除入口不受影响）
    set_flag("style_map_enabled", False)
    try:
        messages3 = await _capture(UID)
        systems3 = [m["content"] for m in messages3 if m["role"] == "system"]
        assert not any("表达习惯" in s for s in systems3), "开关关闭不应注入"
    finally:
        set_flag("style_map_enabled", True)
    print("[OK] 注入闸口：≥2 次才用、初识不用、开关即时生效、user-last")


async def test_style_map_sovereignty() -> None:
    # 主权删除：删掉后 get_style_map 不再返回
    rows = db.get_style_map(UID)
    target = next(s for s in rows if s["situation"] == "开玩笑时")
    assert db.del_style_map(UID, target["id"]) is True
    assert db.del_style_map(UID, target["id"]) is False  # 幂等：再删不存在返回 False
    remaining = {s["situation"] for s in db.get_style_map(UID)}
    assert "开玩笑时" not in remaining
    print("[OK] 用户主权：删除生效且幂等")


async def main() -> None:
    await test_extract_style_map()
    await test_style_map_injection_gating()
    await test_style_map_sovereignty()
    test_style_map_http()
    print("\n=== D1 场景化表达观察（style_map 复活）：全部通过 ===")


def test_style_map_http() -> None:
    """主权 HTTP 闭环：interaction-style 携带 style_map、逐条删除、404 边界。"""
    from fastapi.testclient import TestClient

    from backend.app import create_app

    with TestClient(create_app()) as client:
        uid = "assistant-main"  # active_user_id 默认命名空间
        db.ensure_user(uid)
        db.add_style_map(uid, "倾诉烦恼时", "短句为主，偶尔省略号")
        db.add_style_map(uid, "开玩笑时", "会接梗反杀")
        entry = next(s for s in db.get_style_map(uid) if s["situation"] == "开玩笑时")

        data = client.get("/api/memory/interaction-style").json()
        assert any(s["situation"] == "开玩笑时" for s in data.get("style_map", []))

        assert client.delete(f"/api/memory/style-map/{entry['id']}").status_code == 200
        assert all(s["id"] != entry["id"] for s in db.get_style_map(uid))
        assert client.delete("/api/memory/style-map/999999").status_code == 404
    print("[OK] 主权 HTTP：style_map 随互动偏好返回、逐条删除、不存在 404")


if __name__ == "__main__":
    asyncio.run(main())
