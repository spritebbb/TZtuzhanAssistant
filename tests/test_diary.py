# -*- coding: utf-8 -*-
"""C2 日记/研究课题：幂等落库、七篇触发阶段报告与只读 API。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 数据目录隔离：先于任何 backend import 生效，避免读写真实 bot.db
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_diary_"))

from fastapi.testclient import TestClient

from backend.core import daily
from backend.core.userdb import db, list_diaries, list_research_reports


async def _run() -> None:
    uid = "assistant-main"
    db.ensure_user(uid)
    with db._lock:
        db.conn.execute("DELETE FROM diary WHERE user_id = ?", (uid,))
        db.conn.execute("DELETE FROM research_reports WHERE user_id = ?", (uid,))
        db.conn.commit()

    calls = {"diary": 0, "report": 0}

    async def fake_chat(messages, **kwargs):
        system = messages[0]["content"]
        # 注意：RESEARCH_PROMPT 同时包含「私人日记」与「阶段研究记录」，
        # 必须先判研究报告，否则会被日记分支截胡。
        if "阶段研究记录" in system:
            calls["report"] += 1
            return json.dumps({"title": "样本的第一个七日", "content": "观察：他愿意持续分享日常。假设：信任正在形成，但证据仍少。接下来留意他遇到压力时会不会主动开口。"}, ensure_ascii=False)
        if "私人日记" in system:
            calls["diary"] += 1
            return json.dumps({"content": f"第{calls['diary']}天的观察，我嘴上没说，心里倒记住了。", "mood": "平静"}, ensure_ascii=False)
        raise AssertionError("意外的 LLM 调用")

    original_chat = daily.chat
    daily.chat = fake_chat
    try:
        start = date(2099, 1, 1)
        first = None
        for index in range(7):
            item = await daily.write_daily_diary(
                uid, start + timedelta(days=index), f"user: 今天记录第 {index + 1} 件小事\nbot: 我听见了"
            )
            first = first or item
        duplicate = await daily.write_daily_diary(uid, start, "user: 不应覆盖")
        assert duplicate["id"] == first["id"]
        assert calls["diary"] == 7, "同一天重复执行不得再次生成"

        report = await daily.maybe_write_research_report(uid)
        assert report and report["title"] == "样本的第一个七日"
        assert len(list_diaries(uid)) == 7
        assert len(list_research_reports(uid)) == 1
        assert await daily.maybe_write_research_report(uid) is None, "七篇只能生成一份报告"
        assert calls["report"] == 1
    finally:
        daily.chat = original_chat

    from backend.app import create_app

    with TestClient(create_app()) as client:
        diary_response = client.get("/api/diary")
        report_response = client.get("/api/research-reports")
        assert diary_response.status_code == 200 and len(diary_response.json()["diaries"]) == 7
        assert report_response.status_code == 200 and len(report_response.json()["reports"]) == 1
        assert client.get("/api/diary/not-a-date").status_code == 400

    with db._lock:
        db.conn.execute("DELETE FROM diary WHERE user_id = ?", (uid,))
        db.conn.execute("DELETE FROM research_reports WHERE user_id = ?", (uid,))
        db.conn.commit()
    print("[OK] C2 日记幂等、七篇研究报告与浏览 API")


if __name__ == "__main__":
    asyncio.run(_run())
