# -*- coding: utf-8 -*-
"""体验收口：/api/presence 状态行（她此刻在做什么 + 最近生活事件）。

覆盖：
- 睡眠时段 → activity="sleeping"、地点小屋；
- 周内时段 → 活动与场所中文标签正确映射；
- latest_life_event 只返回 daily_life 虚构事件（新→旧），不混入真实关系事件。

运行：python -m tests.test_presence_api
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tz_presence_test_"))

from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.meta import router
from backend.core import schedule
from backend.core.userdb import db

app = FastAPI()
app.include_router(router)


def _seed_life_event(user_id: str, date: str, description: str) -> None:
    schedule._record_event(
        user_id, "evening_work", date, "daily_life",
        {"date": date, "description": description, "location_id": "P-01",
         "material_id": "mat-test"},
        f"{date}T16:00:00+00:00", f"{date}T16:00:00+00:00",
    )


async def test_presence_labels() -> None:
    uid = "presence-user"
    db.ensure_user(uid)
    with TestClient(app) as client:
        # 周中 15:00 → afternoon_stay / 研究所
        weekday_15 = datetime(2026, 9, 9, 15, 0).astimezone()  # 2026-09-09 是周三
        act = schedule.current_activity(uid, now=weekday_15)
        assert act["activity"] == "afternoon_stay", str(act)
        assert act["activity_label"] == "待在研究所"
        assert act["location_label"] == "研究所"

        # 睡眠时段（周三 04:00）→ sleeping / 小屋
        sleep_4 = datetime(2026, 9, 9, 4, 0).astimezone()
        act2 = schedule.current_activity(uid, now=sleep_4)
        assert act2["activity"] == "sleeping", str(act2)
        assert act2["activity_label"] == "睡觉"
        assert act2["location_label"] == "小屋"

        # API 端点闭环：默认 active user（无行程数据也不 500）
        r = client.get("/api/presence")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ok"] is True
        assert "activity_label" in d["activity"]
        assert isinstance(d["recent_events"], list)
    print("[OK] 活动标签映射 + 睡眠时段 + 端点闭环")


async def test_latest_life_events_order_and_kind() -> None:
    uid = "presence-events"
    db.ensure_user(uid)
    _seed_life_event(uid, "2026-09-06", "把今天观察到的人类现象记进本子")
    _seed_life_event(uid, "2026-09-07", "图书室整理到半夜")
    # 真实关系事件（kind != daily_life）不得出现在她的生活流里
    schedule._record_event(
        uid, "evening_work", "2026-09-08", "real_marker",
        {"note": "real"}, "2026-09-08T00:00:00+00:00", "2026-09-08T00:00:00+00:00",
    )
    events = schedule.latest_life_event(uid)
    assert len(events) == 2, f"只应有 2 条 daily_life: {events}"
    assert events[0]["date"] == "2026-09-07", "新的在前"
    assert events[0]["description"] == "图书室整理到半夜"
    print("[OK] 生活事件按时间倒序且只含虚构 daily_life")


async def main() -> None:
    await test_presence_labels()
    await test_latest_life_events_order_and_kind()
    print("\n全部通过 ✓")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
