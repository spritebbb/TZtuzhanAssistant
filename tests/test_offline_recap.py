# -*- coding: utf-8 -*-
"""D11 离线补算回归（docs/D9-D12-DESIGN-2026-09-21.md §4，逐条回放可跳过）。

覆盖六件事：
1. plan 确定性重放：采样点带离线时间戳、睡眠跳过、同块去重、>48h 降为每日一采样；
2. trim 限额裁剪：超上限降级为一条摘要行（含剩余计数）；
3. generate：D10 ok 档 ≤1 次 LLM 只写开场（素材含确定性事件）；hard 档零 LLM；
   LLM 失败回落确定性开场不抛；
4. maybe_generate：间隙不足不生成；同一 pending 幂等；ack(delivered/skip) 归档；
   flag 关闭返回 None；
5. API：GET 懒生成返回 pending；POST /ack 确认；flag 关 403；
6. kv 登记。
"""
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
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_offline_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import features, offline_recap  # noqa: E402
from backend.core.kv_registry import match_spec  # noqa: E402
from backend.core.userdb import db, kv_get, kv_set  # noqa: E402

UID = "offline-recap-user"

_FROM = datetime(2026, 9, 19, 9, 0)   # → _TO 共 25h（<48h 短窗：一天三采样）
_TO = datetime(2026, 9, 20, 10, 0)


def _fake_schedule_activity(user_id: str, now: datetime | None = None):
    """确定性假行程：白天研究、晚上小屋，10-21 点之外视为睡觉。"""
    hour = (now or datetime.now()).hour
    if hour < 8 or hour >= 23:
        return {"block_id": "", "activity": "sleeping", "activity_label": "睡觉",
                "location_id": "P-02", "location_label": "小屋"}
    if hour >= 19:
        return {"block_id": "eve-home", "activity": "rest", "activity_label": "在小屋里窝着",
                "location_id": "P-02", "location_label": "小屋"}
    return {"block_id": "day-research", "activity": "research", "activity_label": "做研究",
            "location_id": "P-01", "location_label": "研究所"}


def test_plan_deterministic_replay() -> None:
    db.ensure_user(UID)
    with patch("backend.core.schedule.current_activity", _fake_schedule_activity):
        events = offline_recap.plan(UID, _FROM, _TO)
    assert events, "短窗应产出事件"
    for ev in events:
        assert ev["at"].startswith("2026-09-"), f"事件应带离线时间戳: {ev['at']}"
        assert ev["text"]
    texts = [e["text"] for e in events]
    assert any("研究" in t for t in texts) and any("小屋" in t for t in texts), texts
    # 同一块去重：25h 窗口 10/15/21×2 采样 → 研究+小屋+研究 ≤ 3 条
    assert len(events) <= 3, f"同块去重后不应逐小时罗列: {len(events)}"
    # >48h 窗口：每天只取 21:00 一个采样点（此时段在假行程里是晚间块）
    with patch("backend.core.schedule.current_activity", _fake_schedule_activity):
        long_events = offline_recap.plan(
            UID, datetime(2026, 9, 1, 9, 0), datetime(2026, 9, 16, 10, 0),
        )
    assert all(e["at"][11:13] == "21" for e in long_events), \
        f">48h 窗口应每日仅晚间采样: {[e['at'] for e in long_events][:4]}"
    print(f"[OK] plan 确定性重放：{len(events)} 事件、时间戳/去重/长窗降采样全对")


def test_trim_degrades_to_summary() -> None:
    events = [{"at": f"2026-09-18T{h:02d}:00", "text": f"事{h}"} for h in range(20)]
    trimmed = offline_recap.trim(events, cap=8)
    assert len(trimmed) == 9, "8 条 + 1 条摘要"
    assert trimmed[-1].get("summary") is True and "12 件" in trimmed[-1]["text"]
    assert offline_recap.trim(events[:3], cap=8) == events[:3], "不超限原样返回"
    print("[OK] trim：超限降级为一条含计数的摘要行")


async def _fake_chat_ok(messages, **kwargs):
    return "我这边几天没见，倒也没闲着——给你讲讲。"


def test_generate_opening_tiers() -> None:
    db.ensure_user(UID)
    from backend.core import cost_guard

    with patch("backend.core.schedule.current_activity", _fake_schedule_activity), \
            patch("backend.core.llm.chat", side_effect=_fake_chat_ok) as mc, \
            patch.object(cost_guard, "check", return_value=True):
        recap = asyncio.run(offline_recap.generate(UID, _FROM, _TO))
    assert recap["llm_used"] is True and recap["opening"] == "我这边几天没见，倒也没闲着——给你讲讲。"
    assert recap["events"], "事件应为确定性重放结果"
    assert mc.await_count == 1, "开场只允许一次 LLM"

    # hard 档零 LLM
    async def no_llm(*a, **k):
        raise AssertionError("hard 档不得调 LLM")

    with patch("backend.core.schedule.current_activity", _fake_schedule_activity), \
            patch.object(cost_guard, "check", return_value=False), \
            patch("backend.core.llm.chat", new=no_llm):
        recap = asyncio.run(offline_recap.generate(UID, _FROM, _TO))
    assert recap["llm_used"] is False
    assert recap["opening"], "hard 档用确定性开场"

    # LLM 失败回落
    async def boom(*a, **k):
        raise RuntimeError("provider down")

    with patch("backend.core.schedule.current_activity", _fake_schedule_activity), \
            patch.object(cost_guard, "check", return_value=True), \
            patch("backend.core.llm.chat", new=boom):
        recap = asyncio.run(offline_recap.generate(UID, _FROM, _TO))
    assert recap["opening"] == offline_recap._DETERMINISTIC_OPENING
    print("[OK] generate：ok 档一次 LLM 开场、hard 零 LLM、失败回落确定性开场")


def test_maybe_generate_idempotent_and_ack() -> None:
    db.ensure_user(UID)
    kv_set(UID, "offline:pending", "")
    # 间隙不足 → 不生成
    with patch.object(offline_recap, "_last_activity",
                      return_value=datetime.now().astimezone() - timedelta(hours=1)):
        assert asyncio.run(offline_recap.maybe_generate(UID)) is None
    # 足够间隙 → 生成并 pending
    with patch.object(offline_recap, "_last_activity",
                      return_value=datetime.now().astimezone() - timedelta(hours=30)), \
            patch("backend.core.schedule.current_activity", _fake_schedule_activity), \
            patch("backend.core.llm.chat", new=_fake_chat_ok):
        first = asyncio.run(offline_recap.maybe_generate(UID))
    assert first and first["state"] == "pending" and first["events"]
    # 同一 pending 幂等（不重新生成，连 LLM 都不再调）
    with patch("backend.core.llm.chat", side_effect=AssertionError("pending 期不得再生成")):
        again = asyncio.run(offline_recap.maybe_generate(UID))
    assert again == first
    # 确认 delivered → 归档，pending 清空
    result = offline_recap.ack(UID, "delivered")
    assert result["state"] == "delivered" and offline_recap.pending_view(UID)["pending"] is False
    assert kv_get(UID, "offline:last_recap"), "应归档到 last_recap"
    # 幂等：再 ack 无 pending 也 ok
    assert offline_recap.ack(UID, "skip")["ok"] is True
    # flag 关闭 → None
    features.set_flag("offline_recap_enabled", False)
    try:
        assert asyncio.run(offline_recap.maybe_generate(UID)) is None
    finally:
        features.set_flag("offline_recap_enabled", True)
    print("[OK] maybe_generate：间隙门控、pending 幂等、ack 归档、flag 关闭")


def test_http_endpoints() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api import offline_recap as api_mod

    db.ensure_user(UID)
    kv_set(UID, "offline:pending", "")
    app = FastAPI()
    app.include_router(api_mod.router)
    client = TestClient(app)

    # GET：懒生成（足间隙）→ pending
    with patch("backend.core.persona_profiles.active_user_id", return_value=UID), \
            patch.object(offline_recap, "_last_activity",
                         return_value=datetime.now().astimezone() - timedelta(hours=20)), \
            patch("backend.core.schedule.current_activity", _fake_schedule_activity), \
            patch("backend.core.llm.chat", side_effect=_fake_chat_ok):
        resp = client.get("/api/offline-recap")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True and data["pending"] is True and data["events"]

    # ack delivered；再 GET 无 pending
    with patch("backend.core.persona_profiles.active_user_id", return_value=UID):
        assert client.post("/api/offline-recap/ack", json={"action": "delivered"}).json()["state"] == "delivered"
        assert client.get("/api/offline-recap").json()["pending"] is False
        assert client.post("/api/offline-recap/ack", json={"action": "bad"}).status_code == 422

    # flag 关 → 403
    features.set_flag("offline_recap_enabled", False)
    try:
        assert client.get("/api/offline-recap").status_code == 403
    finally:
        features.set_flag("offline_recap_enabled", True)
    print("[OK] API：GET 懒生成/POST ack/非法 action 422/flag 关 403")


def test_kv_registered() -> None:
    for key in ("offline:pending", "offline:last_recap"):
        spec = match_spec(key)
        assert spec is not None and spec.module == "offline_recap", f"kv 未登记: {key}"
    print("[OK] kv 登记：offline:pending 与 offline:last_recap")


def main() -> None:
    db.conn.execute("SELECT 1")
    test_plan_deterministic_replay()
    test_trim_degrades_to_summary()
    test_generate_opening_tiers()
    test_maybe_generate_idempotent_and_ack()
    test_http_endpoints()
    test_kv_registered()
    print("\n=== D11 离线补算：6 组全部通过 ===")


if __name__ == "__main__":
    main()
