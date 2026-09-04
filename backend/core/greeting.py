# -*- coding: utf-8 -*-
"""久别主动问候：用户隔一阵回来，菟菚先开口。

- greeting_for(user_id, session_id, force=False) → str | None
  检查距上次访问间隔，超阈值（默认 8 小时）生成问候语，
  写入会话历史，更新 last_seen。
- 隔时短（< 阈值）仅更新 last_seen，不生成问候。
"""
from __future__ import annotations

import datetime
import random
import threading
import time

from .config import config
from .llm import chat
from .log import logger
from .persona import build_system_prompt
from .userdb import db, kv_del, kv_get, kv_set

_GREET_KEY = "web_last_seen"  # kv 键名
_GREET_HOURS = 8  # 兼容/文档默认值；运行时使用 config.proactive_greeting_idle_hours
_GREET_PENDING_KEY = "web_greet_pending"  # 问候生成中占位（并发去重）
# 并发防护：读-判-写需要原子，避免两个并发请求都生成问候
_greet_lock = threading.Lock()

# 兜底问候池：LLM 失败时轮换抽取，避免总说同一句导致重复率高。
# 语气对齐菟菚人设（干脆、带点腹黑毒舌），且刻意不重复。
_FALLBACK_GREETINGS = [
    "回来了？我还以为你迷路了呢。",
    "哟，舍得上线了？",
    "我还当你把我忘了呢。",
    "可算来了，等得我都要睡着了。",
    "刚才还在想你是不是掉网里了。",
    "回来得正好，我正闲得发慌。",
    "嗯？今天怎么想起找我了。",
    "我还猜你今天会不会来。",
]

# 每个 user 上一次抽到的兜底索引，避免连续两次抽到同一句
_last_fallback_idx: dict[str, int] = {}


def _fallback_greeting(user_id: str) -> str:
    """从兜底池轮换抽一句，且尽量不与上一次相同。"""
    pool = _FALLBACK_GREETINGS
    prev = _last_fallback_idx.get(user_id)
    idx = random.randrange(len(pool))
    if prev is not None and len(pool) > 1:
        while idx == prev:
            idx = random.randrange(len(pool))
    _last_fallback_idx[user_id] = idx
    return pool[idx]


def _last_seen_ts(user_id: str) -> float | None:
    val = kv_get(user_id, _GREET_KEY)
    if val:
        try:
            return float(val)
        except ValueError:
            return None
    return None


def _set_last_seen(user_id: str, ts: float | None = None) -> None:
    kv_set(user_id, _GREET_KEY, str(ts or time.time()))


async def _greeting_text(user_id: str, *, gap_hours: float | None = None) -> str:
    """用 LLM 生成一句菟菚风格的问候。"""
    from . import affection

    user = db.get_user(user_id)
    if user is None:
        user = db.ensure_user(user_id)
    address = user["nickname_pref"] or ""
    affection_val = user["affection"] or 0
    lover_confirm = bool(user["lover_confirm"])
    sys_prompt = build_system_prompt(
        stage=affection.stage_of(affection_val),
        address=address or "",
        lover_confirm=lover_confirm,
        first_chat=False,
        affection=affection_val,
        user_id=user_id,
    )
    # 获取当前时间信息
    now = datetime.datetime.now()
    h = now.hour
    period = (
        "凌晨" if h < 5
        else "早上" if h < 9
        else "上午" if h < 12
        else "中午" if h < 14
        else "下午" if h < 18
        else "傍晚" if h < 20
        else "晚上"
    )
    time_desc = f"{now.month}月{now.day}日 {period}"
    narrative_hint = ""
    if gap_hours is not None and gap_hours >= config.proactive_greeting_idle_hours:
        try:
            from .offline_narrative import collect_offline_context

            narrative = collect_offline_context(user_id, gap_hours, now=now)
            # 梦境只在亲密/恋人采用；关系未到时降为研究碎片，避免借梦越级。
            if narrative.mode == "dream" and affection.stage_of(affection_val) in {"初识", "熟悉"}:
                narrative = type(narrative)(
                    mode="research",
                    gap_hours=narrative.gap_hours,
                    recent_lines=narrative.recent_lines,
                    triple_lines=narrative.triple_lines,
                )
            narrative_hint = "\n\n" + narrative.prompt_hint(affection.stage_of(affection_val))
        except Exception as e:
            logger.warning(f"[问候] 离线叙事素材整理失败: {e}")
    messages = [
        {"role": "system", "content": sys_prompt},
        {
            "role": "user",
            "content": (
                f"现在是{time_desc}，你刚忙完一阵、正打算歇口气，这时对方上线来找你了。"
                "你们隔了一阵没聊，主动打个招呼吧。自然一点，就像朋友隔阵再见那样。"
                "一句话就够，别太长，别解释，别加括号动作。"
                f"{'如果记得对方的名字（' + address + '）就用上。' if address else ''}"
                f"{narrative_hint}"
            ),
        },
    ]
    try:
        text = await chat(messages, max_tokens=100, temperature=0.85)
        return text.strip()[:200]
    except Exception as e:
        logger.warning(f"[问候] LLM 生成失败: {e}")
        return _fallback_greeting(user_id)  # 兜底：轮换池随机抽取


async def greeting_for(
    user_id: str,
    session_id: str,
    *,
    force: bool = False,
) -> str | None:
    """如果用户久别归来，生成问候语。返回问候文本或 None。
    force=True 时跳过时间检查，总是生成（用于首次启动）。
    """
    from .reset import reset_epoch, reset_in_progress
    if reset_in_progress():
        return None
    epoch = reset_epoch()
    # 页面挂载后问候与用户首条消息会并发：记录生成前的会话长度，LLM 返回后
    # 再校验。期间只要有人开始聊天，就丢弃这句过时问候，避免插到正常回复后面。
    from ..session.store import message_count

    baseline_message_count = await message_count(session_id)
    now = time.time()
    gap_hours: float | None = None
    claim_token: str | None = None
    with _greet_lock:
        last_ts = _last_seen_ts(user_id)
        if not force and last_ts is not None:
            gap = now - last_ts
            gap_hours = max(0.0, gap / 3600)
            if gap < config.proactive_greeting_idle_hours * 3600:
                _set_last_seen(user_id, now)
                return None  # 间隔短，不问候
        # 先占位标记「问候已生成中」：即使 _greeting_text 在锁外 await，
        # 并发请求也会因已占位而跳过，避免重复问候。
        if kv_get(user_id, _GREET_PENDING_KEY):
            return None
        kv_set(user_id, _GREET_PENDING_KEY, str(now))
        _set_last_seen(user_id, now)
        from .proactive_policy import try_claim_active

        claim_token = try_claim_active(user_id, "greeting", now=now)
        if not claim_token:
            kv_del(user_id, _GREET_PENDING_KEY)
            return None

    try:
        # 生成问候并持久化到会话
        text = await _greeting_text(user_id, gap_hours=gap_hours)
    except Exception as e:
        logger.warning(f"[问候] 生成异常: {e}")
        from .proactive_policy import finish_active_claim

        finish_active_claim(user_id, claim_token, success=False, source="greeting")
        return None
    finally:
        # 无论成功失败都释放占位，避免一次失败后永久卡住问候
        kv_del(user_id, _GREET_PENDING_KEY)
    if not text:
        from .proactive_policy import finish_active_claim

        finish_active_claim(user_id, claim_token, success=False, source="greeting")
        return None
    if await message_count(session_id) != baseline_message_count:
        from .proactive_policy import finish_active_claim

        finish_active_claim(user_id, claim_token, success=False, source="greeting")
        logger.info("[问候] 生成期间会话已活跃，丢弃过时问候")
        return None
    from .reset import ResetSuperseded, epoch_is_current, user_write_guard
    if not epoch_is_current(epoch):
        from .proactive_policy import finish_active_claim

        finish_active_claim(user_id, claim_token, success=False, source="greeting")
        return None

    # 持久化到会话存储
    try:
        from ..session.store import append_messages

        async with user_write_guard(epoch):
            await append_messages(
                session_id,
                [{"role": "bot", "content": text, "ts": now}],
            )
    except ResetSuperseded:
        from .proactive_policy import finish_active_claim

        finish_active_claim(user_id, claim_token, success=False, source="greeting")
        return None
    except Exception as e:
        logger.warning(f"[问候] 持久化失败: {e}")

    from .proactive_policy import finish_active_claim

    finish_active_claim(user_id, claim_token, success=True, source="greeting")
    logger.info(f"[问候] 隔 {config.proactive_greeting_idle_hours}h+ 生成问候: {text[:40]}...")
    return text
