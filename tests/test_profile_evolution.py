# -*- coding: utf-8 -*-
"""岗位二回归：画像演变留痕（replace 归档不物理删、manual 豁免、fail-open、
profile_diff 轨迹）。LLM 全 mock（两次 chat：提炼 + 演变判定）。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztzhan_pevo_"))

UID = "u_pevo"
ROWS = [{"role": "user", "content": f"消息{i}", "id": i} for i in range(1, 10)]


def _run(coro):
    return asyncio.run(coro)


def test_replace_main_path() -> int:
    from backend.core import profile as pf
    from backend.core.userdb import db

    db.ensure_user(UID)
    old_id = db.add_profile(UID, "habits", "每天早上喝一杯拿铁咖啡", "llm")
    assert old_id is not None
    extract_json = '{"habits": ["最近把咖啡戒了改喝绿茶"], "likes": ["绿茶"]}'
    verdict_json = '{"replaces": [{"old_id": %d, "candidate_idx": 0, "reason": "对话明说戒了"}]}' % old_id
    with patch("backend.core.llm.chat",
               new=AsyncMock(side_effect=[extract_json, verdict_json])):
        ok = _run(pf.extract_profile(UID, rows=ROWS, done=99))
    assert ok, "提炼应成功"
    active = {r["content"] for r in db.get_profile(UID)}
    full = db.get_profile(UID, include_archived=True)
    by_id = {r["id"]: r for r in full}
    assert by_id[old_id]["status"] == "archived", "旧条目应归档不物理删"
    assert "每天早上喝一杯拿铁咖啡" not in active, "归档条目不得进默认读取"
    assert any("绿茶" in c for c in active), "新条目应 active"
    log = db.profile_diff(UID)
    assert len(log) == 1 and log[0]["op"] == "replace", log
    assert log[0]["old_id"] == old_id and "咖啡" in log[0]["old_content"]
    print("[OK] replace 主路径：旧归档新顶替+演变日志+默认读取只回 active+profile_diff 可查")
    return 0


def test_no_evolution_and_failopen() -> int:
    from backend.core import profile as pf
    from backend.core.userdb import db

    UID2 = UID + "_noevo"
    db.ensure_user(UID2)
    db.add_profile(UID2, "likes", "喜欢下雨天", "llm")
    extract_json = '{"likes": ["也喜欢打雪仗"]}'  # 互补新增（bigram 重叠 <50%），非演变
    with patch("backend.core.llm.chat",
               new=AsyncMock(side_effect=[extract_json, '{"replaces": []}'])):
        ok = _run(pf.extract_profile(UID2, rows=ROWS, done=99))
    assert ok and not db.profile_diff(UID2), "无演变：不写日志"
    assert len(db.get_profile(UID2)) == 2, "互补候选按普通新增"

    UID3 = UID + "_fo"
    db.ensure_user(UID3)
    with patch("backend.core.llm.chat",
               new=AsyncMock(side_effect=['{"likes":["喜欢猫"]}', RuntimeError("boom")])):
        ok = _run(pf.extract_profile(UID3, rows=ROWS, done=99))
    assert ok, "判定失败 fail-open：提炼仍成功"
    assert any("猫" in r["content"] for r in db.get_profile(UID3)), "候选按普通新增"
    assert not db.profile_diff(UID3)
    print("[OK] 无演变零日志 + 判定失败 fail-open=旧行为")
    return 0


def test_manual_immunity() -> int:
    """manual（用户手写）条目绝不替代：即使判定幻觉指向它也执行不了。"""
    from backend.core import profile as pf
    from backend.core.userdb import db

    UID4 = UID + "_manual"
    db.ensure_user(UID4)
    manual_id = db.add_profile(UID4, "habits", "手动写的习惯条目", "manual")
    assert manual_id is not None
    # 判定返回指向 manual 条目的 replace（幻觉/绕过）——manual 不在判定集，必被拒
    verdict_json = '{"replaces": [{"old_id": %d, "candidate_idx": 0, "reason": "幻觉"}]}' % manual_id
    with patch("backend.core.llm.chat",
               new=AsyncMock(side_effect=['{"habits": ["新习惯条目"]}', verdict_json])):
        ok = _run(pf.extract_profile(UID4, rows=ROWS, done=99))
    assert ok
    row = next(r for r in db.get_profile(UID4, include_archived=True) if r["id"] == manual_id)
    assert row["status"] == "active", "manual 条目必须原样保留"
    assert not db.profile_diff(UID4), "manual 替代不得产生演变日志"
    print("[OK] manual 豁免：手写条目不被 LLM 判定替代（判定集过滤+校验双层防线）")
    return 0


def main() -> int:
    test_replace_main_path()
    test_no_evolution_and_failopen()
    test_manual_immunity()
    print("\n=== 岗位二·画像演变留痕：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
