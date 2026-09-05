# -*- coding: utf-8 -*-
"""M5 惊喜编排：隔一阵子，她基于真实共同产物给对方一个小惊喜。

路线约束（TECH-PLAN M5：只基于真实经历、低频、可关闭）：
- 真实经历：素材只来自 artifacts（共同故事/共同书摘/目标回顾），prompt 里
  如实引用摘录，绝不编造新的共同经历；
- 低频：距上次惊喜 >= PROACTIVE_SURPRISE_MIN_GAP_DAYS（默认 14 天），到点
  后每次检查再过一道概率门（默认 35%）；
- 可关闭：PROACTIVE_SURPRISE_ENABLED=0 全局关闭；
- 出牌走统一仲裁器（initiative._arbited_proactive），消耗共享每日额度。
"""
from __future__ import annotations

import datetime
import random

from .log import logger
from .userdb import db, kv_get, kv_set

_LAST_SURPRISE_KEY = "surprise:last"          # kv：上次惊喜日期（ISO）
_LAST_SURPRISE_ARTIFACT_KEY = "surprise:last_artifact"  # kv：上次用过的产物 id
_MATERIAL_TYPES = ("co_story", "book_summary", "goal_review")
_TYPE_LABELS = {
    "co_story": "共同故事",
    "book_summary": "共同书摘",
    "goal_review": "目标回顾",
}
_EXCERPT_CHARS = 240


def _surprised_recently(user_id: str) -> bool:
    last = kv_get(user_id, _LAST_SURPRISE_KEY)
    if not last:
        return False
    from .config import config

    gap = datetime.date.today() - datetime.date.fromisoformat(str(last)[:10])
    return gap.days < config.proactive_surprise_min_gap_days


def _pick_material(user_id: str) -> dict | None:
    """挑一件近期的真实共同产物；优先没用过的，其次最近更新的。"""
    from .config import config

    cutoff = (datetime.datetime.now() - datetime.timedelta(
        days=config.proactive_surprise_material_days
    )).isoformat(timespec="seconds")
    with db._lock:
        rows = db.conn.execute(
            "SELECT id, artifact_type, title, content, updated_at FROM artifacts "
            "WHERE user_id = ? AND status = 'active' AND updated_at >= ? "
            f"AND artifact_type IN ({','.join('?' * len(_MATERIAL_TYPES))}) "
            "ORDER BY updated_at DESC, id DESC LIMIT 10",
            (user_id, cutoff, *_MATERIAL_TYPES),
        ).fetchall()
    if not rows:
        return None
    last_id = kv_get(user_id, _LAST_SURPRISE_ARTIFACT_KEY)
    fresh = [row for row in rows if str(row["id"]) != str(last_id or "")]
    picked = fresh[0] if fresh else rows[0]
    return {
        "id": int(picked["id"]),
        "artifact_type": picked["artifact_type"],
        "title": picked["title"],
        "updated_at": picked["updated_at"],
        "excerpt": str(picked["content"])[:_EXCERPT_CHARS],
    }


async def maybe_orchestrate_surprise(user_id: str, *, roll: int | None = None) -> str | None:
    """低频惊喜出牌；返回投递的文本或 None。roll 供测试注入随机数。"""
    from .config import config

    if not config.proactive_surprise_enabled:
        return None
    from .state import stage_of

    user = db.get_user(user_id)
    if not user:
        return None
    from .initiative import _STAGE_ORDER, _MIN_STAGE

    if _STAGE_ORDER.get(stage_of(user["affection"] or 0), 0) < _STAGE_ORDER[_MIN_STAGE]:
        return None
    if _surprised_recently(user_id):
        return None
    chance = config.proactive_surprise_chance_percent
    if chance <= 0:
        return None
    if (random.randrange(100) if roll is None else roll) >= chance:
        return None
    material = _pick_material(user_id)
    if material is None:
        return None

    from .initiative import _arbited_proactive
    from .persona import build_system_prompt

    async def produce() -> str | None:
        affection_val = user["affection"] or 0
        sys_prompt = build_system_prompt(
            stage=stage_of(affection_val),
            address=user["nickname_pref"] or "",
            lover_confirm=bool(user["lover_confirm"]),
            first_chat=False,
            affection=affection_val,
            user_id=user_id,
        )
        label = _TYPE_LABELS.get(material["artifact_type"], "共同回忆")
        msgs = [
            {"role": "system", "content": sys_prompt},
            {
                "role": "user",
                "content": (
                    f"你们在 {material['updated_at'][:10]} 一起留下了{label}"
                    f"《{material['title']}》：\n"
                    f"<real_memory>\n{material['excerpt']}\n</real_memory>\n"
                    "隔了这些日子，你想给对方一个小小的惊喜：用你现在的方式重新提起它——"
                    "比如给那个故事补一句番外、对那次一起读完的书说一句现在才有的感想、"
                    "或者轻轻纪念那个一起完成的目标。两三句就够。"
                    "只能基于标签里的真实经历，绝不编造新的共同经历；"
                    "自然开口，不要报告腔，不要复述摘录，别加括号动作。"
                ),
            },
        ]
        from .llm import chat

        return (await chat(msgs, max_tokens=120, temperature=0.9)).strip()[:200]

    def on_delivered() -> None:
        kv_set(user_id, _LAST_SURPRISE_KEY, datetime.date.today().isoformat())
        kv_set(user_id, _LAST_SURPRISE_ARTIFACT_KEY, str(material["id"]))

    text = await _arbited_proactive(
        user_id,
        source="initiative:surprise",
        idle_minutes=config.proactive_surprise_idle_minutes,
        done_today=lambda: _surprised_recently(user_id),
        produce=produce,
        on_delivered=on_delivered,
    )
    if text:
        logger.info("[惊喜] 已基于产物 #{} 送出一个小惊喜", material["id"])
    return text
