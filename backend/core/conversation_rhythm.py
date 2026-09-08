# -*- coding: utf-8 -*-
"""P2-05 会话节奏：A 有期限追发、B 晚安/行程收尾、C 称呼候选。

契约（docs/Zcode技术指导.md §6 P2-05 / §14.7）：
- A 追发：对话中出现「待补一句」的时刻（被 goodbye 打断的分享、说到一半的
  话题）产生追发实例：origin_turn_id、expires_at（2 小时）、每来源至多一次；
  用户开始新话题 / 手动停止 / 勿扰 / 来源删除 → 取消；复用既有主动队列，
  不用内存 sleep 挂不可恢复定时器；
- B 晚安：显式晚安先遵守用户结束意图（只回一句，不趁机追问）——由既有
  trim_farewell 保证；本模块只负责取消该会话未发追发（晚安不建立 24h
  惩罚锁，第二天问候走原条件）。行程「去忙」是角色表达，不锁输入框、
  不扣分（P1-04 presence 只读）；
- C 称呼：resolver 消费 P2-02 偏好（明确禁令硬约束、允许列表只作候选），
  阶段变化只提供候选不强行越级昵称；变化来源可查（candidates 返回来源）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from .userdb import db, kv_del, kv_get, kv_set

KV_FOLLOWUPS = "rhythm:followups"
# 工程默认（14.7）：追发 20 分钟后可用、2 小时过期、每来源至多一次
FOLLOWUP_DELAY_MIN = 20
FOLLOWUP_TTL_HOURS = 2
MAX_FOLLOWUPS = 3  # 用户同时挂起的追发上限

_GOODNIGHT_RE = re.compile(r"晚安|明天见|睡啦|先睡了|我睡了|我去睡了|睡了睡了|睡觉了|该睡了")
_STOP_RE = re.compile(r"(?:别|不用|不要)(?:再)?(?:追发|补充|接着说)|到此为止")
_INTERRUPT_RE = re.compile(r"先去忙|先忙了|有事先走|等会儿聊|晚点聊|回头再聊|稍后再聊")


def _now() -> datetime:
    return datetime.now()


def _load(user_id: str) -> list[dict]:
    raw = kv_get(user_id, KV_FOLLOWUPS)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _save(user_id: str, items: list[dict]) -> None:
    if items:
        kv_set(user_id, KV_FOLLOWUPS, json.dumps(items, ensure_ascii=False))
    else:
        kv_del(user_id, KV_FOLLOWUPS)


def _source_exists(user_id: str, origin_turn_id: int) -> bool:
    with db._lock:
        return db.conn.execute(
            "SELECT 1 FROM messages WHERE id=? AND user_id=?",
            (int(origin_turn_id), user_id),
        ).fetchone() is not None


def add_followup(user_id: str, *, origin_turn_id: int, hint: str,
                 earliest_at: datetime | None = None,
                 now: datetime | None = None) -> dict | None:
    """挂一条追发（幂等：同 origin_turn 的未过期实例存在即不重复）。返回实例或 None。"""
    now = now or _now()
    live = [i for i in _load(user_id)
            if not i.get("cancelled") and not _expired(i, now)]
    if any(i.get("origin_turn_id") == int(origin_turn_id) for i in live):
        return None  # 同源幂等：只挂一次
    items = [i for i in _load(user_id) if not _expired(i, now)]
    if len([i for i in items if not i.get("cancelled")]) >= MAX_FOLLOWUPS:
        return None
    ready = (earliest_at or now + timedelta(minutes=FOLLOWUP_DELAY_MIN)).isoformat(
        timespec="seconds")
    expires = (now + timedelta(hours=FOLLOWUP_TTL_HOURS)).isoformat(timespec="seconds")
    item = {
        "origin_turn_id": int(origin_turn_id),
        "hint": str(hint)[:120],
        "ready_at": ready,
        "expires_at": expires,
        "created_at": now.isoformat(timespec="seconds"),
        "cancelled": False,
    }
    items.append(item)
    _save(user_id, items)
    return item


def _expired(item: dict, now: datetime) -> bool:
    try:
        return now > datetime.fromisoformat(item["expires_at"])
    except (KeyError, ValueError, TypeError):
        return True


def ready_followups(user_id: str, *, now: datetime | None = None,
                    new_topic_text: str | None = None,
                    dnd: bool = False) -> list[dict]:
    """取当前可发的追发：过期清理、新话题/勿扰/手动取消剔除。

    新话题判定：调用方传入本轮用户消息，若与追发 hint 无关且消息较长
    （>12 字，视为展开了新话题），旧追发让位——不打断新对话。
    """
    now = now or _now()
    items = _load(user_id)
    kept: list[dict] = []
    ready: list[dict] = []
    for item in items:
        if item.get("cancelled") or _expired(item, now):
            continue
        if not _source_exists(user_id, int(item.get("origin_turn_id") or 0)):
            continue
        if dnd:
            continue  # 勿扰期间取消，恢复后也不突然补发旧话题
        if new_topic_text and len(new_topic_text) > 12:
            continue  # 用户已开新话题：未发的追发静默作废（保留记录但不发）
        try:
            is_ready = now >= datetime.fromisoformat(item["ready_at"])
        except (KeyError, ValueError, TypeError):
            continue
        (ready if is_ready else kept).append(item)
    _save(user_id, kept + ready)
    return ready


def cancel_followups(user_id: str, *, origin_turn_ids: list[int] | None = None,
                     now: datetime | None = None) -> int:
    """手动停止/晚安时取消未发追发（晚安只取消本会话追发，无惩罚）。

    now 可注入：过期判定必须与调用方同一时间基准，否则墙钟漂移会漏取消。
    """
    moment = now or _now()
    items = _load(user_id)
    n = 0
    for item in items:
        if item.get("cancelled") or _expired(item, moment):
            continue
        if origin_turn_ids is None or item.get("origin_turn_id") in origin_turn_ids:
            item["cancelled"] = True
            n += 1
    _save(user_id, items)
    return n


def mark_sent(user_id: str, origin_turn_id: int) -> None:
    """追发已表达：移除实例（每来源至多一次的「一次」在表达成功时计数）。"""
    items = [i for i in _load(user_id) if i.get("origin_turn_id") != int(origin_turn_id)]
    _save(user_id, items)


# ---- B 晚安 / 行程 ----

def on_goodnight(user_id: str) -> dict:
    """显式晚安：取消本会话未发追发；不建立任何跨天惩罚状态。"""
    n = cancel_followups(user_id)
    return {"cancelled_followups": n, "penalty": None}


def handle_user_turn(
    user_id: str,
    turn_id: int,
    text: str,
    *,
    now: datetime | None = None,
) -> dict:
    """处理用户侧节奏信号，并在明确中断时挂一条有来源的追发。"""
    text = (text or "").strip()
    if not text:
        return {"action": "none", "created": None, "cancelled": 0}
    if _GOODNIGHT_RE.search(text):
        result = on_goodnight(user_id)
        return {"action": "goodnight", "created": None,
                "cancelled": result["cancelled_followups"]}
    if _STOP_RE.search(text):
        return {"action": "stopped", "created": None,
                "cancelled": cancel_followups(user_id, now=now)}

    if _INTERRUPT_RE.search(text):
        with db._lock:
            row = db.conn.execute(
                "SELECT id, content FROM messages WHERE user_id=? AND role='assistant' "
                "AND id < ? ORDER BY id DESC LIMIT 1",
                (user_id, int(turn_id)),
            ).fetchone()
        if row is None or not str(row["content"] or "").strip():
            return {"action": "interrupted", "created": None, "cancelled": 0}
        item = add_followup(
            user_id,
            origin_turn_id=int(row["id"]),
            hint=str(row["content"]).strip(),
            now=now,
        )
        return {"action": "interrupted", "created": item, "cancelled": 0}

    before = len(_load(user_id))
    ready_followups(user_id, now=now, new_topic_text=text)
    after = len(_load(user_id))
    return {"action": "new_topic" if after < before else "none", "created": None,
            "cancelled": max(0, before - after)}


def presence_line(user_id: str) -> str:
    """行程收尾提示（P1-04 presence 只读）。

    她的「去忙」是角色表达：提示她此刻在忙什么，用户需要时基本可及；
    绝不锁输入框、绝不以睡觉为由冷处理（输出空串 = 无话可说也不装忙）。
    """
    try:
        from .schedule import current_presence

        presence = current_presence(user_id)
    except Exception:
        return ""
    if presence == "announced_offline":
        return "你此刻说了要去忙，简单交代一句就好；对方找你时正常回应，别装消失"
    return ""


# ---- C 称呼候选 ----

def address_candidates(user_id: str, *, stage: str) -> dict:
    """称呼来源可查：明确禁令（硬约束）+ 允许列表（候选）+ 阶段提示。

    阶段变化只提供候选、不强行越级昵称；返回来源便于前端展示/撤销。
    """
    from .user_preferences import list_preferences, migrate_legacy, resolve

    migrate_legacy(user_id)
    prefs = resolve(user_id)
    address = prefs.get("address", {})
    stage_hint = {
        "初识": "初识阶段还很生疏，用「你」最稳妥；对方主动给的叫法可以用",
        "熟悉": "熟悉阶段，对方确认过的称呼可以自然用了",
        "亲密": "亲密阶段可以偶尔冒出调侃叫法，但已确认的主叫法为主",
        "恋人": "恋人阶段亲昵叫法都自然，但仍以确认过的称呼为主（除非对方想换）",
    }.get(stage, "")
    source_rows = []
    for item in list_preferences(user_id):
        if item.get("category") != "address" or item.get("status") != "active":
            continue
        source_rows.append({
            "preference_id": int(item["id"]),
            "source_type": item.get("origin", "user_teaching"),
            "source_message_id": item.get("source_message_id"),
            "revocable": True,
        })
    return {
        "forbidden": sorted(address.get("forbidden", [])),
        "allowed_candidates": sorted(address.get("allowed", [])),
        "stage_hint": stage_hint,
        "sources": source_rows,
    }
