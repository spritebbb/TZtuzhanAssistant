# -*- coding: utf-8 -*-
"""LLM 客户端缓存回归：保存配置热重载后不得复用已关闭的 client。

历史缺陷：POST /api/config 关闭了旧 client，却没清 llm._client_cache；
_client_for_route 按同一 cache key 命中这个已关闭实例继续复用，此后每次
LLM 调用都抛 RuntimeError("Cannot send a request, as the client has been
closed")，用户侧表现为「回复生成中断，请重试」，且只能重启后端恢复
（2026-09-09 data/bot.log 实证：10:31:47–10:33:30 连续 APIConnectionError）。

运行：python -m tests.test_llm_client_cache
"""
from __future__ import annotations

import sys
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.config_api import router
from backend.core import llm

app = FastAPI()
app.include_router(router)

# Electron 生产前端（同源）的真实请求头
_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "http://127.0.0.1:8801",
    "Sec-Fetch-Site": "same-origin",
}

_ROUTE = SimpleNamespace(base_url="http://127.0.0.1:9/v1", key_ref="llm", timeout_sec=5)


class _FakeClient:
    """替代 openai.AsyncOpenAI：只记录是否已被 close，不建真实连接。"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _patches() -> ExitStack:
    """把端点依赖全部隔离：不读真实 .env、不建真实 HTTP 客户端。"""
    stack = ExitStack()
    stack.enter_context(patch("backend.core.model_routes.resolve_route", return_value=_ROUTE))
    stack.enter_context(patch("backend.core.model_routes.resolve_api_key", return_value="test-key"))
    stack.enter_context(patch("openai.AsyncOpenAI", _FakeClient))
    stack.enter_context(patch("backend.core.llm._build_http_client", return_value=None))
    stack.enter_context(
        patch("backend.api.config_api.update_env_file", return_value=["LLM_TEMPERATURE"])
    )
    stack.enter_context(
        patch("backend.api.config_api.config", SimpleNamespace(reload=lambda: None))
    )
    return stack


def test_config_save_rebuilds_client_after_close() -> None:
    with _patches():
        llm._client = None
        llm._client_cache.clear()

        first = llm.get_client()
        assert isinstance(first, _FakeClient), f"应命中假 client: {type(first)}"
        assert first.closed is False
        assert len(llm._client_cache) == 1, "首次取用应写入缓存"

        with TestClient(app) as client:
            r = client.post(
                "/api/config", headers=_HEADERS, json={"llm_temperature": "0.9"}
            )
        assert r.status_code == 200, f"保存配置应 200: {r.status_code} {r.text}"
        assert r.json().get("ok") is True, f"保存配置应 ok=True: {r.text}"

        assert first.closed is True, "保存配置后旧 client 必须已关闭"
        assert not llm._client_cache, "已关闭的 client 不得残留在 _client_cache"

        second = llm.get_client()
        assert second is not first, "必须重建新 client，不能复用已关闭实例"
        assert second.closed is False, "新 client 不应处于已关闭状态"

        # 连续两次保存同样不能把死实例带回缓存
        with TestClient(app) as client:
            r2 = client.post(
                "/api/config", headers=_HEADERS, json={"llm_temperature": "0.7"}
            )
        assert r2.status_code == 200 and r2.json().get("ok") is True, r2.text
        assert second.closed is True
        third = llm.get_client()
        assert third is not second and third.closed is False

    llm._client = None
    llm._client_cache.clear()
    print("[OK] 保存配置后重建 client，不复用已关闭实例")


async def main() -> None:
    test_config_save_rebuilds_client_after_close()
    print("\n全部通过 ✓")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
