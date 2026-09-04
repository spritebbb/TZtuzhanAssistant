# -*- coding: utf-8 -*-
"""C3 纪念日预谋：日期过滤单元 + 前夜「心不在焉」/当天「格外主动」注入。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 数据目录隔离：先于任何 backend import 生效，避免读写真实 bot.db
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_eve_"))

from backend.core.userdb import db, get_dates_for, save_important_date

UID = "eve-test-user"


def _seed_dates() -> None:
    db.ensure_user(UID)
    # birthday：带出生年份也应每年触发；anniversary 同理
    save_important_date(UID, "06-15", "他的生日", kind="birthday", year=1995)
    save_important_date(UID, "06-15", "相识纪念日", kind="anniversary", year=2025)
    # other：未标年份 → 每年都触发；标了年份 → 仅当年触发
    save_important_date(UID, "06-15", "一起看演唱会", kind="other")
    save_important_date(UID, "06-15", "2030 年的约定", kind="other", year=2030)


def test_get_dates_for_year_filter() -> None:
    _seed_dates()
    d2026 = {r["label"] for r in get_dates_for(UID, date(2026, 6, 15))}
    assert d2026 == {"他的生日", "相识纪念日", "一起看演唱会"}, d2026
    d2030 = {r["label"] for r in get_dates_for(UID, date(2030, 6, 15))}
    assert d2030 == {"他的生日", "相识纪念日", "一起看演唱会", "2030 年的约定"}, d2030
    d_other = get_dates_for(UID, date(2026, 6, 16))
    assert d_other == [], d_other
    print("[OK] get_dates_for：birthday/anniversary 每年触发，other 按年份过滤")


async def test_eve_and_dayof_injection() -> None:
    from backend.core import affection
    from backend.core.pipeline import process

    today = date.today()
    tomorrow = today + timedelta(days=1)
    save_important_date(UID, today.strftime("%m-%d"), "今天的纪念日", kind="anniversary")
    save_important_date(UID, tomorrow.strftime("%m-%d"), "明天的生日", kind="birthday")

    affection.set_affection(UID, 60)  # 亲密阶段，避免初识称呼流程干扰
    db.set_first_chat_done(UID)

    captured: list[list[dict]] = []

    async def fake_chat(messages, **kwargs):
        captured.append(messages)
        return "【思考】内部\n【回复】嗯，今天天气不错"

    with patch("backend.core.pipeline.chat", new=fake_chat):
        await process(UID, "在吗", mock=True)

    assert captured, "pipeline 未调用主 chat"
    messages = captured[0]
    systems = [m["content"] for m in messages if m["role"] == "system"]
    assert messages[-1]["role"] == "user", "user 消息必须位于最后"

    dayof = [s for s in systems if "今天的纪念日" in s]
    assert dayof, "当天特殊日子注入缺失"
    assert "从昨天起就记着" in dayof[0], "当天注入未升级为「格外主动」版本"

    eve = [s for s in systems if "明天的生日" in s]
    assert eve, "前夜预谋注入缺失"
    assert "不要直接说破" in eve[0], "前夜注入应要求「不说破、只透出期待」"

    # 两条注入都必须出现在 user 之前（user-last 原则）
    last_user_idx = max(i for i, m in enumerate(messages) if m["role"] == "user")
    for s in (dayof[0], eve[0]):
        idx = next(i for i, m in enumerate(messages) if m["role"] == "system" and m["content"] == s)
        assert idx < last_user_idx, "特殊日子注入必须在 user 消息之前"
    print("[OK] 前夜「心不在焉」+ 当天「格外主动」注入生效且遵守 user-last")


async def main() -> None:
    test_get_dates_for_year_filter()
    await test_eve_and_dayof_injection()
    print("\n=== C3 纪念日预谋：全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
