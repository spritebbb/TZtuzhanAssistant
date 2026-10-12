# -*- coding: utf-8 -*-
"""主动引擎：约定到点跟进、每日额度、失败冷却与专注静默。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_arbiter_"))
os.environ.setdefault("MEMORY_V2", "0")
# 这组契约验证「额度为 1 时一次主动」。不能继承开发机 .env 的个性化上限。
os.environ["PROACTIVE_DAILY_MAX"] = "1"

from backend.core import focus, initiative, proactive_policy as policy  # noqa: E402

from backend.core import features  # noqa: E402
# D12：意愿骰子会引入按分钟波动的随机性；意愿层由 test_willingness_roll 覆盖，
# 这里关掉保持确定性。
features.set_flag("willingness_enabled", False)
from backend.core.userdb import db, get_due_promises, save_promise  # noqa: E402

UID = "arbiter-user-1"


def _make_user(uid: str) -> None:
    db.ensure_user(uid)
    db.set_affection_absolute(uid, 60)  # 熟悉


def _due_promise(uid: str, content: str) -> None:
    save_promise(uid, content, follow_up=date.today().isoformat())


async def _test_daily_quota() -> None:
    _make_user(UID)
    _due_promise(UID, "用户说要把代码发给菟菚看")

    async def fake_chat(messages, **kwargs):
        return "说好的代码呢，我可还记着"

    sent: list[str] = []

    async def fake_enqueue(user_id, text, image=None, epoch=None):
        sent.append(text)
        return True

    with patch("backend.core.initiative.chat", new=fake_chat), \
         patch("backend.core.initiative.enqueue_proactive", new=fake_enqueue):
        text = await initiative.maybe_follow_up_promise(UID)
        assert text == "说好的代码呢，我可还记着"
        assert policy.active_count_today(UID) == 1, "主动源必须消耗共享每日额度"
        # 通用主动同日被额度拦住
        assert policy.try_claim_active(UID, "initiative-loop") is None
    assert len(sent) == 1
    assert get_due_promises(UID, date.today()) == [], "跟进成功后约定应标记完成"
    print("[OK] 每日额度：约定跟进消耗额度，同日通用主动被拦")


async def _test_failure_cooldown() -> None:
    uid = "arbiter-user-3"
    _make_user(uid)
    _due_promise(uid, "用户说过要一起联机")

    async def broken_chat(messages, **kwargs):
        raise RuntimeError("LLM 不可用")

    with patch("backend.core.initiative.chat", new=broken_chat):
        assert await initiative.maybe_follow_up_promise(uid) is None
    assert get_due_promises(uid, date.today()), "生成失败时约定不能被标记完成"

    async def fine_chat(messages, **kwargs):
        return "什么时候联机"

    with patch("backend.core.initiative.chat", new=fine_chat):
        assert await initiative.maybe_follow_up_promise(uid) is None, "失败冷却期内不应重试"
    print("[OK] 失败冷却：生成失败留冷却标记且不落账，冷却期内不重试")


async def _test_focus_quiet_mode() -> None:
    uid = "arbiter-user-4"
    _make_user(uid)
    _due_promise(uid, "用户说周末去爬山")
    session = focus.start_focus(uid, 25)
    try:
        async def fine_chat(messages, **kwargs):
            return "爬山怎样了"

        with patch("backend.core.initiative.chat", new=fine_chat):
            assert await initiative.maybe_follow_up_promise(uid) is None, "专注进行中必须静默"
        assert get_due_promises(uid, date.today()), "静默不能消耗约定"
    finally:
        focus.cancel_focus(uid, session["id"])
    print("[OK] 安静模式：专注进行中主动源静默，不消耗任何条件")


async def _test_arbitrate_secondary_empty() -> None:
    uid = "arbiter-chain"
    _make_user(uid)
    # 无待办来源时整条链干净返回 False（不抛异常）
    assert await initiative._arbitrate_secondary(uid) is False
    print("[OK] 次级链：无到点约定时干净返回 False")


async def main() -> None:
    await _test_daily_quota()
    await _test_failure_cooldown()
    await _test_focus_quiet_mode()
    await _test_arbitrate_secondary_empty()
    print("主动引擎测试通过")


if __name__ == "__main__":
    asyncio.run(main())
