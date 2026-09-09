# -*- coding: utf-8 -*-
"""汇率插件回归：取汇率必须真的能成功，失败原因要可见。

历史缺陷：`urllib.request.Request(..., timeout=15)` —— Request.__init__ 不接受
timeout，这行每次抛 TypeError，又被 `except Exception: return None` 吞掉，
于是 currency_convert 永远返回「汇率获取失败，请稍后再试」（2026-09-09 实证：
工具审计里 ok=false 两条、间隔 9 小时，均为该原因）。

运行：python -m tests.test_currency_plugin
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import plugins.currency as currency  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args) -> bool:
        return False


def test_fetch_rate_success() -> int:
    currency._rate_cache.update(rate=None, ts=0.0)
    calls: dict = {}

    def fake_urlopen(req, timeout=None):
        calls["url"] = req.full_url
        calls["timeout"] = timeout
        return _FakeResponse(json.dumps({"rates": {"CNY": 7.1234}}).encode())

    with patch("plugins.currency.urllib.request.urlopen", new=fake_urlopen):
        rate = currency._fetch_rate()

    assert rate == 7.1234, rate
    assert calls["timeout"] == 15, f"timeout 必须传给 urlopen: {calls}"
    assert calls["url"].endswith("/v6/latest/USD"), calls
    print("[OK] 取汇率成功，且 timeout 传给了 urlopen（不是 Request）")
    return 0


def test_convert_formats_result() -> int:
    currency._rate_cache.update(rate=7.0, ts=time.time())
    out = currency._currency_convert(amount=100)
    assert "700.00" in out, out
    assert "USD" in out and "CNY" in out, out
    print("[OK] 换算结果格式正确：", out)
    return 0


def test_failure_is_reported_not_swallowed() -> int:
    currency._rate_cache.update(rate=None, ts=0.0)

    def boom(req, timeout=None):
        raise OSError("网络炸了")

    with patch("plugins.currency.urllib.request.urlopen", new=boom):
        out = currency._currency_convert(amount=100)
    assert "失败" in out, out
    print("[OK] 取数失败时给出可读提示：", out)
    return 0


def main() -> int:
    failed = (
        test_fetch_rate_success()
        + test_convert_formats_result()
        + test_failure_is_reported_not_swallowed()
    )
    if failed:
        print(f"\n=== 汇率插件：{failed} 项失败 ===")
        return 1
    print("\n=== 汇率插件：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
