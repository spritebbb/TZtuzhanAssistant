# -*- coding: utf-8 -*-
"""38项#22 兴趣源管线回归：授权域 CRUD / 拉取压缩入库 / 到期间隔 / 出牌候选过滤。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tz_feeds_"))

from backend.core import interest_feed as ifeeds  # noqa: E402
from backend.core.userdb import db  # noqa: E402

UID = "feeds-test-user"


def test_crud() -> int:
    fid = db.add_interest_feed(UID, "星穹铁道 版本资讯", "星铁")
    assert fid is not None
    assert db.add_interest_feed(UID, "星穹铁道 版本资讯") is None, "重复关键词应拒绝"
    rows = db.list_interest_feeds(UID)
    assert len(rows) == 1 and rows[0]["keywords"] == "星穹铁道 版本资讯"
    assert db.remove_interest_feed(UID, fid) is True
    assert db.list_interest_feeds(UID) == []
    print("[OK] 授权域 CRUD：登记/去重/删除")
    return 0


async def _pull_with(search_ret, llm_ret, *, feed_id: int, flag_on=True, guard_ok=True) -> int:
    async def fake_llm(*a, **k):
        return llm_ret

    with patch("backend.core.search.web_search", lambda kw, n=5: search_ret), \
         patch("backend.core.llm.chat", fake_llm), \
         patch("backend.core.features.flag", lambda name: flag_on), \
         patch("backend.core.cost_guard.check", lambda module: guard_ok):
        return await ifeeds.pull_feed(UID, feed_id, "测试关键词")


def test_pull_and_items() -> int:
    fid = db.add_interest_feed(UID, "拉取测试源")  # 真实 feed id（CRUD 已删除此前的 1 号）
    n = asyncio.run(_pull_with(
        [{"title": "星穹铁道3.6版本公布", "snippet": "新角色与新剧情"}],
        "星铁3.6要来了，新角色剧透了一半",
        feed_id=fid,
    ))
    assert n == 1
    items = db.unused_feed_items(UID)
    assert len(items) == 1 and "星铁" in items[0]["content"]
    db.mark_feed_item_used(UID, int(items[0]["id"]))
    assert db.unused_feed_items(UID) == []
    assert asyncio.run(_pull_with([{"title": "x"}], "ok文本", feed_id=fid, flag_on=False)) == 0
    assert asyncio.run(_pull_with([{"title": "x"}], "ok文本", feed_id=fid, guard_ok=False)) == 0
    assert asyncio.run(_pull_with([], "", feed_id=fid)) == 0
    assert asyncio.run(_pull_with([{"title": "x"}], "。", feed_id=fid)) == 0
    print("[OK] 拉取压缩入库 / flag 与成本闸 / 空与无价值不写 / 出牌置 used")
    return 0


def test_due_interval() -> int:
    fid = db.add_interest_feed(UID, "到期测试源") or 1
    db.set_feed_pulled(fid)  # 刚拉过 → 不到期
    assert asyncio.run(ifeeds.pull_due_feeds(UID)) == 0
    with db._lock:  # 拨回 76h 前 → 到期（mock 搜索空结果，零写入）
        db.conn.execute(
            "UPDATE interest_feeds SET last_pull_at=? WHERE id=?",
            ((datetime.now() - timedelta(hours=76)).isoformat(timespec="seconds"), fid),
        )
        db.conn.commit()
    before = datetime.now() - timedelta(minutes=1)
    with patch("backend.core.search.web_search", lambda kw, n=5: []):
        asyncio.run(ifeeds.pull_due_feeds(UID))
    with db._lock:
        fresh = db.conn.execute("SELECT last_pull_at FROM interest_feeds WHERE id=?", (fid,)).fetchone()
    assert fresh["last_pull_at"] > before.isoformat(timespec="seconds"), "到期应重拉并刷新时间戳"
    print("[OK] 72h 到期间隔：未到期不拉、到期重拉刷新")
    return 0


def main() -> None:
    test_crud()
    test_pull_and_items()
    test_due_interval()
    print("\n=== 38项#22 兴趣源: 3 组全部通过 ===")


if __name__ == "__main__":
    main()
