# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

from backend.app import create_app
from backend.core.privacy import redact_log_record, redact_sensitive
from backend.tools.hardening import sanitize_error_text, structured_tool_error

SECRETS = (
    "sk-proj-abcdefghijk123456",
    "Bearer abcdefghijk.123456",
    "Authorization: top-secret-token",
    "api_key=another-secret",
    "Cookie: session=abcdef123456",
    "恢复口令：blue-horse-seven",
    r"C:\\Users\\name\\Pictures\\private.png",
)


def suite_sensitive_values_are_removed_from_logs_and_errors() -> None:
    dirty = " | ".join(SECRETS)
    clean = redact_sensitive(dirty)
    for secret in SECRETS:
        assert secret not in clean
    assert "API_KEY已隐去" in clean
    assert "媒体路径已隐去" in clean

    record = {"message": dirty}
    assert redact_log_record(record) is True
    for secret in SECRETS:
        assert secret not in record["message"]

    exception_record = {
        "message": "provider failed",
        "exception": SimpleNamespace(
            type=RuntimeError,
            value=RuntimeError("Authorization: top-secret-token"),
        ),
    }
    assert redact_log_record(exception_record)
    assert exception_record["exception"] is None
    assert "top-secret-token" not in exception_record["message"]
    assert "RuntimeError" in exception_record["message"]

    error = sanitize_error_text("Traceback (most recent call last):\n  File \"x.py\"\nRuntimeError: " + dirty)
    for secret in SECRETS:
        assert secret not in error
    assert len(error) <= 200


def suite_structured_error_has_no_stack_or_credentials() -> None:
    result = structured_tool_error(
        kind="provider", name="web_fetch",
        detail='Traceback (most recent call last):\n  File "C:\\secret.py"\nRuntimeError: sk-proj-abcdefghijk',
    )
    assert "Traceback" not in result and "secret.py" not in result
    assert "sk-proj" not in result
    assert result.startswith("[工具错误 provider] web_fetch：")


def suite_unhandled_http_error_returns_only_safe_request_id() -> None:
    app = create_app()
    handler = app.exception_handlers[Exception]
    request = SimpleNamespace(
        state=SimpleNamespace(request_id="safe-request-123"),
        url=SimpleNamespace(path="/api/test"),
    )
    with patch("backend.app.logger.exception"):
        response = asyncio.run(handler(request, RuntimeError("sk-proj-abcdefghijk")))
    body = json.loads(response.body)
    assert response.status_code == 500
    assert body == {"ok": False, "error": "内部错误", "request_id": "safe-request-123"}
    assert "sk-proj" not in response.body.decode("utf-8")
