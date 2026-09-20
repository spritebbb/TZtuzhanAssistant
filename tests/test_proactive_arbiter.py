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
# 这组契约专门验证「额度为 1 时跨源共享」。不能继承开发机 .env 的个性化
# 上限，否则本机配置为 2 时第二个来源本来就应当获准，测试会产生假失败。
os.environ["PROACTIVE_DAILY_MAX"] = "1"

from backend.core import focus, initiative, proactive_policy as policy  # noqa: E402

from backend.core import features  # noqa: E402
# D12：本组契约断言「投递成功」，主动意愿骰子会引入按分钟波动的随机性；
# 意愿层自身由 test_willingness_roll 覆盖，这里关掉保持确定性。
features.set_flag("willingness_enabled", False)
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


async def _test_secondary_chain_awaitable() -> None:
    """回归：次级链每个来源都必须可 await。

    历史缺陷：L06 外出候选用同步 def 包装同步函数，链上 `await proposer(...)`
    对 str/None 直接抛 TypeError，被上层静默捕获——整条次级链（含 G04 求助、
    惊喜编排）在该环之后全部失效。此用例锁死「链上返回文案时能正常送达」。
    """
    uid = "arbiter-chain"
    _make_user(uid)
    # 无任何待办来源时，整条链必须干净返回 False（不抛异常）
    assert await initiative._arbitrate_secondary(uid) is False

    sent: list[str] = []
    marked: list[str] = []

    async def fake_enqueue(user_id, text, image=None, epoch=None):
        sent.append(text)
        return True

    with patch("backend.core.life_templates.maybe_express_outing",
               new=lambda u, **k: "刚爬完山回来，腿有点酸"), \
         patch("backend.core.life_templates.outing_expressed_today", new=lambda u: False), \
         patch("backend.core.life_templates.mark_outing_expressed", new=marked.append), \
         patch("backend.core.initiative.enqueue_proactive", new=fake_enqueue):
        assert await initiative._arbitrate_secondary(uid) is True, \
            "外出候选必须能被 await 并作为本轮出牌"
    assert sent == ["刚爬完山回来，腿有点酸"], sent
    assert marked == [uid], marked
    print("[OK] 次级链可 await，外出候选投递后才写去重键")


async def main() -> None:
    await _test_shared_daily_quota()
    await _test_single_send_per_tick()
    await _test_failure_cooldown()
    await _test_focus_quiet_mode()
    await _test_secondary_chain_awaitable()
    print("统一主动仲裁器测试通过")


if __name__ == "__main__":
    asyncio.run(main())
