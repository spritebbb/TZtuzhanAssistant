# -*- coding: utf-8 -*-
"""岗位三回归：她的自述记忆（保守提取、conflict 双条并存不取代、提示一次即
resolved、fail-open、话题相关才注入）。LLM 全 mock。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztzhan_her_"))

UID = "u_her"
DAY = date(2026, 9, 30)


def _run(coro):
    return asyncio.run(coro)


def _rows(pairs):
    return [{"role": r, "content": c, "id": i} for i, (r, c) in enumerate(pairs)]


def test_extract_conflict_and_hint() -> int:
    from backend.core import her_statements as hs
    from backend.core.userdb import db

    db.ensure_user(UID)
    # 第一天：她说研究所吵得睡不好
    day1 = _rows([("user", "你晚上睡得好吗"), ("assistant", "研究所通风管道吵得很，我睡不好")])
    extract1 = '{"statements": [{"topic": "研究所", "content": "研究所通风管道吵得很，我睡不好", "msg_idx": 1}]}'
    with patch("backend.core.llm.chat", new=AsyncMock(return_value=extract1)):
        n = _run(hs.extract_her_statements(UID, DAY, day1))
    assert n == 1, f"第一天应新增 1 条，实际 {n}"
    stmts = db.list_her_statements(UID)
    assert len(stmts) == 1 and stmts[0]["conflict_group"] is None

    # 第二天：她改口研究所安静——judge 报矛盾 → 双条同组共存（不取代）
    day2 = _rows([("user", "今天忙啥了"), ("assistant", "研究所安静得很，我在那补了一觉")])
    extract2 = '{"statements": [{"topic": "研究所", "content": "研究所安静得很，我在那补了一觉", "msg_idx": 1}]}'
    verdict = '{"conflicts": [{"idx": 0, "with_id": %d, "reason": "吵vs安静不相容"}]}' % stmts[0]["id"]
    with patch("backend.core.llm.chat",
               new=AsyncMock(side_effect=[extract2, verdict])):
        n = _run(hs.extract_her_statements(UID, DAY, day2))
    assert n == 1
    both = db.list_her_statements(UID, active_only=False)
    groups = {s["conflict_group"] for s in both}
    assert len(both) == 2 and len(groups) == 1 and None not in groups, "矛盾应双条同组共存"

    # 话题相关时提示一次；同话题再次触发不再提示（resolved）
    hint = hs.pending_conflict_hint(UID, "你们研究所环境怎么样，吵不吵")
    assert hint is not None and "通风管道" in hint and "安静" in hint, hint
    assert "不要主动" in hint, "演绎决定权提示必须在场"
    assert hs.pending_conflict_hint(UID, "你们研究所环境怎么样，吵不吵") is None, "一次交付后不得重复"
    print("[OK] 提取→矛盾同组共存→话题相关提示一次→resolved 不再盘问")
    return 0


def test_no_conflict_and_failopen() -> int:
    from backend.core import her_statements as hs
    from backend.core.userdb import db

    UID2 = UID + "_nc"
    db.ensure_user(UID2)
    day = _rows([("user", "hi"), ("assistant", "我喜欢观察排队的人类")])
    extract = '{"statements": [{"topic": "喜好", "content": "我喜欢观察排队的人类", "msg_idx": 1}]}'
    # judge 无矛盾
    with patch("backend.core.llm.chat",
               new=AsyncMock(side_effect=[extract, '{"conflicts": []}'])):
        n = _run(hs.extract_her_statements(UID2, DAY, day))
    assert n == 1 and db.list_her_statements(UID2)[0]["conflict_group"] is None
    # judge 挂了 → fail-open 无矛盾入库
    UID3 = UID + "_fo"
    db.ensure_user(UID3)
    with patch("backend.core.llm.chat",
               new=AsyncMock(side_effect=[extract, RuntimeError("boom")])):
        n = _run(hs.extract_her_statements(UID3, DAY, day))
    assert n == 1, "fail-open：提取的陈述按无矛盾入库"
    # 无关话题不注入
    assert hs.pending_conflict_hint(UID, "今天吃什么") is None
    print("[OK] 无矛盾零标记 + judge 失败 fail-open + 无关话题零注入")
    return 0


def test_conservative_extraction() -> int:
    """转述/共情/超上限拒收：提取纪律走 mock 验证裁剪逻辑（msg 越界→None 溯源）。"""
    from backend.core import her_statements as hs
    from backend.core.userdb import db

    UID4 = UID + "_cons"
    db.ensure_user(UID4)
    day = _rows([("user", "你说得对，我也讨厌下雨"), ("assistant", "嗯，下雨天确实烦人")])
    extract = '{"statements": [{"topic": "厌恶", "content": "我讨厌下雨", "msg_idx": 99}]}'
    with patch("backend.core.llm.chat", new=AsyncMock(return_value=extract)):
        n = _run(hs.extract_her_statements(UID4, DAY, day))
    row = db.list_her_statements(UID4)[0]
    assert n == 1 and row["message_id"] is None, "越界 msg_idx 应回退 None 而不是崩"
    # topic 白名单外 → 归「其他」
    extract2 = '{"statements": [{"topic": "宇宙哲学玄学", "content": "我最近在琢磨点别的", "msg_idx": 0}]}'
    with patch("backend.core.llm.chat", new=AsyncMock(return_value=extract2)):
        _run(hs.extract_her_statements(UID4, DAY, day))
    assert any(s["topic"] == "其他" for s in db.list_her_statements(UID4))
    print("[OK] 保守性边界：越界溯源回退 + 非法 topic 归其他")
    return 0


def main() -> int:
    test_extract_conflict_and_hint()
    test_no_conflict_and_failopen()
    test_conservative_extraction()
    print("\n=== 岗位三·她的自述记忆：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
