"""把回复时实际使用的状态、行为帧和记忆整理成可持久化解释快照。"""
from __future__ import annotations

from typing import Iterable

from .behavior import BehaviorFrame
from .state import AgentState


def _clean(text: object, limit: int = 180) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def build_reply_explanation(
    state: AgentState,
    frame: BehaviorFrame,
    *,
    memory_rows: Iterable[tuple[str, object]] = (),
    search_used: bool = False,
    media: str = "none",
) -> dict:
    """构造稳定、有限长的 UI 数据；不暴露 system prompt 或模型思考链。"""
    behavior = []
    for label, value in (
        ("情绪与精力", frame.mood_line),
        ("关系分寸", frame.stage_line),
        ("主动性", frame.initiative),
        ("情绪余温", frame.reaction_line),
        ("休息状态", frame.rest_line),
        ("关系修复", frame.tension_line),
        ("长期态度", frame.archive_line),
        ("事件记忆", frame.event_line),
    ):
        cleaned = _clean(value, 260)
        if cleaned:
            behavior.append({"label": label, "text": cleaned})

    memories = []
    seen: set[str] = set()
    for kind, value in memory_rows:
        cleaned = _clean(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        memories.append({"kind": _clean(kind, 20), "text": cleaned})
        if len(memories) >= 4:
            break

    return {
        "version": 1,
        "state": {
            "affection": int(state.affection),
            "stage": state.stage,
            "mood": int(state.emotion),
            "mood_label": state.emotion_name,
            "energy": int(state.energy),
            "resting": bool(state.resting),
            "rest_until": state.rest_until,
            "tension": int(state.tension),
        },
        "behavior": behavior,
        "memories": memories,
        "tools": {"search": bool(search_used), "media": media},
    }
