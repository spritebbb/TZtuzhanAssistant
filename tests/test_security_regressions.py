# -*- coding: utf-8 -*-
"""关键修复回归：工具失败、SSRF、确认淘汰、LAN GET 鉴权与输入上限。"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 本文件被 pytest 导入以发现 suite_* 时，不得改动父进程环境或初始化应用；
# suite runner 会在独立子进程中直接执行它，届时再配置轻量测试环境。
if __name__ == "__main__":
    os.environ["MEMORY_EMBED_FORCE"] = "1"
    os.environ["MEMORY_V2"] = "0"
    os.environ["MEMORY_MEM0"] = "0"
    os.environ["MOOD_CITY"] = ""

    from fastapi.testclient import TestClient

    from backend.app import app
    from backend.tools.base import FunctionTool, tool_failure
    from backend.tools.confirm import ConfirmService
    from backend.tools.safety import resolve_public_url


async def _tool_checks() -> None:
    expected = FunctionTool("expected", "", lambda: tool_failure("no"), is_async=False)
    result = await expected.execute({})
    assert result.ok is False and result.error == "no"

    def broken():
        return None + 1

    internal = FunctionTool("broken", "", broken, is_async=False)
    result = await internal.execute({})
    assert result.ok is False and result.error.startswith("TypeError:")


def _ssrf_checks() -> None:
    mixed = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
    ]
    with patch("socket.getaddrinfo", return_value=mixed):
        ok, error, resolved = resolve_public_url("http://example.test/x")
    assert ok is False and "内网" in error and resolved is None

    public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    with patch("socket.getaddrinfo", return_value=public):
        ok, error, resolved = resolve_public_url("https://example.test/x")
    assert ok is True and not error and resolved == "93.184.216.34"


async def _confirm_eviction_check() -> None:
    old = ConfirmService._PENDING_MAX
    ConfirmService._PENDING_MAX = 1
    first = {"event": asyncio.Event(), "decision": None, "ts": 0}
    ConfirmService._pending = {"old": first}
    try:
        ConfirmService._gc_pending_locked(60)
        assert first["event"].is_set() and first["decision"] == "deny"
    finally:
        ConfirmService._pending = {}
        ConfirmService._PENDING_MAX = old


async def _chat_cancel_check() -> None:
    from backend.api import chat as chat_api

    task = asyncio.create_task(asyncio.sleep(60))
    chat_api._bg_by_request["cancel-me"] = task
    response = await chat_api.api_chat_cancel("cancel-me")
    assert response == {"ok": True}
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    chat_api._bg_by_request.pop("cancel-me", None)


async def _remote_task_lifecycle_check() -> None:
    """运行任务不可被清理器静默丢弃，取消端点要实际取消协程。"""
    from starlette.requests import Request

    from backend.api import remote as remote_api

    old_tasks = remote_api._remote_tasks.copy()
    old_max = remote_api._REMOTE_TASKS_MAX
    try:
        remote_api._REMOTE_TASKS_MAX = 1
        remote_api._remote_tasks.clear()
        remote_api._remote_tasks.update({
            "running": {"status": "running", "created_at": 0},
            "finished": {"status": "done", "created_at": 0},
        })
        remote_api._prune_remote_tasks()
        assert "running" in remote_api._remote_tasks
        assert "finished" not in remote_api._remote_tasks

        task = asyncio.create_task(asyncio.sleep(60))
        remote_api._remote_bg_by_id["cancel-me"] = task
        remote_api._remote_tasks["cancel-me"] = {"status": "running", "created_at": time.time()}
        scope = {
            "type": "http", "method": "POST", "path": "/api/remote/task/cancel-me/cancel",
            "headers": [(b"authorization", b"bearer test-token")], "client": ("127.0.0.1", 1234),
        }
        with patch("backend.api.remote.remote_token_ok_by_peer", return_value=True):
            response = await remote_api.api_remote_task_cancel("cancel-me", Request(scope))
        assert response == {"ok": True, "task_id": "cancel-me", "status": "cancelling"}
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()
    finally:
        remote_api._remote_tasks.clear()
        remote_api._remote_tasks.update(old_tasks)
        remote_api._remote_bg_by_id.pop("cancel-me", None)
        remote_api._REMOTE_TASKS_MAX = old_max


def _http_checks() -> None:
    # 通过替换鉴权判定验证敏感 GET 确实进入统一中间件；健康探针保持公开。
    with patch("backend.tools.safety.remote_token_ok_by_peer", return_value=False) as auth:
        with TestClient(app) as client:
            assert client.get("/api/meta").status_code == 403
            assert auth.called
            auth.reset_mock()
            assert client.get("/api/health").status_code == 200
            assert not auth.called
    with TestClient(app) as client:
        assert client.post("/api/chat", data={"text": "x" * 20_001}).status_code == 413

    # /api/remote/task 的既有协议允许 JSON/form body token。全局 LAN 守卫不能在
    # 读取 body 前将它拒绝；路由会用同一检查函数对 body token 再次鉴权。
    async def fake_round(*_args, **_kwargs):
        return "ok"

    token_ok = lambda token, _peer: token == "body-token"
    with (
        patch("backend.tools.safety.remote_token_ok_by_peer", side_effect=token_ok),
        patch("backend.api.remote.remote_token_ok_by_peer", side_effect=token_ok),
        patch("backend.tools.service.run_tool_round", new=fake_round),
    ):
        with TestClient(app) as client:
            response = client.post("/api/remote/task", json={"task": "ping", "token": "body-token"})
            assert response.status_code == 200 and response.json()["ok"] is True

    # reset 恰好发生在最终 bot 消息落库时，也必须给 SSE 一个终止帧。
    from backend.core.reset import ResetSuperseded
    writes = 0

    @asynccontextmanager
    async def reset_between_writes(_epoch):
        nonlocal writes
        writes += 1
        if writes > 1:
            raise ResetSuperseded("test reset")
        yield

    async def completed_process(*_args, **_kwargs):
        return "reply"

    with (
        patch("backend.core.reset.user_write_guard", reset_between_writes),
        patch("backend.api.chat.process", new=completed_process),
    ):
        with TestClient(app) as client:
            response = client.post("/api/chat", data={"text": "reset race"})
            assert response.status_code == 200
            assert '"error": "请求因重置而取消"' in response.text

    async def failed_reset():
        return {"ok": False, "failures": ["vector failed"]}

    with patch("backend.core.reset.reset_everything", failed_reset):
        with TestClient(app) as client:
            assert client.post("/api/user/reset").status_code == 500


def main() -> None:
    asyncio.run(_tool_checks())
    _ssrf_checks()
    asyncio.run(_confirm_eviction_check())
    asyncio.run(_chat_cancel_check())
    asyncio.run(_remote_task_lifecycle_check())
    _http_checks()
    print("[OK] security regressions")


if __name__ == "__main__":
    main()
