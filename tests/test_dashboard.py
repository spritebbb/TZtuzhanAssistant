# -*- coding: utf-8 -*-
"""C5 养成仪表盘：历史落账、日聚合、API 与重置覆盖。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_dashboard_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core.dashboard import dashboard_summary  # noqa: E402
from backend.core.userdb import db  # noqa: E402

UID = "assistant-main"


def _seed() -> None:
    db.ensure_user(UID)
    db.set_affection_absolute(UID, 24)
    db.update_affection(UID, 3, "认真陪伴")
    db.set_mood(UID, 72)
    db.add_message(UID, "user", "今天一起做点什么")
    db.add_message(UID, "assistant", "先把你的计划摊开看看")
    now = datetime.now().isoformat(timespec="seconds")
    with db._lock:
        db.conn.execute(
            "INSERT INTO usage_log "
            "(user_id, channel, model, prompt_tokens, completion_tokens, estimated, ts) "
            "VALUES (?, 'reply', 'test-model', 120, 30, 0, ?)",
            (UID, now),
        )
        db.conn.execute(
            "INSERT INTO promises (user_id, content, follow_up, source, created_at) "
            "VALUES (?, '明天继续整理书架', ?, '', ?)",
            (UID, date.today().isoformat(), now),
        )
        db.conn.executemany(
            "INSERT INTO promises (user_id, content, follow_up, source, created_at) "
            "VALUES (?, ?, '', '', ?)",
            [(UID, f'备用承诺 {index}', now) for index in range(5)],
        )
        db.conn.execute(
            "INSERT INTO diary (user_id, date, content, mood, ts) VALUES (?, ?, '测试日记', '开心', ?)",
            (UID, date.today().isoformat(), now),
        )
        db.conn.commit()


def _test_history_and_summary() -> None:
    _seed()
    with db._lock:
        affection_rows = db.conn.execute(
            "SELECT delta, value FROM affection_log WHERE user_id = ? ORDER BY id", (UID,)
        ).fetchall()
        mood_values = [row[0] for row in db.conn.execute(
            "SELECT value FROM mood_log WHERE user_id = ? ORDER BY id", (UID,)
        ).fetchall()]
    assert affection_rows[-1]["value"] == 27
    assert mood_values[-1] == 72

    summary = dashboard_summary(UID, 30)
    today = summary["timeline"][-1]
    assert summary["days"] == 30 and len(summary["timeline"]) == 30
    assert today["date"] == date.today().isoformat()
    assert today["affection"] == 27 and today["mood"] == 72
    assert today["messages"] == 2 and today["user_messages"] == 1
    assert today["tokens"] == 150 and today["calls"] == 1
    assert summary["current"]["pending_promises"] == 6
    assert summary["stats"]["active_days"] == 1
    assert summary["stats"]["diaries"] == 1
    assert summary["stats"]["unlock_total"] == 9
    assert summary["promises"][0]["content"] == "明天继续整理书架"
    assert len(summary["promises"]) == 5
    assert summary["recent_affection"][0]["reason"] == "认真陪伴"
    print("[OK] C5 历史落账与 30 天聚合")


def _test_api_and_reset_coverage() -> None:
    from fastapi.testclient import TestClient

    from backend.app import create_app
    from backend.core import reset as reset_mod

    assert "mood_log" in reset_mod._TABLES
    with TestClient(create_app()) as client:
        response = client.get("/api/dashboard?days=7")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"] is True
        assert payload["dashboard"]["days"] == 7
        assert len(payload["dashboard"]["timeline"]) == 7
        assert client.get("/api/dashboard?days=6").status_code == 422
    print("[OK] C5 API 范围门控与 reset 覆盖")


if __name__ == "__main__":
    _test_history_and_summary()
    _test_api_and_reset_coverage()
