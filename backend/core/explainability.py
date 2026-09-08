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
    contexts: Iterable[dict] = (),
    fact_ids: Iterable[int] = (),
    user_id: str = "",
) -> dict:
    """构造稳定、有限长的 UI 数据；不暴露 system prompt 或模型思考链。

    fact_ids：本轮实际注入并允许展示的事实 id；G01 生命周期露出（F07）
    只对这些条目附非敏感元数据（保留说明/她被确认过的标记），不另外
    召回事实，也不展示评分权重等算法内部值。
    """
    behavior = []
    for label, value in (
        ("情绪与精力", frame.mood_line),
        ("关系分寸", frame.stage_line),
        ("语言质地", getattr(frame, "texture_line", "")),
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

    # F07 记忆生命周期露出：仅对已引用的合法事实附非敏感生命周期元数据。
    lifecycle_map: dict[int, dict] = {}
    ids = [int(i) for i in fact_ids if int(i) > 0]
    if ids and user_id:
        try:
            from .memory_salience import lifecycle_for_facts

            lifecycle_map = lifecycle_for_facts(user_id, ids)
        except Exception:
            lifecycle_map = {}  # 露出失败不影响解释快照本身

    memories = []
    seen: set[str] = set()
    for row in memory_rows:
        kind, value = row[0], row[1]
        row_fact_id = row[2] if len(row) > 2 else None
        cleaned = _clean(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        entry: dict = {"kind": _clean(kind, 20), "text": cleaned}
        meta = lifecycle_map.get(int(row_fact_id)) if row_fact_id else None
        if meta:
            entry["lifecycle"] = meta
        memories.append(entry)
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
        # P1-02 语境来源：只记录条目 id/命名空间/命中规则，不显示注入原文
        "contexts": [
            {
                "id": _clean(c.get("id", ""), 60),
                "namespace": _clean(c.get("namespace", ""), 40),
                "reasons": [_clean(r, 24) for r in c.get("reasons", ())][:3],
            }
            for c in list(contexts)[:4]
        ],
        "tools": {"search": bool(search_used), "media": media},
    }
