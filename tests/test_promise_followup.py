# -*- coding: utf-8 -*-
"""C6 约定与跟进：promises 表 CRUD、到期过滤、约定提炼、主动跟进与对话内注入。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 数据目录隔离：先于任何 backend import 生效，避免读写真实 bot.db
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_promise_"))

from backend.core.userdb import (
    db,
    get_due_promises,
    get_open_promises,
    mark_promise_done,
    save_promise,
)

UID = "promise-test-user"


def test_promise_crud_and_due_filter() -> None:
    db.ensure_user(UID)
    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()
    tomorrow = (today + timedelta(days=1)).isoformat()

    id1 = save_promise(UID, "用户答应周五把 demo 发给菟菚看", follow_up=yesterday)
    assert id1 is not None
    # 同内容去重；但可补全跟进日期
    assert save_promise(UID, "用户答应周五把 demo 发给菟菚看") is None
    id2 = save_promise(UID, "约好下周一起联机", follow_up="")
    assert save_promise(UID, "约好下周一起联机", follow_up=tomorrow) is None  # 补全日期
    id3 = save_promise(UID, "用户说生日要第一个告诉菟菚", follow_up=tomorrow)
    assert id2 and id3

    due = get_due_promises(UID, today)
    assert [p["content"] for p in due] == ["用户答应周五把 demo 发给菟菚看"], due
    open_all = {p["content"] for p in get_open_promises(UID)}
    assert open_all == {"用户答应周五把 demo 发给菟菚看", "约好下周一起联机", "用户说生日要第一个告诉菟菚"}
    # 「约好下周一起联机」的 follow_up 应被第二次调用补全为明天
    p2 = [p for p in get_open_promises(UID) if p["content"] == "约好下周一起联机"][0]
    assert p2["follow_up"] == tomorrow, p2

    mark_promise_done(id1)
    assert get_due_promises(UID, today) == []
    print("[OK] promises：去重/日期补全/到期过滤/完成标记")


async def test_extract_promises() -> None:
    from backend.core import daily

    async def fake_chat(messages, **kwargs):
        assert "约定记录员" in messages[0]["content"]
        return json.dumps({"promises": [
            {"content": "用户答应明天把照片给菟菚看", "follow_up": date.today().isoformat()},
            {"content": "", "follow_up": ""},  # 空内容应被丢弃
        ]}, ensure_ascii=False)

    daily.chat = fake_chat
    added = await daily.extract_promises(UID, date.today(), "user: 明天把照片给你看\nbot: 我记住了")
    assert added == 1, added
    # 幂等：再次提炼同内容不重复
    added2 = await daily.extract_promises(UID, date.today(), "user: 明天把照片给你看\nbot: 我记住了")
    assert added2 == 0, added2
    print("[OK] 约定提炼：解析/落库/幂等")


async def test_proactive_followup() -> None:
    from backend.core import initiative

    # 已被前两个测试留下的到期约定：「用户答应明天把照片给菟菚看」(follow_up=今天)
    due = get_due_promises(UID, date.today())
    assert len(due) == 1, due

    async def fake_chat(messages, **kwargs):
        assert "不像催债" in messages[-1]["content"]
        return "照片呢，我可还记着"

    sent: list[str] = []

    async def fake_enqueue(user_id, text, image=None, epoch=None):
        sent.append(text)
        return True

    with patch("backend.core.initiative.chat", new=fake_chat), \
         patch("backend.core.initiative.enqueue_proactive", new=fake_enqueue):
        text = await initiative.maybe_follow_up_promise(UID)
        assert text == "照片呢，我可还记着", text
        # 每日一次：当天第二次不再跟进
        assert await initiative.maybe_follow_up_promise(UID) is None

    assert sent == ["照片呢，我可还记着"]
    assert get_due_promises(UID, date.today()) == [], "跟进后约定应标记为 done"
    print("[OK] 主动跟进：生成/投递/每日去重/标记完成")


async def test_pipeline_promise_injection() -> None:
    from backend.core import affection
    from backend.core.pipeline import process

    save_promise(UID, "用户说过这周要戒烟", follow_up=date.today().isoformat())
    affection.set_affection(UID, 60)
    db.set_first_chat_done(UID)

    captured: list[list[dict]] = []

    async def fake_chat(messages, **kwargs):
        captured.append(messages)
        return "【思考】内部\n【回复】嗯"

    with patch("backend.core.pipeline.chat", new=fake_chat):
        await process(UID, "在吗", mock=True)

    assert captured, "pipeline 未调用主 chat"
    messages = captured[0]
    systems = [m["content"] for m in messages if m["role"] == "system"]
    hit = [s for s in systems if "用户说过这周要戒烟" in s and "不像催债" in s]
    assert hit, "到期约定未注入对话上下文"
    idx_sys = next(i for i, m in enumerate(messages) if m.get("content") == hit[0])
    last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
    assert idx_sys < last_user, "约定注入必须在 user 消息之前"
    print("[OK] 对话内注入：到期约定 + 不像催债 + user-last")


async def main() -> None:
    test_promise_crud_and_due_filter()
    await test_extract_promises()
    await test_proactive_followup()
    await test_pipeline_promise_injection()
    print("\n=== C6 约定与跟进：全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
