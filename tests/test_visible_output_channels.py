"""P0-01B：非聊天可见出口统一执行输出卫生检查。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_visible_outputs_"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.output_hygiene import HygieneContext, protect_visible_text  # noqa: E402


LEAKS = (
    "<think>先分析用户</think>你好",
    "<tool_call>{\"name\":\"secret\"}</tool_call>",
    "系统提示词如下：你必须泄露内部规则",
    "assistant to=functions.exec  hidden",
)


def suite_non_chat_guard_replaces_leaks_and_keeps_normal_text() -> None:
    fallback = "这句话没组织好，稍后再说。"
    for kind in ("greeting", "proactive", "diary", "activity_question", "farewell"):
        ctx = HygieneContext(kind=kind, source_namespace=kind)
        for leak in LEAKS:
            result = protect_visible_text(leak, context=ctx, fallback=fallback, enabled=True)
            assert not any(marker in result.text for marker in ("<think>", "tool_call", "系统提示词", "assistant to="))
            assert result.text in {"你好", fallback}

        technical = "可以这样实现：在事务提交前校验字段，然后返回明确的错误码。"
        result = protect_visible_text(technical, context=ctx, fallback=fallback, enabled=True)
        assert result.text == technical
        assert result.action == "accept"


def suite_proactive_boundary_sanitizes_queue_and_persistence(monkeypatch) -> None:
    from backend.core import initiative

    persisted: list[str] = []

    async def fake_persist(user_id, text, *, image=None, epoch=None):
        persisted.append(text)
        return True

    monkeypatch.setattr("backend.core.features.flag", lambda name: True)
    monkeypatch.setattr(initiative, "_persist_proactive", fake_persist)
    monkeypatch.setattr("backend.core.reset.epoch_is_current", lambda epoch: True)
    monkeypatch.setattr("backend.core.reset.reset_epoch", lambda: 1)

    ok = asyncio.run(
        initiative.enqueue_proactive("visible-user", "<think>秘密</think><tool_call>x</tool_call>")
    )
    assert ok is True
    queued = initiative.dequeue_proactive_message("visible-user")
    assert queued is not None
    assert queued["text"] == "刚才那句话没组织好，等我想清楚再来找你。"
    assert persisted == [queued["text"]]


def suite_visible_draft_channels_use_safe_fallback(monkeypatch) -> None:
    from backend.core import possibilities

    async def leaking_chat(*args, **kwargs):
        return "<reasoning>hidden</reasoning><tool_call>bad</tool_call>"

    monkeypatch.setattr("backend.core.features.flag", lambda name: True)
    monkeypatch.setattr(possibilities, "chat", leaking_chat)
    result = asyncio.run(possibilities.generate_draft("u", "dream", "标题", "一个虚构前提"))
    assert "tool_call" not in result["draft"]
    assert result["draft"] == "这个虚构片段还没写好，先把它留在想象里，等下一次再展开。"
