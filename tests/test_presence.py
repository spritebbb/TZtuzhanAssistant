"""L08 unified VisualState derives from existing state, schedule and focus owners."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tz_l08_"))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.meta import router
from backend.core import presence
from backend.core.userdb import db


def main() -> None:
    uid = "presence-unified"
    db.ensure_user(uid)
    now = datetime(2026, 9, 9, 15, 0).astimezone()
    profile = {"id": "p-one", "motion_enabled": True}
    with patch.object(presence, "focus_in_progress", return_value=False), \
         patch.object(presence, "active_profile", return_value=profile):
        view = presence.visual_state(uid, now=now, persona_id="p-one", profile=profile)
    assert set(view) == {"persona_id", "revision", "mood_label", "bond_label", "energy_band",
                         "activity_kind", "presence", "quiet", "reduced_motion", "source_time"}
    assert view["persona_id"] == "p-one" and view["activity_kind"] == "afternoon_stay"
    assert view["presence"] == "home" and not view["quiet"] and not view["reduced_motion"]
    assert isinstance(view["revision"], int) and "prompt" not in str(view).lower()

    with patch.object(presence, "focus_in_progress", return_value=True):
        focused = presence.visual_state(uid, now=now, persona_id="p-one", profile=profile)
    assert focused["presence"] == "focus" and focused["quiet"]

    reduced = presence.visual_state(uid, now=now, persona_id="p-one",
                                    profile={"id": "p-one", "motion_enabled": False})
    assert reduced["quiet"] and reduced["reduced_motion"]
    sleeping = presence.visual_state(uid, now=datetime(2026, 9, 9, 4, 0).astimezone(),
                                     persona_id="p-one", profile=profile)
    assert sleeping["presence"] == "rest" and sleeping["activity_kind"] == "sleeping"

    app = FastAPI()
    app.include_router(router)
    with patch("backend.api.meta.active_user_id", return_value=uid), TestClient(app) as client:
        p = client.get("/api/presence")
        m = client.get("/api/meta")
    assert p.status_code == 200 and p.json()["visual_state"]["persona_id"]
    assert m.status_code == 200 and m.json()["visual_state"]["source_time"]
    print("[OK] L08 unified presence, quiet mode, reduced motion and compatible endpoints")


if __name__ == "__main__":
    main()
