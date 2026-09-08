# -*- coding: utf-8 -*-
"""§17.1 注意力漂移：每用户 ≤5 主题的有界衰减状态。

契约（docs/Zcode技术指导.md §17.1 + 总纲批次 12）：

- 只保存 topic_id / weight / last_seen_turn / source_version，**不复制正文**；
- 每轮：当前主题 +0.35，其余 ×0.7；weight < 0.1 删除；
- 显式转题立即置顶（weight 直接抬到 max(current)+0.35 并排最前）；
- 活动/心事可提供低权重候选（observe_topic，初值 0.1 以下），不能劫持
  当前注意力（不覆盖已有 >0.1 的主题）；
- 临时轮为进程内内存态（内存字典，不落库）；正常会话也只存主题 id 与
  来源引用（kv，登记 kv_registry），持久量极小；
- 来源删除（source_version 变化或 drop_topic）时对应主题移除。
"""
from __future__ import annotations

from .log import logger

MAX_TOPICS = 5
BUMP = 0.35
DECAY = 0.7
FLOOR = 0.1
# kv 键（kv_registry 已登记 attention:topics）
_KV_KEY = "attention:topics"


def _load(user_id: str) -> dict[str, dict]:
    from .userdb import kv_get

    raw = kv_get(user_id, _KV_KEY)
    if not raw:
        return {}
    import json

    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(topic): {
            "topic_id": str(topic),
            "weight": float(item.get("weight", 0.0)),
            "last_seen_turn": int(item.get("last_seen_turn", 0)),
            "source_version": str(item.get("source_version", "")),
        }
        for topic, item in data.items()
        if isinstance(item, dict)
    }


def _save(user_id: str, topics: dict[str, dict]) -> None:
    from .userdb import kv_set
    import json

    kv_set(user_id, _KV_KEY, json.dumps(topics, ensure_ascii=False))


def _prune(topics: dict[str, dict], *, keep_below_floor: bool = False) -> dict[str, dict]:
    """超窗删除：<FLOOR 的主题默认剔除；keep_below_floor 时保留最新的一条
    低权重候选（活动/心事的候选登记，等待用户是否提起，而非直接死亡）。"""
    if keep_below_floor:
        alive = dict(topics)
        low = [t for t, s in alive.items() if s["weight"] < FLOOR]
        # 只保留最近登记的一条候选（多余的 <FLOOR 仍然剔除）
        for t in low[:-1]:
            alive.pop(t, None)
    else:
        alive = {t: s for t, s in topics.items() if s["weight"] >= FLOOR}
    if len(alive) > MAX_TOPICS:
        ranked = sorted(alive.items(), key=lambda kv: (-kv[1]["weight"], kv[0]))
        alive = dict(ranked[:MAX_TOPICS])
    return alive


def advance_turn(user_id: str, current_topic: str, *, turn_id: int,
                 ephemeral: bool = False) -> dict[str, dict]:
    """一轮结束：当前主题 +BUMP，其余 ×DECAY，<FLOOR 删除；返回当前状态。"""
    topics = {} if ephemeral else _load(user_id)
    for topic, state in topics.items():
        if topic == current_topic:
            state["weight"] = min(1.0, state["weight"] + BUMP)
            state["last_seen_turn"] = int(turn_id)
        else:
            state["weight"] *= DECAY
    if current_topic not in topics:
        topics[current_topic] = {
            "topic_id": str(current_topic), "weight": BUMP,
            "last_seen_turn": int(turn_id), "source_version": "",
        }
    else:
        topics[current_topic]["weight"] = min(1.0, topics[current_topic]["weight"] + BUMP)
        topics[current_topic]["last_seen_turn"] = int(turn_id)
    topics = _prune(topics)
    if not ephemeral:
        _save(user_id, topics)
    return topics


def switch_topic(user_id: str, new_topic: str, *, turn_id: int,
                 source_version: str = "", ephemeral: bool = False) -> dict[str, dict]:
    """显式转题：新主题立即置顶（+BUMP 叠加在当前最高权重之上）。"""
    topics = {} if ephemeral else _load(user_id)
    top = max((s["weight"] for s in topics.values()), default=0.0)
    for topic, state in topics.items():
        if topic != new_topic:
            state["weight"] *= DECAY
    topics[new_topic] = {
        "topic_id": str(new_topic),
        "weight": min(1.0, top + BUMP),
        "last_seen_turn": int(turn_id),
        "source_version": source_version,
    }
    topics = _prune(topics)
    if not ephemeral:
        _save(user_id, topics)
    return topics


def observe_topic(user_id: str, topic_id: str, source_version: str = "",
                  *, ephemeral: bool = False) -> dict[str, dict]:
    """活动/心事提供的低权重候选：只登记未见过的话题（0.05，<FLOOR 但保留
    一条最新候选），不能劫持已在注意力内的主题。"""
    topics = {} if ephemeral else _load(user_id)
    if topic_id in topics:
        return topics  # 已在注意力内：不修改（候选不能劫持）
    topics[topic_id] = {
        "topic_id": str(topic_id), "weight": 0.05,
        "last_seen_turn": 0, "source_version": str(source_version),
    }
    topics = _prune(topics, keep_below_floor=True)
    if not ephemeral:
        _save(user_id, topics)
    return topics


def drop_topic(user_id: str, topic_id: str, *, ephemeral: bool = False) -> dict[str, dict]:
    """来源删除：对应主题移除（来源版本变化由调用方比对后调用）。"""
    topics = {} if ephemeral else _load(user_id)
    topics.pop(str(topic_id), None)
    if not ephemeral:
        _save(user_id, topics)
    return topics


def snapshot(user_id: str) -> list[dict]:
    """只读视图（给解释层：主题 id + 权重档，不涉正文）。"""
    topics = _load(user_id)
    return sorted(
        (
            {"topic_id": t, "weight": round(s["weight"], 3),
             "last_seen_turn": s["last_seen_turn"]}
            for t, s in topics.items()
        ),
        key=lambda item: -item["weight"],
    )
