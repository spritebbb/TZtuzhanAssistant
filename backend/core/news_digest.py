# -*- coding: utf-8 -*-
"""38项#23 事件感：她每天知道一件外部世界的事，进问候素材池。

契约（用户拍板 2026-09-30：web_search 每日一条，默认开）：

- **每日一条幂等**：kv ``greeting:news:{day}``（kv_registry 已登记，scope=daily）
  已有当日摘要则不再生成；成功压缩才写 kv，失败静默等下一轮（fail-soft）；
- **三道闸**：flag ``news_digest_enabled`` 关 → 不跑；当日 kv 已有 → 不跑；
  cost_guard ``news`` 在 hard/extreme 档 → 不跑（用户聊天不受影响）；
- **生成**：web_search 取前几条 title+snippet，喂小 LLM（task="batch_other"，
  thinking=False）压缩成一条 ≤60 字的中文口语摘要——不选灾难血腥类、不带日期
  前缀、只讲一件事；
- **消费**：greeting_material._collect_news 把当日摘要作为问候素材的**末位**
  补充（关系素材优先），素材标注明确「外部世界的事，不是你们之间的事」；
- 常驻循环 news_loop：每 3600s 为 active persona 的 user 补当日摘要，
  异常全捕获，绝不拖垮服务。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

from .log import logger

_LOOP_INTERVAL_SEC = 3600
_SEARCH_QUERY = "今天 重要新闻"
_SEARCH_MAX_RESULTS = 5
_SUMMARY_MAX_CHARS = 60

# 压缩提示：一条、口语、不带日期前缀、不选灾难血腥类
_COMPRESS_PROMPT = (
    "下面是今天外部世界的几条新闻标题和摘要。请把它们压缩成一条不超过60字的中文口语转述，"
    "要求：只讲其中一件最值得顺口一提的事；像随口听来的口气；不要带日期前缀；"
    "不要选灾难、事故、血腥、恶性事件类的内容；不要添加列表之外的信息；"
    "直接输出这一句话，不要引号，不要解释。\n"
)


def _kv_key(day: str) -> str:
    return f"greeting:news:{day}"


def _clean_summary(text: str) -> str:
    """模型输出的保守清洗：去首尾空白与引号、取第一个非空行、截到 60 字。"""
    summary = str(text or "").strip()
    for quote in ('"', "'", "“", "”", "『", "」"):
        summary = summary.strip(quote)
    for line in summary.splitlines():
        line = line.strip()
        if line:
            summary = line
            break
    else:
        return ""
    return summary[:_SUMMARY_MAX_CHARS]


async def ensure_daily_news(user_id: str, day: str) -> None:
    """确保当日外部世界摘要存在（幂等、fail-soft，任何失败都不写 kv）。

    成功路径：搜索 → 小 LLM 压缩成一条 ≤60 字摘要 → 写 kv（摘要+ISO 时间）。
    """
    try:
        from .userdb import kv_get, kv_set

        if kv_get(user_id, _kv_key(day)):
            return  # 当日已生成，幂等
        from .features import flag

        if not flag("news_digest_enabled"):
            return
        from .cost_guard import check as _cost_ok

        if not _cost_ok("news"):
            return  # hard/extreme 档暂停自主 LLM 工作
        from .search import web_search

        # web_search 是同步阻塞调用，放线程池避免卡事件循环
        results = await asyncio.to_thread(web_search, _SEARCH_QUERY, _SEARCH_MAX_RESULTS)
        if not results:
            return  # 搜索失败静默返回，等下一轮
        entries: list[str] = []
        for item in results:
            title = str(item.get("title") or "").strip()
            snippet = str(item.get("snippet") or "").strip()[:100]
            if title:
                entries.append(f"- {title}" + (f"：{snippet}" if snippet else ""))
        if not entries:
            return
        from .llm import chat

        text = await chat(
            [{"role": "user", "content": _COMPRESS_PROMPT + "\n".join(entries)}],
            task="batch_other",
            max_tokens=120,
            thinking=False,
            temperature=0.3,
        )
        summary = _clean_summary(text)
        if not summary:
            return  # 压缩出空结果视为失败，不写 kv
        kv_set(user_id, _kv_key(day), json.dumps(
            {"summary": summary, "generated_at": datetime.now().isoformat(timespec="seconds")},
            ensure_ascii=False,
        ))
        logger.info("[事件感] {} 生成当日摘要（{} 字）", user_id, len(summary))
    except Exception as exc:
        # 全程异常静默（fail-soft）：事件感是锦上添花，绝不拖垮调用方
        logger.warning("[事件感] {} 当日摘要生成失败：{}", user_id, exc)


def news_line(user_id: str, day: str) -> str | None:
    """读当日摘要；无或坏数据返回 None（不往上抛）。"""
    try:
        from .userdb import kv_get

        raw = kv_get(user_id, _kv_key(day))
        if not raw:
            return None
        data = json.loads(raw)
        summary = str(data.get("summary") or "").strip()
        return summary or None
    except Exception:
        return None


async def news_loop() -> None:
    """后台事件感循环：每 3600s 为 active persona 的 user 补当日摘要。

    异常全捕获，绝不因事件感故障拖垮服务（同 initiative_loop 的容错约定）。
    """
    logger.info("[事件感] 循环启动，轮询间隔 {}s", _LOOP_INTERVAL_SEC)
    while True:
        try:
            from .persona_profiles import active_user_id
            from .userdb import db

            uid = active_user_id()
            if db.get_user(uid):
                await ensure_daily_news(uid, datetime.now().date().isoformat())
        except Exception as e:
            logger.warning("[事件感] 循环异常: {}", e)
        await asyncio.sleep(_LOOP_INTERVAL_SEC)


__all__ = ["ensure_daily_news", "news_line", "news_loop"]
