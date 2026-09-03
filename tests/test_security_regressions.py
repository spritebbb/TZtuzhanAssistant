# -*- coding: utf-8 -*-
"""关键修复回归：工具失败、SSRF、确认淘汰、LAN GET 鉴权与输入上限。"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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
    _http_checks()
    print("[OK] security regressions")


if __name__ == "__main__":
    main()
