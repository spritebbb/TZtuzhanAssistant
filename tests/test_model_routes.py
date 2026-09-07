"""P0-04A：路由兼容、快照、回退边界与用量归属。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core import llm
from backend.core.model_routes import TASKS, fallback_route, resolve_route


def _clear_route_env(monkeypatch) -> None:
    for task in TASKS:
        for suffix in ("BASE_URL", "KEY_REF", "MODEL", "TIMEOUT_SEC", "MAX_TOKENS", "FALLBACK_TASKS"):
            monkeypatch.delenv(f"MODEL_ROUTE_{task.upper()}_{suffix}", raising=False)


def suite_legacy_defaults_and_explicit_model(monkeypatch) -> None:
    _clear_route_env(monkeypatch)
    route = resolve_route("chat_routine")
    assert route.base_url == llm.config.llm_base_url
    assert route.key_ref == "LLM_API_KEY"
    assert resolve_route("chat_deep").model == (llm.config.llm_model_strong or llm.config.llm_model)
    assert resolve_route("chat_routine", "explicit-model").model == "explicit-model"
    assert resolve_route("extract").base_url == resolve_route("batch_other").base_url
    assert set(TASKS) == {"chat_routine", "chat_deep", "tool", "batch_diary", "batch_other", "extract", "judge", "vision"}


def suite_endpoint_switch_fallback_and_cycle_rejection(monkeypatch) -> None:
    _clear_route_env(monkeypatch)
    monkeypatch.setenv("MODEL_ROUTE_CHAT_DEEP_BASE_URL", "https://cheap.example/v1")
    monkeypatch.setenv("MODEL_ROUTE_CHAT_DEEP_KEY_REF", "CHEAP_KEY")
    monkeypatch.setenv("MODEL_ROUTE_CHAT_DEEP_MODEL", "cheap-model")
    monkeypatch.setenv("MODEL_ROUTE_CHAT_ROUTINE_FALLBACK_TASKS", "chat_deep")
    route = resolve_route("chat_routine")
    fallback = fallback_route(route)
    assert fallback and fallback.base_url == "https://cheap.example/v1"
    assert fallback.key_ref == "CHEAP_KEY" and fallback.model == "cheap-model"

    monkeypatch.setenv("MODEL_ROUTE_CHAT_DEEP_FALLBACK_TASKS", "chat_routine")
    try:
        resolve_route("chat_routine")
    except ValueError as exc:
        assert "循环" in str(exc)
    else:
        raise AssertionError("循环 fallback 必须被拒绝")


def suite_usage_records_actual_task_and_model(monkeypatch) -> None:
    _clear_route_env(monkeypatch)
    monkeypatch.setenv("MODEL_ROUTE_BATCH_DIARY_MODEL", "diary-model")
    calls = []

    class Completion:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="正文"))],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completion()))
    usage = []
    monkeypatch.setattr(llm, "_client_for_route", lambda route: client)
    monkeypatch.setattr(llm, "_record_usage", lambda *args: usage.append(args))
    result = asyncio.run(llm.chat([{"role": "user", "content": "写日记"}], task="batch_diary"))
    assert result == "正文"
    assert calls[0]["model"] == "diary-model"
    assert usage[0][0:2] == ("batch_diary", "diary-model")


def suite_auth_error_never_uses_fallback(monkeypatch) -> None:
    _clear_route_env(monkeypatch)
    monkeypatch.setenv("MODEL_ROUTE_CHAT_ROUTINE_FALLBACK_TASKS", "chat_deep")
    used = []

    class AuthError(Exception):
        status_code = 401

    class Completion:
        async def create(self, **kwargs):
            raise AuthError("unauthorized")

    def client_for(route):
        used.append(route.task)
        return SimpleNamespace(chat=SimpleNamespace(completions=Completion()))

    monkeypatch.setattr(llm, "_client_for_route", client_for)
    try:
        asyncio.run(llm.chat([{"role": "user", "content": "hi"}]))
    except AuthError:
        pass
    else:
        raise AssertionError("鉴权错误必须原样失败")
    assert used == ["chat_routine"]
