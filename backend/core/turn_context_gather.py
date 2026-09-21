# -*- coding: utf-8 -*-
"""本轮对话的上下文采集：所有「读回来喂给模型」的旁路检索。

从 ``pipeline._process_locked`` 抽出的第二段。与 :mod:`turn_effects` 相对：那边是
写副作用，这边是读上下文。共同点是**每个采集器失败都必须降级成空值**，让回复照常
生成——搜索/知识库/共读/专注/目标/创作/酒馆/清单/关系事件/记忆任一路挂掉，都只是
「这一轮少一点背景」，不是「这一轮没有回复」。

每个采集器返回 ``(value, failure)``：``failure`` 为 ``None`` 表示成功，否则是
``(名称, 异常)``。调用方把 failure 记进台账、把 value 当默认值继续——这样「哪一路
降级了」在台账里看得见，而不是散落在一行行日志里。
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from .log import logger

Failure = tuple[str, BaseException] | None


async def _to_thread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """把同步阻塞检索放线程池，避免卡事件循环（原 pipeline 各处同做法）。"""
    return await asyncio.to_thread(fn, *args, **kwargs)


async def gather_memories(user_id: str, text: str, *, mock: bool) -> tuple[dict, Failure]:
    """短期记忆 + 长期事实 + 事实 id 反查（供解释快照附生命周期元数据）。"""
    from .memory import recall, recall_facts
    from .userdb import db

    try:
        remembered = await recall(user_id, text, mock=mock)
        facts = await recall_facts(user_id, text, mock=mock)
        fact_id_map = await asyncio.to_thread(db.fact_ids_by_content, user_id, facts)
    except Exception as exc:  # noqa: BLE001
        return {"remembered": [], "facts": [], "fact_id_map": {}}, ("记忆检索", exc)
    return (
        {"remembered": remembered, "facts": facts, "fact_id_map": fact_id_map},
        None,
    )


async def gather_knowledge(user_id: str, text: str, *, mock: bool) -> tuple[list[dict], Failure]:
    """D2 知识库召回：距离阈值门控，不像就一条不注入。"""
    if mock:
        return [], None
    from .knowledge import recall_knowledge

    try:
        hits = await _to_thread(recall_knowledge, user_id, text)
    except Exception as exc:  # noqa: BLE001
        return [], ("知识库检索", exc)
    return list(hits or []), None


async def gather_reading(user_id: str, text: str) -> tuple[str, Failure]:
    """D3 共读背景：只在用户显然在讨论阅读内容时注入。"""
    from .activities import active_reading_context

    try:
        return await _to_thread(active_reading_context, user_id, text), None
    except Exception as exc:  # noqa: BLE001
        return "", ("共读上下文", exc)


async def gather_focus(user_id: str, text: str, *, ephemeral: bool) -> tuple[str, Failure]:
    """M3.2 专注陪伴：只在谈到专注/计时时注入当前状态。"""
    from .focus import focus_context

    try:
        return (
            await _to_thread(focus_context, user_id, text, settle=not ephemeral),
            None,
        )
    except Exception as exc:  # noqa: BLE001
        return "", ("专注上下文", exc)


async def gather_goal(user_id: str, text: str) -> tuple[str, Failure]:
    """M3.3 共同目标：只在目标/计划相关话题下注入真实进展。"""
    from .goals import goal_context

    try:
        return await _to_thread(goal_context, user_id, text), None
    except Exception as exc:  # noqa: BLE001
        return "", ("共同目标上下文", exc)


async def gather_writing(user_id: str, text: str) -> tuple[str, Failure]:
    """M3.4 共同创作：只在创作/故事话题注入虚构素材（自带虚构声明）。"""
    from .cowriting import cowriting_context

    try:
        return await _to_thread(cowriting_context, user_id, text), None
    except Exception as exc:  # noqa: BLE001
        return "", ("共同创作上下文", exc)


async def gather_tavern(user_id: str, text: str) -> tuple[str, Failure]:
    """酒馆同玩回忆：真回忆，门控在 tavern_context 内部。"""
    from .tavern import tavern_context

    try:
        return await _to_thread(tavern_context, user_id, text), None
    except Exception as exc:  # noqa: BLE001
        return "", ("酒馆回忆", exc)


async def gather_list_context(
    user_id: str, text: str, *, turn_id: int, ephemeral: bool
) -> tuple[dict, Failure]:
    """M3.4 共同清单 + P1-02 语境注册表（两路互斥，不会双注入）。

    返回 ``{"text": str, "selection": ContextSelection | None}``；``selection``
    在注册表开启时非空，供调用方在回复提交后推进语境生命周期。
    """
    from .features import flag

    try:
        if flag("context_registry_enabled"):
            from .context_registry import collect_context

            selection = await _to_thread(
                collect_context,
                user_id,
                text,
                turn_id=turn_id,
                state={"ephemeral": ephemeral},
            )
            if not ephemeral:
                # P3-05B 统计：语境来源选择计数（只记 entry id，不记内容）
                try:
                    from .experience_metrics import record as _metric

                    for item in selection.explain()[:5]:
                        _metric(user_id, "source_pick", str(item.get("id", ""))[:60])
                except Exception:  # noqa: BLE001 —— 统计失败不影响语境
                    pass
            return {"text": selection.assemble(), "selection": selection}, None

        from .colists import list_context

        return {"text": await _to_thread(list_context, user_id, text), "selection": None}, None
    except Exception as exc:  # noqa: BLE001
        return {"text": "", "selection": None}, ("共同清单上下文", exc)


async def gather_event_recall(user_id: str, text: str) -> tuple[dict, Failure]:
    """M2 关系事件回忆：只回忆真实发生过的约定/特殊日子。"""
    from .relationship_events import event_recall

    try:
        result = await _to_thread(event_recall, user_id, text)
    except Exception as exc:  # noqa: BLE001
        return {"context": "", "sources": []}, ("关系事件回忆", exc)
    return result or {"context": "", "sources": []}, None


async def gather_search(text: str, *, mock: bool, needs_search: bool) -> tuple[dict, Failure]:
    """联网搜索 / 天气。天气类先取句内城市，取不到才回落 MOOD_CITY。"""
    if mock or not needs_search:
        return {"hits": [], "report": None}, None

    from . import pipeline as _pipeline

    hits: list[dict] = []
    report = None
    try:
        if any(
            k in text
            for k in ("天气", "温度", "冷", "热", "下雨", "气温", "天气预报", "多少度")
        ):
            from .config import config as _cfg

            city = _pipeline._extract_city(text) or _cfg.mood_city
            if city:
                weather_line = await _to_thread(_pipeline._fetch_weather, city)
                if weather_line:
                    hits = [
                        {
                            "id": "E1",
                            "title": f"{city}今日天气",
                            "snippet": weather_line,
                            "url": f"https://wttr.in/{city}",
                            "domain": "wttr.in",
                            "provider": "wttr",
                            "cache_hit": False,
                        }
                    ]
                    report = {
                        "status": "insufficient",
                        "agreement": "not_comparable",
                        "reason": "single_specialized_weather_source",
                        "evidence": hits,
                    }
    except Exception:  # noqa: BLE001 —— 天气是尽力而为，失败回落多源求证
        logger.debug("[pipeline] 天气查询失败，回落多源求证")
        hits = []

    if hits:
        return {"hits": hits, "report": report}, None

    from .source_verification import verify_search

    try:
        report = await _to_thread(verify_search, text)
    except Exception as exc:  # noqa: BLE001
        return {"hits": [], "report": None}, ("联网搜索", exc)
    return {"hits": list(report.get("evidence", [])), "report": report}, None


async def gather_situation(user_id: str) -> tuple[str, Failure]:
    """D9 局势档案：常驻世界快照（目标/约定/悬念/近事件/生活/焦点）。

    与其他采集器不同：它不做话题门控——「钩子恒定在场」正是这一层的意义
    （不靠检索命中）。flag 关闭时返回空串。
    """
    from .situation import situation_context

    try:
        return await _to_thread(situation_context, user_id), None
    except Exception as exc:  # noqa: BLE001
        return "", ("局势档案", exc)


async def gather_all(
    user_id: str,
    text: str,
    *,
    mock: bool,
    ephemeral: bool,
    turn_id: int,
    needs_search: bool,
) -> tuple[dict, list[tuple[str, BaseException]]]:
    """按固定顺序采集全部上下文，返回 ``(结果字典, 失败列表)``。

    失败列表交给调用方记进台账；结果字典里对应项已是降级默认值，可直接使用。

    刻意**顺序执行**而非 ``asyncio.gather``：这些采集器读的是同一份会话/关系状态，
    并发化会改变可观测时序（也会让「哪一路先降级」随线程池调度漂移），收益只是
    每轮省下的几百毫秒，不值得拿行为一致性换。顺序也与抽取前逐字一致。
    """
    gathered = [
        await gather_situation(user_id),
        await gather_memories(user_id, text, mock=mock),
        await gather_knowledge(user_id, text, mock=mock),
        await gather_reading(user_id, text),
        await gather_focus(user_id, text, ephemeral=ephemeral),
        await gather_goal(user_id, text),
        await gather_writing(user_id, text),
        await gather_tavern(user_id, text),
        await gather_list_context(user_id, text, turn_id=turn_id, ephemeral=ephemeral),
        await gather_event_recall(user_id, text),
        await gather_search(text, mock=mock, needs_search=needs_search),
    ]
    (
        situation,
        memories,
        knowledge,
        reading,
        focus,
        goal,
        writing,
        tavern,
        lists,
        events,
        search,
    ) = gathered

    failures = [f for value, f in gathered if f is not None]
    result = {
        "situation_ctx": situation[0],
        "remembered": memories[0]["remembered"],
        "facts": memories[0]["facts"],
        "fact_id_map": memories[0]["fact_id_map"],
        "kb_hits": knowledge[0],
        "reading_context": reading[0],
        "focus_ctx": focus[0],
        "goal_ctx": goal[0],
        "writing_ctx": writing[0],
        "tavern_mem_ctx": tavern[0],
        "list_ctx": lists[0]["text"],
        "context_selection": lists[0]["selection"],
        "event_recall_result": events[0],
        "search_hits": search[0]["hits"],
        "search_report": search[0]["report"],
    }
    return result, failures


__all__ = ["gather_all"]
