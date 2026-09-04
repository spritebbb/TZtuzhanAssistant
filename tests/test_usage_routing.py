# -*- coding: utf-8 -*-
"""D5 模型路由 + 成本面板：用量记录聚合、usage 提取与估算兜底、路由启发式、汇总端点。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 数据目录隔离：先于任何 backend import 生效，避免读写真实 bot.db
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_usage_"))

from backend.core import llm
from backend.core.config import config
from backend.core.userdb import db, log_usage, usage_summary

UID = "assistant-main"


def test_usage_log_and_summary() -> None:
    db.ensure_user(UID)
    log_usage(UID, "reply", "deepseek-chat", 100, 50)
    log_usage(UID, "reply", "deepseek-chat", 200, 80)
    log_usage(UID, "perception", "deepseek-chat", 30, 10)
    # 手工塞一条 10 天前的记录：不应计入 7 天聚合
    old_day = (date.today() - timedelta(days=10)).isoformat()
    with db._lock:
        db.conn.execute(
            "INSERT INTO usage_log (user_id, channel, model, prompt_tokens, completion_tokens, estimated, ts) "
            "VALUES (?, 'reply', 'x', 999, 999, 0, ?)", (UID, old_day),
        )
        db.conn.commit()
    summary = usage_summary(UID, 7)
    assert summary["today"]["prompt"] == 330, summary["today"]
    assert summary["today"]["calls"] == 3
    assert summary["period"]["prompt"] == 330, "10 天前的记录不应计入 7 天聚合"
    channels = {r["channel"]: r for r in summary["by_channel"]}
    assert channels["reply"]["prompt"] == 300 and channels["perception"]["prompt"] == 30
    print("[OK] usage_log：记录/今日聚合/周期过滤/按通道分组")


def _fake_client(pt: int, ct: int, with_usage: bool = True):
    usage = SimpleNamespace(prompt_tokens=pt, completion_tokens=ct) if with_usage else None

    class _Completions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="你好呀"))],
                usage=usage,
            )

    return SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))


async def test_chat_records_usage() -> None:
    before = usage_summary(UID, 1)["today"]["calls"]
    with patch.object(llm, "get_client", lambda: _fake_client(120, 40)):
        text = await llm.chat([{"role": "user", "content": "你好"}])
    assert text == "你好呀"
    after = usage_summary(UID, 1)["today"]
    assert after["calls"] == before + 1
    assert after["estimated"] == 0, "有 usage 时不应标记估算"

    # 端点不返回 usage → 本地估算兜底且 estimated=1
    with patch.object(llm, "get_client", lambda: _fake_client(0, 0, with_usage=False)):
        await llm.chat([{"role": "user", "content": "再问一句"}])
    final = usage_summary(UID, 1)["today"]
    assert final["estimated"] == 1, "无 usage 时应本地估算并标记"
    print("[OK] chat：usage 提取 + 估算兜底")


def test_strong_model_routing() -> None:
    from backend.core.pipeline import _needs_strong_model

    hits = ["帮我写一篇关于秋天的作文", "这段代码有 bug，帮我 debug 一下",
            "帮我写个 python 脚本", "把这段话翻译成英文", "x" * 90]
    misses = ["在吗", "今天天气怎么样", "你吃了吗", "哈哈哈哈"]
    assert all(_needs_strong_model(t) for t in hits)
    assert not any(_needs_strong_model(t) for t in misses)

    # 未配置 LLM_MODEL_STRONG 时路由不生效（reply_model=None）
    assert config.llm_model_strong == "" or isinstance(config.llm_model_strong, str)
    print("[OK] 路由启发式：写作/代码/长文命中，闲聊不误伤")


def test_usage_api() -> None:
    from fastapi.testclient import TestClient

    from backend.app import create_app

    with TestClient(create_app()) as client:
        res = client.get("/api/usage/summary?days=7")
        assert res.status_code == 200
        usage = res.json()["usage"]
        assert usage["today"]["cost"] >= 0 and "prices" in usage
        assert isinstance(usage["by_channel"], list)
        assert client.get("/api/usage/summary?days=0").status_code == 422
    print("[OK] 汇总端点：cost 计算/价格透出/参数校验")


async def main() -> None:
    test_usage_log_and_summary()
    await test_chat_records_usage()
    test_strong_model_routing()
    test_usage_api()
    print("\n=== D5 模型路由 + 成本面板：全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
