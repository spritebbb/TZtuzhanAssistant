# -*- coding: utf-8 -*-
"""休息沉默、连续消息唤醒及真实聊天管线回归。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_sleep_gate_"))
# 本套件测的就是睡眠门控：弹掉 conftest 的测试豁免，验证真实门控行为
os.environ.pop("TZTUZHAN_NO_SLEEP_GATE", None)

from backend.core import sleep_gate
from backend.core.userdb import db


def test_three_messages_wake_and_expire() -> None:
    uid = "sleep-gate-unit"
    db.ensure_user(uid)
    now = datetime.now().astimezone()
    with patch.object(sleep_gate, "_resting_now", return_value=True):
        assert sleep_gate.before_user_message(uid, now=now) == "silent"
        db.add_message(uid, "user", "第一条")
        assert sleep_gate.before_user_message(uid, now=now + timedelta(minutes=1)) == "silent"
        db.add_message(uid, "user", "第二条")
        assert sleep_gate.before_user_message(uid, now=now + timedelta(minutes=2)) == "woke"
        assert sleep_gate.is_awake(uid, now=now + timedelta(minutes=2))
        from backend.core import presence
        with patch.object(presence, "_presence", return_value="rest"):
            assert presence.current_presence(uid, now=now + timedelta(minutes=2)) == "home"
            assert presence.current_presence(
                uid, now=now + timedelta(minutes=2), include_wake=False
            ) == "rest"
        assert sleep_gate.before_user_message(uid, now=now + timedelta(minutes=20)) == "active"
        assert not sleep_gate.is_awake(uid, now=now + timedelta(minutes=51))
    print("[OK] 十分钟内第三条唤醒 + 可聊窗口续期与过期")


async def test_pipeline_silences_then_wakes() -> None:
    from backend.core import pipeline

    uid = "sleep-gate-pipeline"
    db.ensure_user(uid)
    db.set_first_chat_done(uid)
    captured: list[list[dict]] = []

    async def fake_chat(messages, **kwargs):
        captured.append(messages)
        return "……被你吵醒了。怎么了？"

    with patch.object(sleep_gate, "_resting_now", return_value=True), \
         patch("backend.core.pipeline.chat", new=fake_chat):
        assert await pipeline.process(uid, "在吗", mock=True) == ""
        assert await pipeline.process(uid, "醒醒", mock=True) == ""
        reply = await pipeline.process(uid, "真有事找你", mock=True)

    assert reply and len(captured) == 1, "前两条不得调用 LLM，第三条才生成回复"
    systems = [m["content"] for m in captured[0] if m["role"] == "system"]
    assert any("连续发来的多条消息吵醒" in line for line in systems)
    rows = db.recent_messages(uid, 4)
    assert [row["role"] for row in rows] == ["user", "user", "user", "assistant"]
    assert captured[0][-1]["role"] == "user"
    print("[OK] 真实 pipeline：两轮沉默零 LLM，第三轮携带唤醒提示回复")


def main() -> None:
    test_three_messages_wake_and_expire()
    asyncio.run(test_pipeline_silences_then_wakes())
    print("\n=== 休息唤醒门控：全部通过 ===")


if __name__ == "__main__":
    main()
