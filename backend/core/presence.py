"""L08 unified, explainable visual presence derived from existing state owners."""
from __future__ import annotations

from datetime import datetime

from . import schedule
from .affection import bond_level_dimensions, dimensions_of, dimensions_stage
from .focus import focus_in_progress
from .persona_profiles import active_id, active_profile
from .state import load_state


def _energy_band(value: int) -> str:
    if value < 35:
        return "low"
    if value < 70:
        return "medium"
    return "high"


def _presence(user_id: str, activity: dict, state, *, now: datetime) -> str:
    if focus_in_progress(user_id):
        return "focus"
    if state.resting or activity.get("activity") == "sleeping":
        return "rest"
    try:
        from .life_templates import latest_outing

        today = now.astimezone().date().isoformat()
        if any(item.get("date") == today for item in latest_outing(user_id, limit=1)):
            return "announced_offline"
    except Exception:
        pass
    if activity.get("location_id") == "P-00":
        return "mobile"
    return schedule.current_presence(user_id)


def visual_state(user_id: str, *, now: datetime | None = None,
                 persona_id: str | None = None, profile: dict | None = None) -> dict:
    """Build the UI state without creating events or exposing prompt material."""
    instant = now or datetime.now().astimezone()
    activity = schedule.current_activity(user_id, now=instant)
    state = load_state(user_id)
    current_profile = profile or active_profile()
    current_persona = persona_id or str(current_profile.get("id") or active_id())
    presence = _presence(user_id, activity, state, now=instant)
    trust, intimacy = dimensions_of(user_id)
    stage = dimensions_stage(trust, intimacy)
    bond_info = bond_level_dimensions(trust, intimacy)
    bond = bond_info[0] if bond_info else ""
    motion_enabled = current_profile.get("motion_enabled", True) is not False
    return {
        "persona_id": current_persona,
        "revision": int(instant.timestamp() * 1_000_000),
        "mood_label": state.emotion_name,
        "bond_label": bond or stage,
        "energy_band": _energy_band(int(state.energy)),
        "activity_kind": str(activity.get("activity") or "unknown"),
        "presence": presence if presence in {"home", "mobile", "announced_offline", "rest", "focus"} else "home",
        "quiet": presence == "focus" or not motion_enabled,
        "reduced_motion": not motion_enabled,
        "source_time": instant.isoformat(timespec="seconds"),
    }
