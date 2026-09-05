# -*- coding: utf-8 -*-
"""统一主动仲裁器：跨源共享每日额度、一轮单发、失败冷却与专注静默。"""
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

from backend.core import focus, initiative, proactive_policy as policy  # noqa: E402
from backend.core.userdb import db, get_due_promises, save_promise  # noqa: E402

UID = "arbiter-user-1"


def _make_user(uid: str) -> None:
    db.ensure_user(uid)
    db.set_affection_absolute(uid, 60)  # 熟悉：允许 confirm_memory 心事


def _due_promise(uid: str, content: str) -> None:
    save_promise(uid, content, follow_up=date.today().isoformat())


def _due_thought(uid: str, content: str) -> None:
    import datetime as _dt

    now = _dt.datetime.now().isoformat(timespec="seconds")
    with db._lock:
        db.conn.execute(
            "INSERT INTO pending_thoughts (user_id, kind, source_type, source_id, content, "
            "earliest_at, priority, created_at) VALUES (?, 'confirm_memory', 'fact', 555, ?, ?, 3, ?)",
            (uid, content, now, now),
        )
        db.conn.commit()


async def _test_shared_daily_quota() -> None:
    _make_user(UID)
    _due_promise(UID, "用户说要把代码发给菟菚看")
    _due_thought(UID, "想确认上次纠偏的记忆记对了没")

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
        assert policy.active_count_today(UID) == 1, "次级源现在必须消耗共享每日额度"
        # 同日心事源即使有到点心事，也拿不到占位（统一额度）
        assert await initiative.maybe_express_pending_thoughts(UID) is None
        # 通用主动同日同样被额度拦住
        assert policy.try_claim_active(UID, "initiative-loop") is None
    assert len(sent) == 1
    assert get_due_promises(UID, date.today()) == [], "跟进成功后约定应标记完成"
    print("[OK] 统一额度：次级源走原子占位并消耗每日额度，跨源去重生效")


async def _test_single_send_per_tick() -> None:
    uid = "arbiter-user-2"
    _make_user(uid)
    _due_promise(uid, "用户答应周五给 demo")
    _due_thought(uid, "想问问读到哪了")

    async def fake_chat(messages, **kwargs):
        return "demo 好了吗"

    sent: list[str] = []

    async def fake_enqueue(user_id, text, image=None, epoch=None):
        sent.append(text)
        return True

    with patch("backend.core.initiative.chat", new=fake_chat), \
         patch("backend.core.initiative.enqueue_proactive", new=fake_enqueue):
        assert await initiative._arbitrate_secondary(uid) is True
    assert len(sent) == 1, f"同一轮至多发一条，实际 {len(sent)} 条"
    assert sent[0] == "demo 好了吗", "约定跟进优先级高于心事表达"
    print("[OK] 一轮单发：多源同时到点时按优先级只出一张牌")


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
            assert await initiative.maybe_follow_up_promise(uid) is None, "专注进行中次级源必须静默"
        assert get_due_promises(uid, date.today()), "静默不能消耗约定"
    finally:
        focus.cancel_focus(uid, session["id"])
    print("[OK] 安静模式：专注进行中所有次级源静默，不消耗任何条件")


async def main() -> None:
    await _test_shared_daily_quota()
    await _test_single_send_per_tick()
    await _test_failure_cooldown()
    await _test_focus_quiet_mode()
    print("统一主动仲裁器测试通过")


if __name__ == "__main__":
    asyncio.run(main())
