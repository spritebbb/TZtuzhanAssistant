# -*- coding: utf-8 -*-
"""M1 临时对话：回复可完成，但不留下任何关系或记忆副作用。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["TZTUZHAN_DATA_DIR"] = tempfile.mkdtemp(prefix="tztuzhan_test_ephemeral_")
os.environ["MEMORY_V2"] = "0"
os.environ["MOOD_CITY"] = ""
os.environ["SEARCH_ENABLED"] = "0"

from backend.core import pipeline  # noqa: E402
from backend.core.privacy import is_ephemeral_request  # noqa: E402
from backend.core.userdb import db  # noqa: E402

UID = "assistant-main"


def _user_scoped_counts(user_id: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    with db._lock:
        tables = [
            str(row[0])
            for row in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        for table in tables:
            columns = {
                str(row[1]) for row in db.conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            }
            if "user_id" not in columns:
                continue
            row = db.conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE user_id = ?', (user_id,)
            ).fetchone()
            counts[table] = int(row[0])
    return counts


async def _test_ephemeral_pipeline_has_no_side_effects() -> None:
    db.ensure_user(UID)
    before_counts = _user_scoped_counts(UID)
    before_user = dict(db.get_user(UID))
    calls: list[list[dict]] = []

    async def fake_chat(messages, **kwargs):
        calls.append(messages)
        return "【思考】先陪着。\n【回复】我在，慢慢说。"

    with patch("backend.core.pipeline.chat", new=fake_chat):
        reply = await pipeline.process(
            UID,
            "陪我说完但别记住这件事：我今天有点难受",
            mock=True,
        )

    assert reply == "我在，慢慢说。"
    assert _user_scoped_counts(UID) == before_counts
    assert dict(db.get_user(UID)) == before_user
    assert calls and any(
        "临时对话" in str(message.get("content", ""))
        for message in calls[0]
        if message.get("role") == "system"
    )
    print("[OK] 临时对话：正常回复，但 userdb 所有用户域表与用户状态均零变化")


async def _test_explicit_flag_and_normal_control() -> None:
    uid = "privacy-explicit"
    db.ensure_user(uid)
    before = _user_scoped_counts(uid)

    async def fake_chat(messages, **kwargs):
        return "【思考】好。\n【回复】只留在这一轮。"

    with patch("backend.core.pipeline.chat", new=fake_chat):
        await pipeline.process(uid, "这里没有触发词", mock=True, ephemeral=True)
    assert _user_scoped_counts(uid) == before

    normal_uid = "privacy-normal-control"
    db.ensure_user(normal_uid)
    with patch("backend.core.pipeline.chat", new=fake_chat):
        await pipeline.process(normal_uid, "普通聊天", mock=True)
    assert len(db.recent_messages(normal_uid, 10)) == 2
    print("[OK] 显式开关不落痕；普通聊天仍按原契约持久化")


async def _test_ephemeral_new_user_and_plugin_boundary() -> None:
    uid = "privacy-never-persisted"
    assert db.get_user(uid) is None

    async def fake_chat(messages, **kwargs):
        return "【思考】好。\n【回复】这一轮不留下。"

    with (
        patch("backend.core.pipeline.chat", new=fake_chat),
        patch("backend.plugins.context.apply_user_message") as user_hook,
        patch("backend.plugins.context.apply_reply") as reply_hook,
        patch("backend.plugins.context.system_prompt_contributions") as prompt_hook,
    ):
        reply = await pipeline.process(uid, "临时聊一下", mock=True)

    assert reply == "这一轮不留下。"
    assert db.get_user(uid) is None
    assert not any(_user_scoped_counts(uid).values())
    user_hook.assert_not_called()
    reply_hook.assert_not_called()
    prompt_hook.assert_not_called()
    print("[OK] 新用户临时轮不建档；插件输入/提示/输出钩子全部隔离")


def _test_detection_boundaries() -> None:
    assert is_ephemeral_request("这件事别记住")
    assert is_ephemeral_request("临时聊一下")
    assert is_ephemeral_request("普通内容", explicit=True)
    assert not is_ephemeral_request("别忘记提醒我保存文件")
    assert not is_ephemeral_request("普通聊天")
    print("[OK] 临时语义检测：覆盖明确表达，不误伤‘别忘记保存’")


async def main() -> None:
    _test_detection_boundaries()
    await _test_ephemeral_pipeline_has_no_side_effects()
    await _test_explicit_flag_and_normal_control()
    await _test_ephemeral_new_user_and_plugin_boundary()
    print("\n=== M1 临时对话隐私回归全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
