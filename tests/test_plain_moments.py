# -*- coding: utf-8 -*-
"""38项#26 回归：平淡时刻入账（flag 关不提取、<6 条早退、低权写 facts、
14 天过期、空返回、LLM 失败静默）。LLM 全 mock。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztzhan_plain_"))

UID = "u_plain"
DAY = date(2026, 9, 30)


def _run(coro):
    return asyncio.run(coro)


def _rows(pairs):
    return [{"role": r, "content": c, "id": i} for i, (r, c) in enumerate(pairs)]


def _plain_facts(uid):
    from backend.core.userdb import db

    with db._lock:
        return db.conn.execute(
            "SELECT * FROM facts WHERE user_id=? AND source_type='plain_moment'",
            (uid,),
        ).fetchall()


def _pairs(n):
    roles = ("user", "assistant")
    return [(roles[i % 2], f"第{i}轮：聊服务器选择的第{i}个细节") for i in range(n)]


def test_flag_off_and_too_quiet() -> int:
    from backend.core import plain_moments as pm
    from backend.core.features import set_flag

    rows = _rows(_pairs(8))
    extract = '{"moments": ["聊了两小时星穹服务器的选区"]}'
    set_flag("plain_moments_enabled", False)
    try:
        with patch("backend.core.llm.chat", new=AsyncMock(return_value=extract)):
            assert _run(pm.extract_plain_moments(UID, DAY, rows)) == 0, "flag 关不得提取"
        assert not _plain_facts(UID), "flag 关时 facts 表不得出现 plain_moment 行"
    finally:
        set_flag("plain_moments_enabled", True)
    # 消息 <6 条：早退（mock 计数验证根本没调 LLM）
    quiet = _rows([("user", "早"), ("assistant", "嗯"), ("user", "睡了？"), ("assistant", "嗯")])
    m = AsyncMock(return_value=extract)
    with patch("backend.core.llm.chat", new=m):
        assert _run(pm.extract_plain_moments(UID, DAY, quiet)) == 0, "太平淡的日子不值得入账"
    assert m.await_count == 0, "早退路径不得发起 LLM 调用"
    print("[OK] flag 关不提取 + 消息<6 条早退且零 LLM 调用")
    return 0


def test_extract_writes_low_weight_facts() -> int:
    from backend.core import plain_moments as pm
    from backend.core.userdb import db

    db.ensure_user(UID)
    rows = _rows(_pairs(8))
    extract = ('{"moments": ["聊了两小时星穹铁道的服务器选择",'
               '"一起把阳台的多肉换了个大盆"]}')
    with patch("backend.core.llm.chat", new=AsyncMock(return_value=extract)):
        n = _run(pm.extract_plain_moments(UID, DAY, rows))
    assert n == 2, f"应新增 2 条，实际 {n}"
    facts = _plain_facts(UID)
    assert len(facts) == 2, f"facts 表应出现 2 行 plain_moment，实际 {len(facts)}"
    contents = {f["content"] for f in facts}
    assert contents == {"聊了两小时星穹铁道的服务器选择", "一起把阳台的多肉换了个大盆"}
    expect_expire = (
        datetime.combine(DAY, dtime.min) + timedelta(days=14)
    ).isoformat(timespec="seconds")
    for f in facts:
        assert f["confidence"] == 0.5, "平淡时刻是低权事实"
        assert f["status"] == "active"
        assert f["expires_at"] == expect_expire, f"保留期应为对话日+14天，实际 {f['expires_at']}"
        assert f["source_message_ids"] == "[0,1,2,3,4,5,6,7]", "溯源到当日消息 id"
    # 同一天重跑：二元组重叠去重，不重复入账
    with patch("backend.core.llm.chat", new=AsyncMock(return_value=extract)):
        assert _run(pm.extract_plain_moments(UID, DAY, rows)) == 0, "重复内容应被 add_fact 去重"
    assert len(_plain_facts(UID)) == 2
    print("[OK] 2 条低权入账（confidence=0.5、expires_at=day+14d、溯源、去重）")
    return 0


def test_empty_and_llm_failure_silent() -> int:
    from backend.core import plain_moments as pm
    from backend.core.userdb import db

    UID2 = UID + "_empty"
    db.ensure_user(UID2)
    rows = _rows(_pairs(8))
    with patch("backend.core.llm.chat", new=AsyncMock(return_value='{"moments": []}')):
        assert _run(pm.extract_plain_moments(UID2, DAY, rows)) == 0
    assert not _plain_facts(UID2), "空返回不得写 facts"
    # LLM 挂 → 静默 0，不抛
    with patch("backend.core.llm.chat", new=AsyncMock(side_effect=RuntimeError("boom"))):
        assert _run(pm.extract_plain_moments(UID2, DAY, rows)) == 0
    # 返回非 JSON 垃圾 → 同样静默 0
    with patch("backend.core.llm.chat", new=AsyncMock(return_value="我今天不想输出 JSON")):
        assert _run(pm.extract_plain_moments(UID2, DAY, rows)) == 0
    assert not _plain_facts(UID2)
    print("[OK] 空返回 0 行 + LLM 失败/垃圾输出静默不抛")
    return 0


def main() -> int:
    test_flag_off_and_too_quiet()
    test_extract_writes_low_weight_facts()
    test_empty_and_llm_failure_silent()
    print("\n=== 38项#26 平淡时刻入账：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
