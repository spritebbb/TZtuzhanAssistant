# -*- coding: utf-8 -*-
"""38项#22 外部素材管线：授权兴趣源定期拉外部内容 → 素材池 → 统一仲裁低频分享。

动机（38 项清单原文）：主动素材全内部循环（你们的事/她的虚构日常/手记），
新鲜度必衰减。授权兴趣源给主动消息接上外部输入。

契约：
- **授权域**：只有用户明确登记进 interest_feeds 的主题才会被检索——不猜兴趣、
  不自动扩源（隐私与噪声双闸）；
- 拉取：每源每 PULL_INTERVAL_HOURS(72h) 一次 web_search → 小 LLM 压缩成
  1 条 ≤80 字素材入 interest_feed_items（fail-soft，不写空摘要）；
- 消费：initiative 次级源 _maybe_interest_share 走统一仲裁出牌，necessity
  门槛高于心事/约定（外部的事不压过你们之间的事）；出牌置 used_at；
- 成本闸：模块名 "feed"，hard/extreme 档暂停拉取；
- 素材 5 天保鲜（unused_feed_items 过滤），过期自然沉底。
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from .log import logger

PULL_INTERVAL_HOURS = 72  # 每源拉取间隔：外部分享是低频点缀，不是资讯流
_FEED_LOOP_SEC = 3600     # 常驻循环轮询间隔


async def pull_feed(user_id: str, feed_id: int, keywords: str) -> int:
    """拉取一个兴趣源并压缩入库，返回新增素材条数（0~1）。"""
    from . import cost_guard
    from .features import flag
    from .llm import chat
    from .search import web_search
    from .userdb import db

    if not flag("interest_feeds_enabled"):
        return 0
    if not cost_guard.check("feed"):
        return 0
    try:
        results = await asyncio.to_thread(web_search, keywords, 5)
        titles = [str(r.get("title") or "").strip() for r in (results or []) if r.get("title")]
        if not titles:
            db.set_feed_pulled(feed_id)  # 空结果也计一次拉取，防死循环重试
            return 0
        material = "\n".join(f"- {t[:60]}" for t in titles[:5])
        resp = await chat(
            [
                {"role": "system", "content": (
                    "把搜索结果的要点压缩成一条给朋友分享用的中文短讯：≤80字、口语、"
                    "只挑最值得说的一件事、不带日期前缀、不列清单、不写来源。"
                    "没有值得说的就只输出一个句号。"
                )},
                {"role": "user", "content": f"关键词：{keywords}\n搜索结果：\n{material}"},
            ],
            temperature=0.3,
            max_tokens=120,
            task="batch_other",
            thinking=False,
        )
        text = (resp or "").strip().splitlines()[0].strip(" \t\"'“”")[:80] if resp else ""
        if not text or text == "。" or len(text) < 8:
            db.set_feed_pulled(feed_id)
            return 0
        n = db.add_feed_items(user_id, feed_id, [text])
        db.set_feed_pulled(feed_id)
        logger.info("[兴趣源] {} 「{}」新素材：{}", user_id, keywords, text[:40])
        return n
    except Exception:
        logger.exception("[兴趣源] {} 拉取失败（fail-soft）", keywords)
        return 0


async def pull_due_feeds(user_id: str) -> int:
    """拉取所有到期（超过间隔）的启用源，返回新增素材总数。"""
    from .userdb import db

    now = datetime.now()
    total = 0
    for feed in db.list_interest_feeds(user_id, only_enabled=True):
        last = str(feed["last_pull_at"] or "")
        due = True
        if last:
            try:
                pulled = datetime.fromisoformat(last)
                due = (now - pulled).total_seconds() >= PULL_INTERVAL_HOURS * 3600
            except ValueError:
                due = True
        if due:
            total += await pull_feed(user_id, int(feed["id"]), str(feed["keywords"]))
    return total


async def feed_loop() -> None:
    """常驻循环：每小时检查一次到期源（无源时零动作零成本）。"""
    from .persona_profiles import active_user_id
    from .userdb import db

    while True:
        try:
            uid = active_user_id()
            if uid and db.get_user(uid):
                await pull_due_feeds(uid)
        except Exception:
            logger.exception("[兴趣源] 循环异常（继续轮询）")
        await asyncio.sleep(_FEED_LOOP_SEC)
