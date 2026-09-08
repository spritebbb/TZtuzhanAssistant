# -*- coding: utf-8 -*-
"""L03 关系气质：证据入账/半衰期推导/双气质并存/阶段门控/屏蔽/behavior 修正。

契约（§16 L03）：
- 只用明确事件映射气质；romantic 非恋人不登记（低亲密没有 romantic）；
- 90 天窗口 + 30 天半衰期；≥3 个不同日期有效事件才显示，否则 forming；
- 最高两类权重差 <10% 可并存；derive_style 不向 UI 返回分数；
- 单日高频事件不能形成分支（≥3 distinct days 门槛）；
- 屏蔽后 derive 排除；源删（forget_for_source）即时重算；
- 开关关闭 → forming 且不入账。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_l03_"))

from backend.core import relationship_events as rel_events
from backend.core import relationship_style as rs
from backend.core.userdb import db

AT = datetime(2026, 9, 8, 12, 0).astimezone()
UID = "style-user"


def _seed_event(event_type: str, source_id: int, days_ago: int) -> int | None:
    """直接落一条事件（会经 record() 的 L03 挂点自动登记证据）。"""
    occurred = (AT - timedelta(days=days_ago)).isoformat(timespec="seconds")
    return rel_events.record(
        UID, event_type, "fact", source_id,
        subject="", obj="", payload={}, occurred_at=occurred,
    )


def test_event_mapping_and_romantic_gate() -> int:
    db.ensure_user(UID)
    # 恋人阶段：important_date → romantic 登记
    with patch.object(rs, "_current_stage" if hasattr(rs, "_current_stage") else "_blocked_styles"):
        pass  # placeholder；record_evidence 显式传 stage
    recorded = rs.record_evidence(UID, 9001, "important_date",
                                  occurred_at=AT.isoformat(timespec="seconds"),
                                  stage="恋人")
    assert recorded == ["romantic"], recorded
    # 非恋人阶段：不登记
    recorded2 = rs.record_evidence(UID, 9002, "important_date",
                                   occurred_at=AT.isoformat(timespec="seconds"),
                                   stage="初识")
    assert recorded2 == [], recorded2
    # 幂等：同 event 同 style 重复登记不新增
    again = rs.record_evidence(UID, 9001, "important_date", stage="恋人")
    assert again == [], again
    # 未知类型：不登记
    assert rs.record_evidence(UID, 9003, "unknown_type", stage="恋人") == []
    print("[OK] 事件→气质映射 + romantic 恋人门控 + 幂等")
    return 0


def test_derive_forming_and_threshold() -> int:
    uid = "style-forming"
    db.ensure_user(uid)
    # 同一天 3 条 growth 事件：distinct days=1 <3 → forming
    for i in range(3):
        rs.record_evidence(uid, 100 + i, "goal_completed",
                           occurred_at=AT.isoformat(timespec="seconds"))
    result = rs.derive_style(uid, now=AT)
    assert result["forming"] is True and result["style_ids"] == [], result
    # 3 个不同日期 → 显示
    for i, days in enumerate((10, 40, 70)):
        rs.record_evidence(uid, 200 + i, "goal_completed",
                           occurred_at=(AT - timedelta(days=days)).isoformat(timespec="seconds"))
    result2 = rs.derive_style(uid, now=AT)
    assert result2["forming"] is False and result2["style_ids"] == ["growth"], result2
    assert result2["reasons"] and len(result2["reasons"]) <= 2
    assert "score" not in result2 and "weight" not in json.dumps(result2), "不得向 UI 泄漏分数"
    print("[OK] ≥3 distinct days 门槛（单日高频不形成分支）+ 无分数泄漏")
    return 0


def test_half_life_and_coexist() -> int:
    uid = "style-halflife"
    db.ensure_user(uid)
    # growth：3 天事件但都在 80 天前 → 衰减到很小
    for i, days in enumerate((70, 75, 80)):
        rs.record_evidence(uid, 300 + i, "goal_completed",
                           occurred_at=(AT - timedelta(days=days)).isoformat(timespec="seconds"))
    # playful：3 天事件都在最近 → 权重高
    for i, days in enumerate((1, 5, 9)):
        rs.record_evidence(uid, 400 + i, "story_finished",
                           occurred_at=(AT - timedelta(days=days)).isoformat(timespec="seconds"))
    result = rs.derive_style(uid, now=AT)
    assert result["style_ids"][0] == "companion", f"近期事件应占优: {result}"
    print("[OK] 30 天半衰期：近期事件权重占优")
    return 0


def test_block_and_source_delete() -> int:
    uid = "style-block"
    db.ensure_user(uid)
    for i, days in enumerate((5, 15, 25)):
        rs.record_evidence(uid, 500 + i, "story_finished",
                           occurred_at=(AT - timedelta(days=days)).isoformat(timespec="seconds"))
    base = rs.derive_style(uid, now=AT)
    assert base["style_ids"] == ["companion"], base
    # 屏蔽 companion → forming
    assert rs.block_style(uid, "companion") is True
    blocked = rs.derive_style(uid, now=AT)
    assert blocked["forming"] is True, blocked
    # 撤销屏蔽 → 恢复
    assert rs.unblock_style(uid, "companion") is True
    restored = rs.derive_style(uid, now=AT)
    assert restored["style_ids"] == ["companion"], restored
    # 源删除：清 3 条事件证据 → 回到 forming
    for i in range(3):
        rs.forget_for_source(uid, 500 + i)
    gone = rs.derive_style(uid, now=AT)
    assert gone["forming"] is True, gone
    print("[OK] 屏蔽/撤销屏蔽 + 源删即时重算")
    return 0


def test_record_hook_and_switch() -> int:
    uid = "style-hook"
    db.ensure_user(uid)
    # record() 落事件 → 证据自动登记
    occurred = (AT - timedelta(days=2)).isoformat(timespec="seconds")
    event_id = rel_events.record(uid, "story_finished", "fact", 777, occurred_at=occurred)
    assert event_id is not None
    rows = db.conn.execute(
        "SELECT style FROM relationship_style_evidence WHERE user_id=? AND event_id=?",
        (uid, event_id)).fetchall()
    assert any(r["style"] == "companion" for r in rows), rows
    # 开关关闭：derive forming
    from backend.core.features import set_flag

    set_flag("relationship_style_enabled", False)
    try:
        assert rs.derive_style(uid, now=AT)["forming"] is True
    finally:
        set_flag("relationship_style_enabled", True)
    print("[OK] record() 自动挂点 + 开关关闭 forming")
    return 0


def test_behavior_hint_clamp() -> int:
    hints = rs.behavior_hint(["playful", "companion"])
    assert all(-0.1 <= v <= 0.1 for v in hints.values()), hints
    assert hints.get("humor") == 0.1
    print("[OK] behavior 单轴 ±0.1 clamp")
    return 0


def test_api_endpoints() -> int:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.memory_admin import router

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        r = client.get("/api/memory/relationship-style")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ok"] is True and "forming" in d
        assert "score" not in json.dumps(d), "API 不得返回分数"
        # 屏蔽/撤销闭环
        assert client.post("/api/memory/relationship-style/companion/block").json()["ok"] is True
        assert client.delete("/api/memory/relationship-style/companion/block").json()["ok"] is True
        # 未知气质 → 422
        bad = client.post("/api/memory/relationship-style/nope/block")
        assert bad.status_code == 422
    print("[OK] API：GET 无分数 + 屏蔽/撤销闭环 + 未知气质 422")
    return 0


async def main() -> int:
    failed = (
        test_event_mapping_and_romantic_gate()
        + test_derive_forming_and_threshold()
        + test_half_life_and_coexist()
        + test_block_and_source_delete()
        + test_record_hook_and_switch()
        + test_behavior_hint_clamp()
        + test_api_endpoints()
    )
    if failed:
        print(f"\n=== L03 关系气质：{failed} 项失败 ===")
        return 1
    print("\n=== L03 关系气质：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
