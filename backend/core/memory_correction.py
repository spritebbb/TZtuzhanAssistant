# -*- coding: utf-8 -*-
"""对话内记忆纠偏（C7 第二步）：检测纠正语 → LLM 仲裁定位错误事实 → 真删。

设计原则（宁缺勿滥）：
- 只在用户说出明确纠正语（"你记错了""我没说过"等）时才触发仲裁；
- 仲裁只删「用户明确否定」的事实，拿不准一律不删；
- 单次最多删 3 条，全部删除都记日志可审计；
- 删 SQLite facts 的同时清理对应向量索引（向量是索引，SQLite 才是事实源）。
"""
from __future__ import annotations

import re

from .llm import chat
from .log import logger
from .userdb import delete_fact, list_facts

# 明确纠正语（刻意收窄：日常吐槽/调侃不触发仲裁，防误删）
_CORRECTION_RE = re.compile(
    r"你记错|记错了|你搞错|搞错了|不是那样的|不是你想的那样|"
    r"我什么时候说过|我没说过|我从没说过|你说的不对|你记岔了"
)

ARBITER_PROMPT = """你是记忆仲裁员。用户刚刚纠正了助手「菟菚」记错的事。
下面是她记住的关于用户的事实清单（带编号），以及用户最近的对话上下文。

判断哪些事实被用户**明确否定或纠正**了，只输出 JSON：
{"wrong_ids": [编号整数...], "reason": "一句短话说明"}

规则：
- 只有被明确否定/与纠正内容直接矛盾的事实才列入；
- 拿不准、只是语气不好的，一律不列（宁缺勿滥）；
- 最多列 3 条；
- 没有就输出 {"wrong_ids": [], "reason": ""}。"""


def is_correction(text: str) -> bool:
    """用户这句话是否在明确纠正菟菚的记忆。"""
    return bool(_CORRECTION_RE.search(text or ""))


async def arbitrate_and_forget(user_id: str, text: str, recent_context: str = "") -> list[int]:
    """LLM 仲裁定位被否定的事实并删除。返回被删除的事实 id 列表。"""
    facts = list_facts(user_id, 50)
    if not facts:
        return []
    numbered = "\n".join(f"{f['id']}. {f['content']}" for f in facts)
    try:
        from .persona_profiles import persona_name_for_user_id

        arbiter_prompt = ARBITER_PROMPT.replace("菟菚", persona_name_for_user_id(user_id))
    except Exception:
        arbiter_prompt = ARBITER_PROMPT
    try:
        resp = await chat(
            [
                {"role": "system", "content": arbiter_prompt},
                {
                    "role": "user",
                    "content": (
                        f"事实清单：\n{numbered}\n\n"
                        f"用户刚刚说：{text}\n"
                        + (f"最近对话：\n{recent_context}" if recent_context else "")
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=200,
        )
    except Exception:
        logger.warning("[记忆纠偏] {} 的仲裁调用失败，本次不删", user_id)
        return []

    from .daily import _parse_json

    data = _parse_json(resp)
    raw_ids = data.get("wrong_ids") if isinstance(data, dict) else None
    if not isinstance(raw_ids, list) or not raw_ids:
        return []

    valid_ids = {f["id"] for f in facts}
    deleted: list[int] = []
    for raw in raw_ids[:3]:
        try:
            fid = int(raw)
        except (TypeError, ValueError):
            continue
        if fid not in valid_ids:
            continue
        if delete_fact(user_id, fid):
            try:
                from .vector_store import delete as vec_delete

                vec_delete(user_id, "facts", fid)
            except Exception:
                pass
            deleted.append(fid)
    if deleted:
        reason = str(data.get("reason") or "")[:60]
        logger.info("[记忆纠偏] {} 纠正生效，删除事实 {}（{}）", user_id, deleted, reason)
    return deleted
