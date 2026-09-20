# -*- coding: utf-8 -*-
"""旁路副作用台账：失败不中断主流程，但必须可观测（不是静默 pass）。

覆盖三件事：
1. EffectLedger 的成败语义——成功记 applied、失败记 failures 且不抛出；
2. 同一轮内重复失败只详细记一次日志，但每次都计数（流式回调断连会连爆）；
3. 聊天派活的系统提示真的进入本轮 prompt（回归：曾因 messages 先使用后赋值
   而 NameError，被 except Exception 吞掉，功能整条死掉却没人发现）。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 数据目录隔离：先于任何 backend import 生效，避免读写真实 bot.db
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_ledger_"))

from backend.core.effect_ledger import (  # noqa: E402
    EffectLedger,
    effect_stats,
    reset_effect_stats,
)
from backend.core.pipeline import process  # noqa: E402
from backend.core.userdb import db  # noqa: E402

UID = "ledger-test-user"


def test_success_and_failure_semantics() -> None:
    """成功返回值并记入 applied；失败返回 None、记入 failures、绝不抛出。"""
    ledger = EffectLedger("t")

    def ok(value: int) -> int:
        return value * 2

    def boom() -> None:
        raise ValueError("坏了")

    assert ledger.run("成功项", ok, 21) == 42
    assert ledger.run("失败项", boom) is None, "失败必须降级为 None，不得抛出"
    assert ledger.applied == ["成功项"]
    assert [f.name for f in ledger.failures] == ["失败项"]
    assert ledger.failures[0].error_type == "ValueError"
    assert ledger.failed is True
    summary = ledger.summary()
    assert summary["applied"] == ["成功项"]
    assert summary["failures"][0]["error"] == "ValueError"
    print("[OK] 台账：成功记 applied、失败降级 None 且不抛出")


async def test_async_semantics() -> None:
    """异步副作用同样：成功返回值，失败降级 None。"""
    ledger = EffectLedger("t")

    async def ok() -> str:
        return "好了"

    async def boom() -> None:
        raise RuntimeError("异步坏了")

    assert await ledger.run_async("异步成功", ok) == "好了"
    assert await ledger.run_async("异步失败", boom) is None
    assert ledger.applied == ["异步成功"]
    assert [f.name for f in ledger.failures] == ["异步失败"]
    print("[OK] 台账：异步副作用同语义")


def test_repeated_failure_counts_every_time() -> None:
    """同一轮内重复失败：只详细记一次日志，但每次都计数（不刷爆日志）。"""
    reset_effect_stats()
    ledger = EffectLedger("t")

    def boom() -> None:
        raise OSError("流断了")

    for _ in range(5):
        ledger.run("流式回调", boom)

    assert len(ledger.failures) == 5, "每次失败都要计数"
    stats = effect_stats()
    assert stats["total_failures"] == 5
    entry = next(e for e in stats["by_effect"] if e["name"] == "流式回调")
    assert entry["count"] == 5
    assert "OSError" in entry["last_error"]
    print("[OK] 台账：重复失败每次计数，累计统计可查")


def test_ledger_reaches_process_stats() -> None:
    """台账失败会进进程级统计——这是 /api/meta 里 effect_stats 的数据源。"""
    reset_effect_stats()
    ledger = EffectLedger("t")
    ledger.fail("记忆检索", ValueError("向量库没起来"))
    assert effect_stats()["total_failures"] == 1
    assert effect_stats()["by_effect"][0]["name"] == "记忆检索"
    print("[OK] 台账：失败进入进程级统计（/api/meta effect_stats 可见）")


async def test_dispatch_prompt_reaches_model() -> None:
    """聊天派活：系统提示必须进入本轮 prompt，且 Agent 关联待办已建。"""
    captured: list[list[dict]] = []

    async def fake_chat(messages, **kwargs):
        captured.append(messages)
        return "【思考】内部\n【回复】计划已生成"

    db.ensure_user(UID)
    with patch("backend.core.pipeline.chat", new=fake_chat):
        await process(UID, "帮我分几步做一下竞品分析", mock=True)

    assert captured, "pipeline 未调用主 chat"
    systems = [m["content"] for m in captured[0] if m["role"] == "system"]
    assert any("已按用户要求创建 Agent 任务" in s for s in systems), (
        "派活系统提示未进入 prompt（回归：messages 先使用后赋值曾被静默吞掉）"
    )
    assert systems[0].strip(), "persona 必须是第一条 system"
    assert captured[0][-1]["role"] == "user", "user 必须是发给模型的最后一条"
    agent_tasks = [
        t for t in db.list_tasks(UID) if str(t.get("content", "")).startswith("[Agent任务]")
    ]
    assert agent_tasks, "未创建 Agent 关联待办"
    print("[OK] 派活：系统提示进入 prompt + 关联待办已建")


async def main() -> None:
    test_success_and_failure_semantics()
    await test_async_semantics()
    test_repeated_failure_counts_every_time()
    test_ledger_reaches_process_stats()
    await test_dispatch_prompt_reaches_model()
    reset_effect_stats()
    print("\n=== 旁路副作用台账：全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
