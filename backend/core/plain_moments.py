# -*- coding: utf-8 -*-
"""38项#26 平淡时刻入账：daily 批次提取「平淡但有记头的当日事项」。

背景：facts 提炼（FACT_PROMPT）只收长期价值事实，情绪冲击事件另有专门
通道；中间那一大片「今天认真做过/深聊过的小事」谁都不记——三天后她连
「昨天陪他挑了两小时服务器」都想不起来，日常连续感断裂。

机制（用户拍板 2026-09-30：LLM 提取，默认开）：
- daily 批处理从当天对话提取 0~2 条平淡但有记头的当日事项；
- 低权写 facts（source_type=plain_moment，confidence=0.5，14 天保留期，
  到期由 decay_expired_facts 按 expires_at 通用清理自然淡忘）；
- **不进情绪档案**：不碰 state.py 的 apply_impulse / 情绪归档链——
  平淡就是平淡，不该影响情绪状态。

与 add_fact 的 50% 二元组重叠去重配合：每条措辞必须含具体话题词
（「聊了两小时XX游戏的服务器选择」），保证与其它事实可区分。
"""
from __future__ import annotations

import json
from datetime import date, datetime, time as dtime, timedelta

from .log import logger

_RETENTION_DAYS = 14
_MAX_PER_DAY = 2

_PLAIN_MOMENT_PROMPT = """你是日常观察员。从下面这天的对话记录里，提取 0~2 条「平淡但有记头的当日事项」——
当天确实花了可观时间做的事、聊得比较深入的话题、或两人一起完成的小事。

纪律（宁缺勿滥）：
- 不要情绪冲击事件（吵架、告白、坏消息等——那些另有专门通道记录）；
- 不要没有具体内容的纯寒暄闲聊（「今天好累」「吃了吗」这类不算）；
- 每条 ≤30 字，第三人称事实式，必须含具体话题词（如「聊了两小时XX游戏的服务器选择」）；
- 没有就输出空数组。

对话记录：
{transcript}

只输出 JSON：{{"moments": ["...", "..."]}}（最多 2 条）"""


async def extract_plain_moments(user_id: str, day: date, transcript_rows: list[dict]) -> int:
    """daily 第 9 件：提取→低权落库。返回新增条数；失败静默（不影响批次）。"""
    from .features import flag

    if not flag("plain_moments_enabled"):
        return 0
    msgs = [
        r for r in transcript_rows
        if r.get("role") in ("user", "assistant") and str(r.get("content") or "").strip()
    ]
    if len(msgs) < 6:  # 太平淡的日子不值得入账（省一次 LLM 调用）
        return 0
    transcript = "\n".join(f"{r['role']}: {str(r['content'])[:200]}" for r in msgs[-60:])
    try:
        from .llm import chat

        resp = await chat(
            [
                {"role": "system", "content": _PLAIN_MOMENT_PROMPT.format(transcript=transcript)},
                {"role": "user", "content": "提取今天平淡但有记头的当日事项。"},
            ],
            temperature=0.2,
            max_tokens=300,
            task="batch_other",
            thinking=False,  # 小预算 JSON：思考段会吃光 max_tokens 致正文空
        )
    except Exception:
        logger.warning("[平淡时刻] {} 提取失败（跳过当日）", user_id)
        return 0
    start, end = resp.find("{"), resp.rfind("}")

    try:
        data = json.loads(resp[start:end + 1]) if 0 <= start < end else {}
    except ValueError:
        return 0
    items = data.get("moments") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return 0

    # 14 天保留期：从「对话当天」起算（批处理次日跑，从当天算才名副其实）；
    # 到期由 fact_decay.decay_expired_facts 按 expires_at 通用清理，无需专门代码。
    expires_at = (
        datetime.combine(day, dtime.min) + timedelta(days=_RETENTION_DAYS)
    ).isoformat(timespec="seconds")
    source_ids = json.dumps([int(r["id"]) for r in msgs if r.get("id") is not None],
                            separators=(",", ":"))
    from .userdb import db

    added = 0
    for it in items[:_MAX_PER_DAY]:
        raw = it.get("content") if isinstance(it, dict) else it  # 容错：偶发包一层对象
        content = str(raw or "").strip()[:30]
        if not content:
            continue
        if db.add_fact(
            user_id,
            content,
            source_type="plain_moment",
            source_message_ids=source_ids,
            confidence=0.5,
            expires_at=expires_at,
        ) is not None:
            added += 1
    return added
