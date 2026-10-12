# -*- coding: utf-8 -*-
"""批次 13：主动阈值、内容池和正典映射的回归测试。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_batch13_"))

from backend.core.config import Config
from backend.core import greeting_material, initiative, schedule
from backend.core.userdb import db
from backend.api.config_api import router as config_router


def test_approved_default_thresholds() -> None:
    names = (
        "PROACTIVE_NEW_USER_DAYS",
        "PROACTIVE_NEW_USER_IDLE_HOURS",
    )
    old_values = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ.pop(name, None)
        cfg = Config()
    finally:
        for name, value in old_values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    assert cfg.proactive_new_user_days == 3
    assert cfg.proactive_new_user_idle_hours == 2


def test_new_user_idle_threshold_expires_after_three_days() -> None:
    uid = "batch13-new-user"
    db.ensure_user(uid)
    now = datetime.now()
    with db._lock:
        db.conn.execute("DELETE FROM messages WHERE user_id = ?", (uid,))
        db.conn.execute(
            "INSERT INTO messages (user_id, role, content, ts) VALUES (?, 'user', 'hello', ?)",
            (uid, (now - timedelta(days=2)).isoformat(timespec="seconds")),
        )
        db.conn.commit()
    with patch.object(initiative.config, "proactive_idle_hours", 6), \
         patch.object(initiative.config, "proactive_new_user_days", 3), \
         patch.object(initiative.config, "proactive_new_user_idle_hours", 2):
        assert initiative._idle_hours_for_user(uid, now_ts=now.timestamp()) == 2
        assert initiative._idle_hours_for_user(
            uid, now_ts=(now + timedelta(days=2)).timestamp()
        ) == 6
    assert initiative._MIN_STAGE == "熟悉"


def test_content_expansion_contracts() -> None:
    variants = greeting_material.load_variants()
    by_category: dict[str, list[str]] = {}
    for variant in variants:
        by_category.setdefault(variant.category, []).append(variant.id)
    assert set(by_category) == set(greeting_material._CATEGORIES)
    assert all(len(ids) >= 4 for ids in by_category.values())
    assert {"A4", "B4", "C4", "D4"}.issubset({v.id for v in variants})


def test_canon_material_bucket_mapping_is_complete_and_valid() -> None:
    mapping = schedule.load_canon_material_buckets()
    assert set(mapping) == {
        "P-00", "P-01", "P-02", "O-01", "O-02", "O-03",
        "O-04", "O-05", "O-06", "C-01", "C-02",
    }
    valid_activities = {block.activity for block in schedule.weekly_template()}
    assert all(set(buckets) <= valid_activities for buckets in mapping.values())
    assert set(item["activity"] for item in schedule.MATERIALS) <= {
        bucket for buckets in mapping.values() for bucket in buckets
    }
    assert schedule.canon_material_buckets("C-01") == ("afternoon_stay", "late_night")


def test_thresholds_are_visible_and_validated_in_settings_api() -> None:
    app = FastAPI()
    app.include_router(config_router)
    with TestClient(app) as client:
        response = client.get("/api/config")
        assert response.status_code == 200
        visible = response.json()["config"]
        assert visible["proactive_new_user_days"] >= 1
        assert visible["proactive_new_user_idle_hours"] >= 1
        invalid = client.post(
            "/api/config", json={"proactive_new_user_days": "101"}
        )
        assert invalid.status_code == 400
        assert "PROACTIVE_NEW_USER_DAYS" in invalid.json()["error"]


def main() -> int:
    test_approved_default_thresholds()
    test_new_user_idle_threshold_expires_after_three_days()
    test_content_expansion_contracts()
    test_canon_material_bucket_mapping_is_complete_and_valid()
    test_thresholds_are_visible_and_validated_in_settings_api()
    print("[OK] 批次 13：阈值、内容池、升档台词与正典映射全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
