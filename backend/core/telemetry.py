# -*- coding: utf-8 -*-
"""Q3 本地可观测性：最小字段事件链与有界日聚合。

遥测只写入本机 ``data/telemetry.db``，不保存对话正文、提示词、事实文本、
恢复材料或外部账号。用户作用域使用安装级随机盐做 HMAC；临时轮直接丢弃。
"""
from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from .config import config
from ..storage.connect import connect_database

EVENT_FIELDS = frozenset({
    "event_name", "request_id", "user_scope_hash", "persona_id",
    "logical_message_id", "source_ids", "rule_version", "duration_bucket",
    "outcome", "error_code", "job_run_id",
})
_EVENT_NAMES = frozenset({
    "chat_started", "tool_started", "tool_finished", "output_checked",
    "session_persisted", "outbox_persisted", "chat_completed", "chat_failed",
    "job_started", "job_finished",
})
_OUTCOMES = frozenset({"", "started", "success", "failure", "cancelled", "timeout", "accept", "rewrite", "fallback"})
_DURATION_BUCKETS = frozenset({"", "lt_100ms", "100ms_1s", "1s_5s", "5s_30s", "gte_30s"})
_ERROR_CODES = frozenset({
    "", "reset_superseded", "user_cancelled", "process_timeout",
    "pipeline_exception", "provider_error", "permission", "budget_exceeded",
})
_ID_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{1,120}$")
_MAX_EVENTS_PER_SCOPE_DAY = 500


@dataclass(frozen=True)
class TraceContext:
    user_id: str
    request_id: str
    logical_message_id: str
    persona_id: str = ""
    ephemeral: bool = False
    job_run_id: str = ""


_trace: contextvars.ContextVar[TraceContext | None] = contextvars.ContextVar(
    "telemetry_trace", default=None
)
_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_conn_path: Path | None = None
_last_prune_day = ""


def enabled() -> bool:
    try:
        from .features import flag

        return flag("telemetry_enabled")
    except Exception:
        return False


def duration_bucket(elapsed_ms: float | int | None) -> str:
    if elapsed_ms is None or elapsed_ms < 0:
        return ""
    value = float(elapsed_ms)
    if value < 100:
        return "lt_100ms"
    if value < 1_000:
        return "100ms_1s"
    if value < 5_000:
        return "1s_5s"
    if value < 30_000:
        return "5s_30s"
    return "gte_30s"


def _db_path() -> Path:
    return config.data_dir / "telemetry.db"


def _connection() -> sqlite3.Connection:
    global _conn, _conn_path, _last_prune_day
    path = _db_path()
    with _lock:
        if _conn is not None and _conn_path != path:
            _conn.close()
            _conn = None
            _last_prune_day = ""
        if _conn is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            _conn = connect_database(
                path, timeout=10, check_same_thread=False, row_factory=True
            )
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS telemetry_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS telemetry_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_name TEXT NOT NULL,
                    request_id TEXT NOT NULL DEFAULT '',
                    user_scope_hash TEXT NOT NULL,
                    persona_id TEXT NOT NULL DEFAULT '',
                    logical_message_id TEXT NOT NULL DEFAULT '',
                    source_ids TEXT NOT NULL DEFAULT '[]',
                    rule_version TEXT NOT NULL DEFAULT '',
                    duration_bucket TEXT NOT NULL DEFAULT '',
                    outcome TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    job_run_id TEXT NOT NULL DEFAULT '',
                    day TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_telemetry_events_scope_day
                    ON telemetry_events(user_scope_hash, day);
                CREATE INDEX IF NOT EXISTS idx_telemetry_events_request
                    ON telemetry_events(request_id, logical_message_id);
                CREATE TABLE IF NOT EXISTS telemetry_daily (
                    day TEXT NOT NULL,
                    user_scope_hash TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    duration_bucket TEXT NOT NULL DEFAULT '',
                    outcome TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(day, user_scope_hash, event_name, duration_bucket, outcome, error_code)
                );
                """
            )
            _conn.commit()
            _conn_path = path
        return _conn


def _salt(conn: sqlite3.Connection) -> bytes:
    row = conn.execute("SELECT value FROM telemetry_meta WHERE key='scope_salt'").fetchone()
    if row:
        return bytes.fromhex(row["value"])
    salt = secrets.token_bytes(32)
    conn.execute("INSERT INTO telemetry_meta(key, value) VALUES('scope_salt', ?)", (salt.hex(),))
    return salt


def user_scope_hash(user_id: str) -> str:
    if not user_id:
        raise ValueError("user_id 不能为空")
    with _lock:
        conn = _connection()
        digest = hmac.new(_salt(conn), user_id.encode("utf-8"), hashlib.sha256).hexdigest()
        conn.commit()
    return digest[:32]


def _safe_id(value: object, *, allow_empty: bool = True) -> str:
    text = str(value or "").strip()
    if not text and allow_empty:
        return ""
    if not _ID_RE.fullmatch(text):
        raise ValueError("标识符格式无效")
    return text


def _safe_sources(value: object) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError("source_ids 必须是标识符数组")
    if len(value) > 8:
        raise ValueError("source_ids 超出上限")
    return [_safe_id(item, allow_empty=False) for item in value]


def _retention_days() -> int:
    return max(1, min(365, int(getattr(config, "telemetry_retention_days", 30))))


def _event_retention_days() -> int:
    return max(1, min(30, int(getattr(config, "telemetry_event_retention_days", 7))))


def _prune(conn: sqlite3.Connection, now: datetime) -> None:
    global _last_prune_day
    day = now.date().isoformat()
    if _last_prune_day == day:
        return
    event_cutoff = (now.date() - timedelta(days=_event_retention_days() - 1)).isoformat()
    daily_cutoff = (now.date() - timedelta(days=_retention_days() - 1)).isoformat()
    conn.execute("DELETE FROM telemetry_events WHERE day < ?", (event_cutoff,))
    conn.execute("DELETE FROM telemetry_daily WHERE day < ?", (daily_cutoff,))
    _last_prune_day = day


def record_event(
    user_id: str,
    payload: Mapping[str, object],
    *,
    ephemeral: bool = False,
    now: datetime | None = None,
) -> bool:
    """校验并记录一个事件；出现字段越权时拒绝整条事件。"""
    unknown = set(payload) - EVENT_FIELDS
    if unknown:
        raise ValueError(f"不允许的遥测字段: {', '.join(sorted(unknown))}")
    if ephemeral or not enabled():
        return False
    event_name = str(payload.get("event_name") or "")
    if event_name not in _EVENT_NAMES:
        raise ValueError("未知事件名")
    outcome = str(payload.get("outcome") or "")
    if outcome not in _OUTCOMES:
        raise ValueError("未知 outcome")
    band = str(payload.get("duration_bucket") or "")
    if band not in _DURATION_BUCKETS:
        raise ValueError("未知 duration_bucket")
    error_code = str(payload.get("error_code") or "")
    if error_code not in _ERROR_CODES:
        raise ValueError("未知 error_code")
    when = now or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    scope_hash = user_scope_hash(user_id)
    supplied_hash = str(payload.get("user_scope_hash") or "")
    if supplied_hash and not hmac.compare_digest(supplied_hash, scope_hash):
        raise ValueError("user_scope_hash 必须由当前用户作用域派生")
    values = {
        "event_name": event_name,
        "request_id": _safe_id(payload.get("request_id")),
        "user_scope_hash": scope_hash,
        "persona_id": _safe_id(payload.get("persona_id")),
        "logical_message_id": _safe_id(payload.get("logical_message_id")),
        "source_ids": _safe_sources(payload.get("source_ids")),
        "rule_version": _safe_id(payload.get("rule_version")),
        "duration_bucket": band,
        "outcome": outcome,
        "error_code": error_code,
        "job_run_id": _safe_id(payload.get("job_run_id")),
    }
    day = when.date().isoformat()
    created_at = when.isoformat(timespec="seconds")
    with _lock:
        conn = _connection()
        _prune(conn, when)
        conn.execute(
            "INSERT INTO telemetry_daily(day,user_scope_hash,event_name,duration_bucket,outcome,error_code,count) "
            "VALUES(?,?,?,?,?,?,1) ON CONFLICT(day,user_scope_hash,event_name,duration_bucket,outcome,error_code) "
            "DO UPDATE SET count=count+1",
            (day, scope_hash, event_name, band, outcome, values["error_code"]),
        )
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM telemetry_events WHERE user_scope_hash=? AND day=?",
            (scope_hash, day),
        ).fetchone()["n"]
        if count < _MAX_EVENTS_PER_SCOPE_DAY:
            conn.execute(
                "INSERT INTO telemetry_events(event_name,request_id,user_scope_hash,persona_id,logical_message_id,"
                "source_ids,rule_version,duration_bucket,outcome,error_code,job_run_id,day,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    values["event_name"], values["request_id"], scope_hash, values["persona_id"],
                    values["logical_message_id"], json.dumps(values["source_ids"], ensure_ascii=True),
                    values["rule_version"], band, outcome, values["error_code"], values["job_run_id"],
                    day, created_at,
                ),
            )
        conn.commit()
    return True


@contextmanager
def bind_trace(context: TraceContext) -> Iterator[None]:
    token = _trace.set(context)
    try:
        yield
    finally:
        _trace.reset(token)


def record_current(event_name: str, **fields: object) -> bool:
    context = _trace.get()
    if context is None:
        return False
    payload: dict[str, object] = {
        "event_name": event_name,
        "request_id": context.request_id,
        "logical_message_id": context.logical_message_id,
        "persona_id": context.persona_id,
        "job_run_id": context.job_run_id,
        **fields,
    }
    return record_event(context.user_id, payload, ephemeral=context.ephemeral)


def summary(user_id: str, *, days: int = 30) -> list[dict]:
    scope_hash = user_scope_hash(user_id)
    days = max(1, min(_retention_days(), int(days)))
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days - 1)).isoformat()
    with _lock:
        rows = _connection().execute(
            "SELECT event_name,duration_bucket,outcome,error_code,SUM(count) AS count "
            "FROM telemetry_daily WHERE user_scope_hash=? AND day>=? "
            "GROUP BY event_name,duration_bucket,outcome,error_code "
            "ORDER BY event_name,duration_bucket,outcome,error_code",
            (scope_hash, cutoff),
        ).fetchall()
    return [dict(row) for row in rows]


def trace_events(user_id: str, request_id: str) -> list[dict]:
    """返回可解释的高层事件链；不返回内部正文或模型推理。"""
    scope_hash = user_scope_hash(user_id)
    request_id = _safe_id(request_id, allow_empty=False)
    with _lock:
        rows = _connection().execute(
            "SELECT event_name,request_id,persona_id,logical_message_id,source_ids,rule_version,"
            "duration_bucket,outcome,error_code,job_run_id,created_at FROM telemetry_events "
            "WHERE user_scope_hash=? AND request_id=? ORDER BY id",
            (scope_hash, request_id),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["source_ids"] = json.loads(item["source_ids"])
        result.append(item)
    return result


def clear_user(user_id: str) -> int:
    scope_hash = user_scope_hash(user_id)
    with _lock:
        conn = _connection()
        first = conn.execute("DELETE FROM telemetry_events WHERE user_scope_hash=?", (scope_hash,)).rowcount
        second = conn.execute("DELETE FROM telemetry_daily WHERE user_scope_hash=?", (scope_hash,)).rowcount
        conn.commit()
    return int(first + second)


def close_for_tests() -> None:
    global _conn, _conn_path, _last_prune_day
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None
        _conn_path = None
        _last_prune_day = ""
