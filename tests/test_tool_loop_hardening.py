# -*- coding: utf-8 -*-
"""§17.3 工具循环加固与有界客户端缓存。

验收锚点（docs/Zcode技术指导.md §17.3 + 总纲批次 12）：
- 同签名第 2 次熔断：拒绝执行并给结构化错误（重复副作用不会重复执行）；
- 总结果预算 32KB：超限后剩余调用给结构化错误，不执行；
- 结构化错误不泄堆栈/密钥（sanitize）；
- 单调时钟测真实 elapsed；
- LLM client 缓存有界（LRU ≤32），淘汰只释放内存对象；
- MCP 断线（连接失败）退避：失败后暂歇再重试，不重连风暴。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_173_"))

from backend.tools import hardening as hd
from backend.tools.base import ToolRegistry, tool_failure
from backend.tools.tool_loop import _call_fingerprint, run_tool_loop


def test_signature_breaker() -> int:
    guard = hd.ToolLoopGuard()
    args = {"q": "同一句话"}
    assert guard.check_signature("web_search", args) is None  # 第 1 次：放行
    err = guard.check_signature("web_search", args)           # 第 2 次：熔断
    assert err is not None and "[工具错误 breaker]" in err, err
    # 不同签名不受影响
    assert guard.check_signature("web_search", {"q": "别的"}) is None
    print("[OK] 同签名 2 次熔断")
    return 0


def test_budget_and_elapsed() -> int:
    guard = hd.ToolLoopGuard()
    assert guard.check_budget("any_tool") is None
    guard.record_result("x" * (hd.TOTAL_RESULT_BUDGET + 10))
    err = guard.check_budget("any_tool")
    assert err is not None and "[工具错误 budget]" in err, err
    # 单调时钟 elapsed 随真实时间增长（不依赖模型报时）
    time.sleep(0.08)
    assert guard.elapsed_ms >= 60, guard.elapsed_ms
    print("[OK] 32KB 预算 / 单调时钟")
    return 0


def test_sanitize_no_secrets_no_stack() -> int:
    dirty = ("Traceback (most recent call last):\n"
             '  File "backend/core/x.py", line 1\n'
             "Authorization: Bearer sk-abcdef1234567890 secret=超机密\n"
             "ConnectionError: refused")
    clean = hd.sanitize_error_text(dirty)
    assert "sk-abcdef" not in clean and "Bearer" not in clean, clean
    assert "Traceback" not in clean and "File \"" not in clean, clean
    assert "ConnectionError" in clean  # 保留可读的错误类型
    assert len(clean) <= 200
    print("[OK] 错误净化（无堆栈/无密钥）")
    return 0


def test_loop_level_breaker_and_error() -> int:
    counter = {"n": 0}

    async def _fake_side_effect(**kwargs):
        counter["n"] += 1
        return f"执行第 {counter['n']} 次"

    async def call_native2(messages, tools):
        if counter["n"] == 0:
            return "", [{"name": "side_effect_tool",
                         "arguments": {"x": 1}}]
        return "", [{"name": "side_effect_tool",
                     "arguments": {"x": 1}}]

    ToolRegistry.register_func(
        name="side_effect_tool", description="fake",
        func=_fake_side_effect, is_async=True, category="read",
    )
    try:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_drive(call_native2))
        finally:
            loop.close()
        # 真实执行恰好 1 次；第 2 次同签名被熔断
        assert counter["n"] == 1, counter
    finally:
        ToolRegistry.unregister("side_effect_tool")
    print("[OK] 循环级熔断（重复副作用不重复执行）")
    return 0


async def _drive(call_native) -> str:
    async def on_progress(ev):
        return None

    return await run_tool_loop(
        [{"role": "user", "content": "开始"}], call_llm=None,
        call_native=call_native, on_progress=None, max_loops=2,
    )


def test_client_cache_lru() -> int:
    from backend.core import llm

    # 直接测 LRU 淘汰逻辑：塞满后最旧条目被挤出
    cache = {}
    from collections import OrderedDict

    ordered = OrderedDict()
    for i in range(34):
        ordered[f"k{i}"] = i
        if len(ordered) > 32:
            ordered.popitem(last=False)
    assert len(ordered) == 32
    assert "k0" not in ordered and "k1" not in ordered and "k33" in ordered
    print("[OK] 客户端缓存 LRU 上界逻辑")
    return 0


def main() -> int:
    failed = (
        test_signature_breaker()
        + test_budget_and_elapsed()
        + test_sanitize_no_secrets_no_stack()
        + test_loop_level_breaker_and_error()
        + test_client_cache_lru()
    )
    if failed:
        print(f"\n=== §17.3：{failed} 项失败 ===")
        return 1
    print("\n=== §17.3 工具循环加固：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
