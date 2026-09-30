# -*- coding: utf-8 -*-
"""内置工具：记忆读写（对标 Harness 的 memory_search / memory_add）。

- memory_search：语义检索长期记忆 + 事实（LLM 主动回忆用户偏好/约定/历史）
- memory_add：显式写入一条长期记忆
"""
from __future__ import annotations

import asyncio

from ..base import ToolRegistry, tool_failure
from ...core import userdb
from ...core.current_user import current_user_id
from ...core.log import logger

_TOP_K = 6


def _uid() -> str:
    return current_user_id.get()


def _pool_gated_hits(uid: str, hits: list) -> list:
    """跨分区向量命中按 kind 分池回查权威表（P3-29）。

    薄壳版 search 丢 kind，小整数 record_id 跨分区碰撞时会回错表（facts 命中
    被当成 long_memory 内容返回）。lm/facts 池闸门故障 fail-open；其余分区
    （topic/diary/…）无本工具的权威映射，宁少勿错直接跳过。
    """
    pools: dict[str, list] = {}
    for h in hits:
        pools.setdefault(str(h.meta.get("kind") or ""), []).append(h)
    gated: list = []
    for kind, khits in pools.items():
        try:
            if kind == "lm":
                alive = userdb.db.recallable_long_memory_ids(uid, [h.record_id for h in khits])
            elif kind == "facts":
                alive = userdb.db.recallable_fact_ids(uid, [h.record_id for h in khits])
            else:
                continue
        except Exception:
            alive = {h.record_id for h in khits}
        gated.extend(h for h in khits if h.record_id in alive)
    return gated


async def _memory_search(query: str = "", top_k: int = 0) -> str:
    """语义检索长期记忆与事实，返回相关内容。

    #27 相对时间推理：query 含「上周/前天/N天前」等表达时，解析出绝对日期范围
    （时间线索行）并附范围内对话原文（限量）——lm/facts 是提炼后的记忆，
    「上周我们聊了什么」的原始答案在 messages 表里。
    """
    if not query:
        return tool_failure("（缺少检索内容）")
    uid = _uid()
    k = max(1, min(top_k or _TOP_K, 20))

    # 0) 相对时间解析（纯函数，失败即无时间线索）
    time_line = ""
    transcript_part = ""
    try:
        from ...core.relative_time import resolve_ranges
        from datetime import date as _date

        ranges = resolve_ranges(query, _date.today())
        if ranges:
            r = ranges[0]
            time_line = f"【时间线索】『{r.label}』= {r.start} ~ {r.end}（含端点）"
            rows = userdb.db.messages_between(uid, _date.fromisoformat(r.start), _date.fromisoformat(r.end))
            if rows:
                lines = [f"{row['role']}: {str(row['content'])[:80]}" for row in rows[-12:]]
                transcript_part = "【该时段对话原文（末 12 条）】\n" + "\n".join(lines)
    except Exception:
        pass  # 时间解析失败不影响主检索

    # 1) 关键词 + 语义混合检索长期记忆原文
    try:
        # memory_search 显式调用总是走混合检索（kind 必填，漏传会 TypeError 退回旧检索）
        from ...core.memory import _recall_with_expansion
        lm = await _recall_with_expansion(uid, query, kind="lm", mock=False)
    except Exception:
        lm = [h["content"] for h in userdb.db.search_long_memory(uid, query, k)]

    # 2) 检索事实
    try:
        from ...core.memory import _facts_with_expansion
        facts = await _facts_with_expansion(uid, query, kind="facts", mock=False)
    except Exception:
        facts = [h["content"] for h in userdb.db.search_facts(uid, query, k)]

    # 3) 向量检索兜底（显式工具调用时无论如何都查一次稠密向量）。
    # P3-29：必须用带 SearchHit.meta 的完整版 search——薄壳丢 kind，跨分区
    # id 碰撞会回错表；命中再经 _pool_gated_hits 按权威表分池过滤。
    try:
        from ...core.memory import vector_store as vs

        hits = await asyncio.to_thread(vs.search, uid, query, k, None)
        for h in _pool_gated_hits(uid, hits):
            if h.text and h.text not in lm and len(lm) < k:
                lm.append(h.text)
    except Exception as e:
        # 向量兜底失败只影响召回丰富度，不阻断主流程；记日志而非静默吞掉
        logger.warning("[memory_search] 向量兜底检索失败: {}", e)

    parts = []
    if time_line:
        parts.append(time_line)
    if lm:
        parts.append("【长期记忆】\n" + "\n".join(f"- {m}" for m in lm[:k]))
    if facts:
        parts.append("【长期事实】\n" + "\n".join(f"- {f}" for f in facts[:k]))
    if transcript_part:
        parts.append(transcript_part)
    if not parts:
        return "（未找到相关记忆）"
    return "\n\n".join(parts)


async def _memory_add(content: str = "") -> str:
    """写入一条长期记忆。"""
    if not content:
        return tool_failure("（缺少记忆内容）")
    uid = _uid()
    # pinned=True：用户/LLM 显式要求记住的内容打保护位，不会被容量轮转清理
    # （clean_old_long_memory / prune_long_memory 均跳过 pinned 行）
    mid = userdb.db.add_long_memory(uid, content, pinned=True)
    # 建语义向量索引（失败静默）
    try:
        from ...core.vector_store import index as vec_index
        await asyncio.to_thread(vec_index, uid, mid, content, "lm")
    except Exception:
        logger.warning("[memory_add] 向量索引写入失败（SQLite 已落库，回填任务会补）")
    return f"✅ 已写入长期记忆 #{mid}"


def register() -> None:
    ToolRegistry.register_func(
        name="memory_search",
        description="语义检索长期记忆与事实（用户偏好、约定、历史事件、你的画像）。当你需要回忆用户过去说过什么、喜欢什么、讨厌什么时使用",
        func=_memory_search,
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "想检索的记忆内容（如：用户的爱好）"},
                "top_k": {"type": "integer", "description": "返回条数（默认 6，最大 20）"}
            },
            "required": ["query"],
        },
    )
    ToolRegistry.register_func(
        name="memory_add",
        description="主动写入一条长期记忆（重要事实、用户偏好、约定承诺）。用于把值得长期记住的信息存下来",
        func=_memory_add,
        input_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "要长期记住的内容"}
            },
            "required": ["content"],
        },
        category="write",
        needs_confirm=True,
    )
