# -*- coding: utf-8 -*-
"""休息时的有界沉默与连续消息唤醒。"""
from __future__ import annotations

from datetime import datetime, timedelta

from .userdb import db, kv_get, kv_set

_WAKE_KEY = "sleep:wake_until"
_WAKE_AFTER_MESSAGES = 3
_MESSAGE_WINDOW = timedelta(minutes=10)
_AWAKE_WINDOW = timedelta(minutes=30)


def _instant(now: datetime | None) -> datetime:
    value = now or datetime.now().astimezone()
    return value if value.tzinfo is not None else value.astimezone()


def _resting_now(user_id: str, now: datetime) -> bool:
    """读取统一 Presence 的基础状态，但忽略已经建立的唤醒覆盖。"""
    from .presence import current_presence

    return current_presence(user_id, now=now, include_wake=False) == "rest"


def is_awake(user_id: str, *, now: datetime | None = None) -> bool:
    raw = kv_get(user_id, _WAKE_KEY)
    if not raw:
        return False
    try:
        until = datetime.fromisoformat(raw)
        if until.tzinfo is None:
            until = until.astimezone()
    except (TypeError, ValueError):
        return False
    return _instant(now).timestamp() < until.timestamp()


def _recent_unanswered_messages(user_id: str, now: datetime) -> int:
    """统计十分钟内、上一条助手回复之后的连续用户消息。"""
    count = 0
    cutoff = now.timestamp() - _MESSAGE_WINDOW.total_seconds()
    for row in reversed(db.recent_messages_with_ids(user_id, _WAKE_AFTER_MESSAGES - 1)):
        if row["role"] != "user":
            break
        try:
            ts = datetime.fromisoformat(str(row["ts"])).timestamp()
        except (TypeError, ValueError):
            break
        if ts < cutoff:
            break
        count += 1
    return count


def before_user_message(
    user_id: str,
    *,
    now: datetime | None = None,
    persist: bool = True,
) -> str:
    """返回 active/silent/woke；当前消息尚未写入 messages 表。

    TZTUZHAN_NO_SLEEP_GATE=1（测试豁免）：凌晨跑全量时她的睡眠时段会让
    所有「断言 mock 回复非空」的套件整批假红（2026-09-22 凌晨实证）；
    conftest 为 pytest 进程默认置位并随 env 继承到套件子进程，生产不设
    即保持真实门控。
    """
    import os

    if os.getenv("TZTUZHAN_NO_SLEEP_GATE", "").strip().lower() in {"1", "true"}:
        return "active"
    instant = _instant(now)
    if not _resting_now(user_id, instant):
        return "active"
    if is_awake(user_id, now=instant):
        if persist:
            kv_set(user_id, _WAKE_KEY, (instant + _AWAKE_WINDOW).isoformat(timespec="seconds"))
        return "active"
    if _recent_unanswered_messages(user_id, instant) + 1 < _WAKE_AFTER_MESSAGES:
        return "silent"
    if persist:
        kv_set(user_id, _WAKE_KEY, (instant + _AWAKE_WINDOW).isoformat(timespec="seconds"))
    return "woke"


def wake_prompt() -> str:
    return (
        "你原本正在休息，被对方十分钟内连续发来的多条消息吵醒了。"
        "现在回应这些消息的整体意思，语气可以带一点刚醒的困倦，但不要责怪、惩罚或让对方内疚；"
        "若内容显得紧急，先直接处理紧急内容"
    )
