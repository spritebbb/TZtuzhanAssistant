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


def _aware(dt: datetime) -> datetime:
    """统一为 aware 本地时区（P1-4：messages.ts 是 naive 本地时间，混比必 TypeError）。"""
    return dt if dt.tzinfo else dt.astimezone()


def _last_activity(user_id: str) -> datetime:
    """离线窗口的起点：最近一条消息 / 上次回放 / 上次批处理，取最新。"""
    from .userdb import db, kv_get

    # 全部归一为 aware 再比较：messages.ts 以 naive 本地时间落库（userdb），
    # last_recap 的 to 是 aware isoformat——此前 naive/aware 混进同一个 max()
    # 只要用户有任何消息就抛 TypeError，离线补算整体失效。
    moments = [_aware(datetime.now()) - timedelta(days=365)]
    try:
        with db._lock:
            row = db.conn.execute(
                "SELECT MAX(ts) FROM messages WHERE user_id = ?", (user_id,)
            ).fetchone()
        if row and row[0]:
            moments.append(_aware(datetime.fromisoformat(str(row[0]))))
    except Exception:
        pass
    for key in (_LAST_RECAP_KEY,):
        raw = kv_get(user_id, key)
        if raw:
            try:
                data = json.loads(raw)
                moments.append(_aware(datetime.fromisoformat(str(data.get("to") or ""))))
            except Exception:
                pass
    return max(moments)


def plan(user_id: str, from_dt: datetime, to_dt: datetime) -> list[dict]:
    """确定性重放离线窗口的行程（真实周模板引擎，零 LLM）。

    采样规则：窗口 >48h 每天一个晚间采样点；否则每天三个。睡眠时段跳过；
    连续落在同一行程块的采样点只保留第一个（不逐小时罗列同一件事）。
    """
    from .schedule import current_activity

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
    """生成一段离线补算（plan + trim + ≤1 次 LLM 讲述）。"""
    events = trim(plan(user_id, from_dt, to_dt))
    opening = _DETERMINISTIC_OPENING
    narrated: list[str] | None = None
    llm_used = False
    if events:
        try:
            from .cost_guard import check as _cost_ok

            if _cost_ok("offline"):
                opening, narrated = await _narrate(user_id, events, from_dt, to_dt)
                llm_used = True
        except Exception as exc:
            logger.warning("[离线补算] {} 讲述生成失败，用确定性开场: {}",
                           user_id, type(exc).__name__)
    # 逐条覆盖为讲述版：只覆盖非 summary 行（summary 是确定性摘要，不该被
    # 改写），narrated 与 narratable 按序对位，空段保持机械行（fail-soft）
    narrated_iter = iter(narrated or [])
    final_events: list[dict] = []
    for e in events:
        text = e["text"]
        if narrated and not e.get("summary"):
            nxt = next(narrated_iter, "")
            if nxt:
                text = nxt
        final_events.append({**e, "text": text})
    return {
        "from": from_dt.isoformat(timespec="minutes"),
        "to": to_dt.isoformat(timespec="minutes"),
        "opening": opening,
        "events": final_events,
        "llm_used": llm_used,
    }


async def _narrate(
    user_id: str, events: list[dict], from_dt: datetime, to_dt: datetime
) -> tuple[str, list[str] | None]:
    """LLM 讲述（一次调用）：久别重逢的开场 + 逐条事件的她视角改写。

    事件骨架（时间/活动/地点）是确定性记录，不是创作素材——事实层只能
    复述，感受层（心情/氛围/小情绪）是她的人格自由。响应应为 JSON：
    {"opening": str, "events": [str, ...]}；解析失败或纯文本响应时
    opening 取整段文本、events 返回 None（调用方保持机械行，fail-soft）。
    """
    from .llm import chat

    situation_text = ""
    try:
        from .situation import situation_context

        situation_text = situation_context(user_id)
    except Exception:
        pass
    try:
        from .persona_profiles import persona_name_for_user_id

        persona_name = persona_name_for_user_id(user_id)
    except Exception:
        persona_name = "菟菚"
    days = max(1, round((to_dt - from_dt).total_seconds() / 86400))
    # P3-36：at 是 aware isoformat（如 2026-09-25T21:00+08:00），时间在
    # 定长第 11-16 位；旧的 [-14:-9] 负索引会切出 "25T21" 这种错值。
    material = "\n".join(
        f"- [{e['at'][5:16]}] {e['text']}" for e in events if not e.get("summary"))
    prompt = (
        f"你是{persona_name}。对方离开了大约 {days} 天，这期间你一个人照常过日子。"
        "现在对方回来了，你要跟他讲讲这段时间的事。\n\n"
        "[你这段时间的确定性日程记录（时间 + 活动 + 地点）]\n" + material
        + (f"\n\n[你们的世界快照]\n{situation_text}" if situation_text else "")
        + "\n\n请输出严格的 JSON 对象（不要 markdown 代码块、不要任何多余文字）：\n"
        '{"opening": "2-4 句开场白", "events": ["改写后的事件1", "改写后的事件2", ...]}\n'
        "要求：\n"
        "1. opening：你的口吻，久别重逢的自然开场，可以带点想念或小情绪，"
        "顺势引出「这段时间照常过日子」，2-4 句；\n"
        "2. events：把每条日程记录逐条改写成你视角的一句话讲述（每条 15-45 字），"
        "像跟对方唠家常；保留时间、活动、地点的事实骨架；\n"
        "3. 可以加入做这件事时的心情、氛围、小感受，但**不得发明记录之外的新事件、"
        "新人物、新物品**，不要提任何系统和数字预算；\n"
        "4. events 数组长度必须与日程记录条数一致，顺序一一对应。"
    )
    resp = await chat(
        [{"role": "user", "content": prompt}],
        task="batch_other",
        temperature=0.6,
        max_tokens=900,
        thinking=False,  # 结构化输出：思考段会吃光 max_tokens 致正文空
    )
    text = (resp or "").strip()
    if not text:
        return _DETERMINISTIC_OPENING, None
    import json as _json

    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = _json.loads(text[start:end + 1])
        except ValueError:
            data = None
        if isinstance(data, dict):
            opening = str(data.get("opening") or "").strip()
            evs = data.get("events")
            # 不滤空串（滤了会顶位错位）：空段由调用方回落机械行
            narrated = [str(x).strip() for x in evs] \
                if isinstance(evs, list) else None
            # 长度必须与非 summary 事件条数一致：错位会把时间线讲串，整批弃用
            n_expected = sum(1 for e in events if not e.get("summary"))
            if narrated and len(narrated) != n_expected:
                narrated = None
            return (opening or _DETERMINISTIC_OPENING), narrated
    # 纯文本/无法解析：整段当开场，事件保持机械行
    return text or _DETERMINISTIC_OPENING, None


# ---------------------------------------------------------------------------
# 幂等状态与确认（kv runtime）
# ---------------------------------------------------------------------------

async def maybe_generate(user_id: str) -> dict | None:
    """有足够长的离线窗口才生成；同一 pending 幂等返回。

    离线补算回放的是菟菚的正典生活引擎（schedule 行程/素材），只对默认
    人格成立；其他人格没有各自的行程正典，直接跳过，不替她们编菟菚的一天。
    """
    try:
        from .features import flag

        if not flag("offline_recap_enabled"):
            return None
    except Exception:
        return None
    from .persona_profiles import DEFAULT_PERSONA_ID, profile_id_from_user_id

    if profile_id_from_user_id(user_id) != DEFAULT_PERSONA_ID:
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
