# -*- coding: utf-8 -*-
"""D11 离线补算：应用关着的这段时间，她的日子照「过」。

契约（docs/D9-D12-DESIGN-2026-09-21.md §4，2026-09-21 拍板：逐条回放可跳过）：

- **plan 全确定性**：把离线窗口按采样点重放真实行程引擎（schedule.block_at），
  得到「她那段时间在做什么」的候选事件，零 LLM、可复现；
- **trim 限额裁剪**：超出上限（默认 8 条）降级为一条「还有 N 件日常小事」摘要行；
- **generate ≤1 次 LLM**：只为整段回放写一句自然的开场（素材=裁剪后事件 +
  D9 局势档案），失败回落确定性开场；D10 hard/extreme 档零 LLM；
- 状态用 kv（offline:pending / offline:last_recap，runtime）：同一离线窗口
  幂等（pending 未确认前不重新生成）；确认/跳过后归档到 last_recap；
- 不写 character_life_events（回放即档，不往真实生活事件表里塞派生行）；
  世界状态的延续由 D9 局势档案承担——这是对设计文档的一处收敛，提交已注明；
- 前端逐条回放、可跳过：GET 取待回放内容，POST /ack 确认 delivered/skip。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from .config import config
from .log import logger

_SAMPLE_HOURS_LONG_GAP = (21,)            # 离线 >48h：每天只取晚间一个采样点
_SAMPLE_HOURS_SHORT_GAP = (10, 15, 21)    # 较短离线：一天三个采样点
_LONG_GAP = timedelta(hours=48)

_PENDING_KEY = "offline:pending"
_LAST_RECAP_KEY = "offline:last_recap"

_DETERMINISTIC_OPENING = "（这段时间我照常过自己的日子。挑几件说给你听——）"


def _last_activity(user_id: str) -> datetime:
    """离线窗口的起点：最近一条消息 / 上次回放 / 上次批处理，取最新。"""
    from .userdb import db, kv_get

    moments = [datetime.now().astimezone() - timedelta(days=365)]
    try:
        with db._lock:
            row = db.conn.execute(
                "SELECT MAX(ts) FROM messages WHERE user_id = ?", (user_id,)
            ).fetchone()
        if row and row[0]:
            moments.append(datetime.fromisoformat(str(row[0])))
    except Exception:
        pass
    for key in (_LAST_RECAP_KEY,):
        raw = kv_get(user_id, key)
        if raw:
            try:
                data = json.loads(raw)
                moments.append(datetime.fromisoformat(str(data.get("to") or "")))
            except Exception:
                pass
    return max(moments)


def plan(user_id: str, from_dt: datetime, to_dt: datetime) -> list[dict]:
    """确定性重放离线窗口的行程（真实周模板引擎，零 LLM）。

    采样规则：窗口 >48h 每天一个晚间采样点；否则每天三个。睡眠时段跳过；
    连续落在同一行程块的采样点只保留第一个（不逐小时罗列同一件事）。
    """
    from .schedule import current_activity

    def _aware(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.astimezone()

    from_dt, to_dt = _aware(from_dt), _aware(to_dt)
    long_gap = (to_dt - from_dt) > _LONG_GAP
    hours = _SAMPLE_HOURS_LONG_GAP if long_gap else _SAMPLE_HOURS_SHORT_GAP
    events: list[dict] = []
    last_block = ""
    day = from_dt.date()
    while day <= to_dt.date():
        for hour in hours:
            moment = datetime(day.year, day.month, day.day, hour).astimezone()
            if moment < from_dt or moment > to_dt:
                continue
            act = current_activity(user_id, now=moment)
            if act.get("activity") == "sleeping":
                continue
            block_id = str(act.get("block_id") or act.get("activity") or "")
            if block_id and block_id == last_block:
                continue
            last_block = block_id
            label = str(act.get("activity_label") or act.get("activity") or "过日子")
            location = str(act.get("location_label") or "").strip()
            events.append({
                "at": moment.isoformat(timespec="minutes"),
                "text": f"{label}" + (f"（{location}）" if location else ""),
            })
            if len(events) >= 24:  # plan 上限保护；真正裁剪在 trim
                return events
        day += timedelta(days=1)
    return events


def trim(events: list[dict], *, cap: int | None = None) -> list[dict]:
    """护栏限额裁剪：超出上限降级为一条摘要行（ZZZChats §7 可搬点）。"""
    limit = cap if cap is not None else int(config.offline_recap_event_cap)
    if len(events) <= limit:
        return list(events)
    rest = len(events) - limit
    return list(events[:limit]) + [{
        "at": events[-1]["at"],
        "summary": True,
        "text": f"（那段时间还有 {rest} 件日常小事，就不一一说了）",
    }]


async def generate(user_id: str, from_dt: datetime, to_dt: datetime) -> dict:
    """生成一段离线补算（plan + trim + ≤1 次 LLM 开场）。"""
    events = trim(plan(user_id, from_dt, to_dt))
    opening = _DETERMINISTIC_OPENING
    llm_used = False
    if events:
        try:
            from .cost_guard import check as _cost_ok

            if _cost_ok("offline"):
                opening = await _opening_line(user_id, events, from_dt, to_dt)
                llm_used = True
        except Exception as exc:
            logger.warning("[离线补算] {} 开场生成失败，用确定性开场: {}",
                           user_id, type(exc).__name__)
    return {
        "from": from_dt.isoformat(timespec="minutes"),
        "to": to_dt.isoformat(timespec="minutes"),
        "opening": opening,
        "events": events,
        "llm_used": llm_used,
    }


async def _opening_line(
    user_id: str, events: list[dict], from_dt: datetime, to_dt: datetime
) -> str:
    """LLM 开场一句：素材=确定性事件 + 局势档案快照，禁止编造新事实。"""
    from .llm import chat

    situation_text = ""
    try:
        from .situation import situation_context

        situation_text = situation_context(user_id)
    except Exception:
        pass
    material = "\n".join(f"- [{e['at'][-14:-9]}] {e['text']}" for e in events[:8])
    prompt = (
        "你一个人过了几天，现在对方回来了。下面是你这段时间真实做过的事"
        "（确定性记录，不是创作素材）和你们的世界快照。"
        "请用你的口吻说一句自然的开场（1-2 句），带出「这段时间照常过日子」，"
        "可以提一两件具体的事，不编造记录之外的事，不解释系统，不提数字预算。"
        "\n\n[你这段时间做过的事]\n" + material
        + (f"\n\n[世界快照]\n{situation_text}" if situation_text else "")
    )
    resp = await chat(
        [{"role": "user", "content": prompt}],
        task="batch_other",
        temperature=0.6,
        max_tokens=200,
    )
    text = (resp or "").strip()
    return text or _DETERMINISTIC_OPENING


# ---------------------------------------------------------------------------
# 幂等状态与确认（kv runtime）
# ---------------------------------------------------------------------------

async def maybe_generate(user_id: str) -> dict | None:
    """有足够长的离线窗口才生成；同一 pending 幂等返回。"""
    try:
        from .features import flag

        if not flag("offline_recap_enabled"):
            return None
    except Exception:
        return None
    from .userdb import kv_get, kv_set

    pending_raw = kv_get(user_id, _PENDING_KEY)
    if pending_raw:
        try:
            pending = json.loads(pending_raw)
            if pending.get("state") == "pending":
                return pending
        except Exception:
            pass  # 坏数据走重新生成
    now = datetime.now().astimezone()
    last = _last_activity(user_id)
    min_gap = timedelta(hours=float(config.offline_recap_min_gap_hours))
    if now - last < min_gap:
        return None
    max_days = timedelta(days=int(config.offline_recap_max_days))
    from_dt = max(last, now - max_days)
    recap = await generate(user_id, from_dt, now)
    recap["state"] = "pending"
    kv_set(user_id, _PENDING_KEY, json.dumps(recap, ensure_ascii=False))
    return recap


def pending_view(user_id: str) -> dict:
    """GET 视图：pending 原样；无 pending 返回 {ok, pending: False}。"""
    from .userdb import kv_get

    raw = kv_get(user_id, _PENDING_KEY)
    if raw:
        try:
            data = json.loads(raw)
            if data.get("state") == "pending":
                return {"ok": True, "pending": True, **data}
        except Exception:
            pass
    return {"ok": True, "pending": False}


def ack(user_id: str, action: str) -> dict:
    """回放结束（delivered）或用户跳过（skip）。幂等：无 pending 也返回 ok。"""
    from .userdb import kv_get, kv_set

    raw = kv_get(user_id, _PENDING_KEY)
    if not raw:
        return {"ok": True, "pending": False}
    try:
        data = json.loads(raw)
    except Exception:
        data = {}
    state = "delivered" if action == "delivered" else "skipped"
    data["state"] = state
    data["acked_at"] = datetime.now().isoformat(timespec="seconds")
    kv_set(user_id, _LAST_RECAP_KEY, json.dumps(data, ensure_ascii=False))
    kv_set(user_id, _PENDING_KEY, "")
    logger.info("[离线补算] {} 回放{}", user_id, state)
    return {"ok": True, "pending": False, "state": state}


async def startup_recap() -> None:
    """app 启动时为当前人格后台预生成（解锁后的第一次就绪）。"""
    try:
        from .persona_profiles import active_user_id

        await maybe_generate(active_user_id())
    except Exception:
        logger.exception("[离线补算] 启动预生成失败（不影响启动）")


__all__ = [
    "ack",
    "generate",
    "maybe_generate",
    "pending_view",
    "plan",
    "startup_recap",
    "trim",
]
