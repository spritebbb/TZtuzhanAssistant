# -*- coding: utf-8 -*-
"""Q4 JOB-1 租约恢复与外部调用故障边界。直接运行，不访问真实数据或网络。"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["TZTUZHAN_DATA_DIR"] = tempfile.mkdtemp(prefix="tztuzhan-job-provider-")
os.environ["MEMORY_V2"] = "0"
os.environ["MEMORY_MEM0"] = "0"

from backend.maintenance import time_tick

UTC = timezone.utc
NOW = datetime(2026, 9, 10, 6, 0, tzinfo=UTC)
PERIOD = time_tick._iso(NOW)


def _conn() -> sqlite3.Connection:
    path = Path(tempfile.mkdtemp(prefix="tztuzhan-job-recovery-")) / "jobs.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE job_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, scope_key TEXT NOT NULL, "
        "job_key TEXT NOT NULL, period_start TEXT NOT NULL, status TEXT NOT NULL, lease_owner TEXT, "
        "lease_until TEXT, attempt INTEGER NOT NULL DEFAULT 0, next_retry TEXT, finished_at TEXT, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "UNIQUE(scope_key,job_key,period_start))"
    )
    return conn


def test_scope_isolation_and_single_claim() -> None:
    conn = _conn()
    assert time_tick._claim(conn, "persona::one", PERIOD, "owner-1", NOW)
    assert not time_tick._claim(conn, "persona::one", PERIOD, "owner-2", NOW)

    # 目标任务已经成功；另一个 scope 有过期 running。旧 SQL 的 OR 优先级错误会
    # 把另一个 scope 更新掉，并错误返回认领成功。
    conn.execute(
        "UPDATE job_runs SET status='succeeded',lease_owner=NULL,lease_until=NULL "
        "WHERE scope_key='persona::one'"
    )
    conn.execute(
        "INSERT INTO job_runs(scope_key,job_key,period_start,status,lease_owner,lease_until,attempt,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        ("persona::other", time_tick.JOB_KEY, PERIOD, "running", "other-owner",
         time_tick._iso(NOW - timedelta(seconds=1)), 1, time_tick._iso(NOW), time_tick._iso(NOW)),
    )
    conn.commit()
    assert not time_tick._claim(conn, "persona::one", PERIOD, "owner-3", NOW)
    other = conn.execute("SELECT lease_owner FROM job_runs WHERE scope_key='persona::other'").fetchone()
    assert other["lease_owner"] == "other-owner"
    conn.close()
    print("[PASS] 双 worker 单认领与 scope 隔离")


def test_expired_lease_late_finish_and_clock_rollback() -> None:
    conn = _conn()
    scope = "persona::lease"
    assert time_tick._claim(conn, scope, PERIOD, "owner-old", NOW)
    assert not time_tick._claim(
        conn, scope, PERIOD, "owner-backward", NOW - timedelta(minutes=10)
    ), "时钟回拨不能让未到期租约提前失效"
    after_expiry = NOW + timedelta(seconds=time_tick.LEASE_SECONDS + 1)
    assert time_tick._claim(conn, scope, PERIOD, "owner-new", after_expiry)
    assert not time_tick._finish(conn, scope, PERIOD, "owner-old", True, after_expiry, 1)
    row = conn.execute(
        "SELECT status,lease_owner,attempt FROM job_runs WHERE scope_key=?", (scope,)
    ).fetchone()
    assert (row["status"], row["lease_owner"], row["attempt"]) == ("running", "owner-new", 2)
    assert time_tick._finish(conn, scope, PERIOD, "owner-new", True, after_expiry, 2)
    conn.close()
    print("[PASS] 租约过期恢复、时钟回拨与迟到完成隔离")


def test_reset_delete_rejects_late_completion() -> None:
    conn = _conn()
    scope = "persona::reset"
    assert time_tick._claim(conn, scope, PERIOD, "owner-reset", NOW)
    conn.execute("DELETE FROM job_runs WHERE scope_key=?", (scope,))
    conn.commit()
    assert not time_tick._finish(conn, scope, PERIOD, "owner-reset", True, NOW, 1)
    assert conn.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0] == 0
    conn.close()
    print("[PASS] reset 删除后迟到 worker 不能复活任务")


class _StatusError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"status {status_code}")
        self.status_code = status_code


def _response(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=None
    )


async def test_external_failure_boundaries() -> None:
    from backend.core import llm

    create = AsyncMock(side_effect=[_StatusError(429), _response("ok")])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with patch.object(llm, "get_client", return_value=client), \
         patch.object(llm.asyncio, "sleep", new=AsyncMock()):
        assert await llm.chat([{"role": "user", "content": "x"}]) == "ok"
    assert create.await_count == 2, "429 应在首块前执行有界重试"

    invalid = AsyncMock(side_effect=ValueError("invalid json"))
    invalid_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=invalid)))
    with patch.object(llm, "get_client", return_value=invalid_client):
        try:
            await llm.chat([{"role": "user", "content": "x"}])
            raise AssertionError("无效响应应失败")
        except ValueError:
            pass
    assert invalid.await_count == 1, "非暂时性格式错误不能盲目重试"

    class PartialStream:
        def __init__(self):
            self.sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.sent:
                self.sent = True
                return SimpleNamespace(
                    usage=None,
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="part"))],
                )
            raise TimeoutError("stream interrupted")

    stream_create = AsyncMock(return_value=PartialStream())
    stream_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=stream_create))
    )
    pieces: list[str] = []
    with patch.object(llm, "_client_for_route", return_value=stream_client), \
         patch("backend.core.model_routes.fallback_route", return_value=None):
        try:
            async for piece in llm.chat_stream([{"role": "user", "content": "x"}]):
                pieces.append(piece)
            raise AssertionError("部分流中断应向上游报告")
        except TimeoutError:
            pass
    assert pieces == ["part"] and stream_create.await_count == 1, "已输出片段后不得从头重试"

    gate = asyncio.Event()

    async def blocked_create(**kwargs):
        await gate.wait()

    cancelled_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=blocked_create))
    )
    with patch.object(llm, "get_client", return_value=cancelled_client):
        task = asyncio.create_task(llm.chat([{"role": "user", "content": "x"}]))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
            raise AssertionError("取消应传播")
        except asyncio.CancelledError:
            pass
    print("[PASS] timeout/429/无效响应/部分流/取消边界")


def main() -> None:
    test_scope_isolation_and_single_claim()
    test_expired_lease_late_finish_and_clock_rollback()
    test_reset_delete_rejects_late_completion()
    asyncio.run(test_external_failure_boundaries())
    print("\nQ4 job recovery: 4/4 passed")


if __name__ == "__main__":
    main()
