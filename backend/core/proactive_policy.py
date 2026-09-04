"""问候与主动引擎共享的频次、并发和失败冷却闸门。"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import date


_lock = threading.Lock()
_CLAIM_STALE_SEC = 300


def _done_key(day: str) -> str:
    return f"proactive:done:{day}"


def _claim_key(day: str) -> str:
    return f"proactive:claim:{day}"


def _failure_key(day: str) -> str:
    return f"proactive:failure:{day}"


def _kv_get(user_id: str, key: str) -> str | None:
    from .userdb import kv_get

    return kv_get(user_id, key)


def _kv_set(user_id: str, key: str, value: str) -> None:
    from .userdb import kv_set

    kv_set(user_id, key, value)


def _kv_del(user_id: str, key: str) -> bool:
    from .userdb import kv_del

    return kv_del(user_id, key)


def _daily_max() -> int:
    from .config import config

    return max(1, int(config.proactive_daily_max))


def _failure_cooldown_sec() -> int:
    from .config import config

    return max(30, int(config.proactive_failure_cooldown_sec))


def active_count_today(user_id: str, *, day: str | None = None) -> int:
    day = day or date.today().isoformat()
    raw = _kv_get(user_id, _done_key(day))
    try:
        count = max(0, int(raw or 0))
    except (TypeError, ValueError):
        count = 0
    # 兼容旧版 initiative:{day}:{user_id}=1，升级当天不重复主动。
    if _kv_get(user_id, f"initiative:{day}:{user_id}") is not None:
        count = max(1, count)
    return count


def active_done_today(user_id: str, *, day: str | None = None) -> bool:
    return active_count_today(user_id, day=day) >= _daily_max()


def mark_active_done(user_id: str, source: str, *, day: str | None = None) -> None:
    """登记一次已实际发生的主动行为。source 仅用于诊断，不影响额度。"""
    day = day or date.today().isoformat()
    with _lock:
        count = active_count_today(user_id, day=day) + 1
        _kv_set(user_id, _done_key(day), str(count))
        # 保留旧键供旧进程/旧测试读取；新逻辑以 proactive:done 为准。
        _kv_set(user_id, f"initiative:{day}:{user_id}", source or "1")


def try_claim_active(
    user_id: str,
    source: str,
    *,
    day: str | None = None,
    now: float | None = None,
) -> str | None:
    """尝试原子占位；返回 token 表示可生成，None 表示已达额度/生成中/失败冷却。"""
    day = day or date.today().isoformat()
    now = time.time() if now is None else now
    with _lock:
        if active_done_today(user_id, day=day):
            return None
        failure_raw = _kv_get(user_id, _failure_key(day))
        try:
            failure_ts = float(failure_raw or 0)
        except (TypeError, ValueError):
            failure_ts = 0
        if failure_ts and now - failure_ts < _failure_cooldown_sec():
            return None

        claim_raw = _kv_get(user_id, _claim_key(day))
        if claim_raw:
            try:
                claim = json.loads(claim_raw)
                claim_ts = float(claim.get("ts", 0))
            except (TypeError, ValueError, json.JSONDecodeError, AttributeError):
                claim_ts = now
            if now - claim_ts < _CLAIM_STALE_SEC:
                return None

        token = uuid.uuid4().hex
        _kv_set(
            user_id,
            _claim_key(day),
            json.dumps({"token": token, "source": source, "ts": now}, ensure_ascii=False),
        )
        return token


def finish_active_claim(
    user_id: str,
    token: str,
    *,
    success: bool,
    source: str,
    day: str | None = None,
    now: float | None = None,
) -> bool:
    """完成占位。只有 token 匹配者能释放；失败会留下短期冷却标记。"""
    day = day or date.today().isoformat()
    now = time.time() if now is None else now
    with _lock:
        raw = _kv_get(user_id, _claim_key(day))
        try:
            claim = json.loads(raw or "{}")
        except (TypeError, json.JSONDecodeError):
            claim = {}
        if claim.get("token") != token:
            return False
        _kv_del(user_id, _claim_key(day))
        if success:
            count = active_count_today(user_id, day=day) + 1
            _kv_set(user_id, _done_key(day), str(count))
            _kv_set(user_id, f"initiative:{day}:{user_id}", source or "1")
            _kv_del(user_id, _failure_key(day))
        else:
            _kv_set(user_id, _failure_key(day), str(now))
        return True

