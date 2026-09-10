# -*- coding: utf-8 -*-
"""Q3 本地可观测性与来源链回归。直接运行，不访问网络或真实 data。"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

_PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT))
_ROOT = Path(tempfile.mkdtemp(prefix="tztuzhan-observability-"))
os.environ["TZTUZHAN_DATA_DIR"] = str(_ROOT)
os.environ["FEATURE_TELEMETRY_ENABLED"] = "1"

from backend.core.config import config  # noqa: E402
from backend.core import telemetry  # noqa: E402


def _reset() -> None:
    telemetry.close_for_tests()
    for suffix in ("", "-wal", "-shm"):
        path = _ROOT / f"telemetry.db{suffix}"
        if path.exists():
            path.unlink()


def test_schema_and_field_allowlist() -> None:
    _reset()
    assert telemetry.record_event(
        "private-user-id",
        {
            "event_name": "chat_started",
            "request_id": "a" * 32,
            "logical_message_id": "b" * 32,
            "persona_id": "default",
            "outcome": "started",
        },
    )
    try:
        telemetry.record_event(
            "private-user-id",
            {"event_name": "chat_completed", "raw_prompt": "secret"},
        )
        raise AssertionError("越权字段没有被拒绝")
    except ValueError:
        pass

    conn = sqlite3.connect(_ROOT / "telemetry.db")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(telemetry_events)")}
    conn.close()
    forbidden = {"prompt", "reply", "content", "fact_text", "account", "recovery_material"}
    assert columns.isdisjoint(forbidden)
    assert "user_id" not in columns
    telemetry.close_for_tests()
    on_disk = b"".join(
        p.read_bytes() for p in _ROOT.glob("telemetry.db*") if p.is_file()
    )
    assert b"private-user-id" not in on_disk
    print("[PASS] 字段白名单与用户作用域哈希")


def test_trace_chain_and_daily_aggregate() -> None:
    _reset()
    uid = "trace-user"
    request_id = "c" * 32
    logical_id = "d" * 32
    common = {"request_id": request_id, "logical_message_id": logical_id, "persona_id": "default"}
    events = (
        ("chat_started", {"outcome": "started"}),
        ("tool_started", {"source_ids": ["tool:web_search"], "outcome": "started"}),
        ("tool_finished", {"source_ids": ["tool:web_search"], "duration_bucket": "100ms_1s", "outcome": "success"}),
        ("output_checked", {"source_ids": ["rule:tool_protocol"], "rule_version": "out-1.v1", "outcome": "accept"}),
        ("session_persisted", {"source_ids": ["session:assistant"], "outcome": "success"}),
        ("chat_completed", {"duration_bucket": "1s_5s", "outcome": "success"}),
    )
    for name, extra in events:
        telemetry.record_event(uid, {"event_name": name, **common, **extra})
    trace = telemetry.trace_events(uid, request_id)
    assert [item["event_name"] for item in trace] == [name for name, _ in events]
    assert all(item["logical_message_id"] == logical_id for item in trace)
    assert telemetry.summary(uid)
    print("[PASS] chat→tool→OUT-1→session 关联与日聚合")


def test_ephemeral_disable_retention_cap_and_clear() -> None:
    _reset()
    assert not telemetry.record_event(
        "ephemeral-user", {"event_name": "chat_started", "outcome": "started"}, ephemeral=True
    )
    original_enabled = telemetry.enabled
    telemetry.enabled = lambda: False
    try:
        assert not telemetry.record_event(
            "disabled-user", {"event_name": "chat_started", "outcome": "started"}
        )
    finally:
        telemetry.enabled = original_enabled

    now = datetime.now(timezone.utc)
    telemetry.record_event(
        "bounded-user", {"event_name": "chat_started", "outcome": "started"},
        now=now - timedelta(days=8),
    )
    for _ in range(505):
        telemetry.record_event(
            "bounded-user", {"event_name": "chat_started", "outcome": "started"}, now=now
        )
    conn = sqlite3.connect(_ROOT / "telemetry.db")
    scope = telemetry.user_scope_hash("bounded-user")
    event_count = conn.execute(
        "SELECT COUNT(*) FROM telemetry_events WHERE user_scope_hash=? AND day=?",
        (scope, now.date().isoformat()),
    ).fetchone()[0]
    aggregate = conn.execute(
        "SELECT SUM(count) FROM telemetry_daily WHERE user_scope_hash=? AND day=?",
        (scope, now.date().isoformat()),
    ).fetchone()[0]
    old_events = conn.execute(
        "SELECT COUNT(*) FROM telemetry_events WHERE user_scope_hash=? AND day<?",
        (scope, now.date().isoformat()),
    ).fetchone()[0]
    old_daily = conn.execute(
        "SELECT SUM(count) FROM telemetry_daily WHERE user_scope_hash=? AND day<?",
        (scope, now.date().isoformat()),
    ).fetchone()[0]
    conn.close()
    assert event_count == 500
    assert aggregate == 505
    assert old_events == 0
    assert old_daily == 1

    telemetry.record_event("other-user", {"event_name": "chat_started", "outcome": "started"})
    assert telemetry.clear_user("bounded-user") > 0
    assert telemetry.summary("bounded-user") == []
    assert telemetry.summary("other-user")
    print("[PASS] 临时轮/关闭/30天保留/每日上限/按用户清理")


def test_real_http_trace_linkage() -> None:
    _reset()
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend.core.output_hygiene import RULE_VERSION

    async def fake_process(user_id, text, **kwargs):
        await kwargs["progress_cb"]({"type": "tool", "name": "web_search"})
        await kwargs["progress_cb"]({
            "type": "tool_done", "name": "web_search", "ok": True, "error_code": ""
        })
        telemetry.record_current(
            "output_checked", rule_version=RULE_VERSION, outcome="accept"
        )
        await kwargs["stream_cb"]("完成")
        return "完成"

    app = create_app()
    logical_id = "e" * 32
    with patch("backend.api.chat.process", new=fake_process):
        client = TestClient(app)
        response = client.post(
            "/api/chat",
            data={"text": "测试调用链", "session_id": "current", "request_id": logical_id},
        )
    assert response.status_code == 200, response.text
    request_id = response.headers["X-Request-ID"]
    from backend.core.persona_profiles import active_user_id

    trace = telemetry.trace_events(active_user_id(), request_id)
    names = [item["event_name"] for item in trace]
    expected = [
        "chat_started", "tool_started", "tool_finished", "output_checked",
        "session_persisted", "chat_completed",
    ]
    cursor = 0
    for name in names:
        if cursor < len(expected) and name == expected[cursor]:
            cursor += 1
    assert cursor == len(expected), names
    assert all(item["logical_message_id"] == logical_id for item in trace)
    print("[PASS] 真实 HTTP 生命周期 request/logical id 串联")


def main() -> None:
    config.telemetry_retention_days = 30
    config.telemetry_event_retention_days = 7
    test_schema_and_field_allowlist()
    test_trace_chain_and_daily_aggregate()
    test_ephemeral_disable_retention_cap_and_clear()
    test_real_http_trace_linkage()
    telemetry.close_for_tests()
    print("\nQ3 observability: 4/4 passed")


if __name__ == "__main__":
    main()
