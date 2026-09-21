# -*- coding: utf-8 -*-
"""本轮用户消息的旁路副作用：不产出 prompt 内容的那一半。

从 ``pipeline._process_locked`` 抽出的第一段。这些动作都发生在「用户消息已落库」
之后、「组装 prompt」之前，特征是**确定性、有则记、失败不得影响回复**：

- 关系与状态入账：重逢推进、注意力漂移、开放问题登记、幽默记忆反馈；
- 用户教学入账：偏好教学、学习候选、活动草稿、求助回应、会话节奏；
- 即时奖励：用称呼交流 / 引用共同回忆的关键词加分；
- 惰性提炼调度：事实 / 画像 / 话题 / 结构化五元组（后台，不阻塞回复）；
- 聊天派活：明确要求「分几步做」时创建 Agent 任务并回注一条系统提示。

原先这些动作各自包着 ``try / except Exception``，失败只留一行日志。现在统一走
:class:`~backend.core.effect_ledger.EffectLedger`：失败仍不抛、行为不变，但
「这一轮吞掉了什么」变成可读的台账，并计入进程级统计供 ``/api/meta`` 观测。
"""
from __future__ import annotations

from typing import Any, Callable

from . import affection
from .effect_ledger import EffectLedger
from .log import logger
from .userdb import db


async def _run_dispatch_agent(
    user_id: str, text: str, messages: list[dict] | None
) -> None:
    """聊天派活：明确「分几步做 / 用任务代理」时建 Agent 任务并回注系统提示。"""
    from ..agent.session import create_task as _agent_create, detect_dispatch_request

    objective = detect_dispatch_request(text)
    if not objective:
        return
    task = await _agent_create(user_id, objective)
    try:
        with db._lock:
            db.create_task(
                user_id, f"[Agent任务] {objective}", priority="P1", phase="agent"
            )
    except Exception:
        logger.exception("[pipeline] Agent 任务关联待办失败（不影响任务）")
    if messages is not None:
        messages.append(
            {
                "role": "system",
                "content": (
                    f"（系统提示：已按用户要求创建 Agent 任务 #{task.id}，"
                    f"目标「{objective}」，拆成 {len(task.plan)} 步。"
                    "请在回复里自然地告诉用户：计划已生成，去「任务代理」面板确认要执行哪几步；"
                    "不要假装已经开始执行。）"
                ),
            }
        )
    logger.info("[pipeline] 聊天派活 → Agent 任务 {}（{} 步）", task.id, len(task.plan))


async def _run_activity_draft(
    user_id: str, text: str, *, turn_id: int, draft_cb: Callable[[Any], Any] | None
) -> None:
    """F06：用户明确说「想一起做 X」时产一张签名短期草稿（不落库）。"""
    if draft_cb is None:
        return
    from .activity_drafts import create_draft, detect_draft_intent

    intent = detect_draft_intent(text)
    if intent is None:
        return
    draft = create_draft(user_id, intent[0], intent[1], source_turn_id=turn_id or None)
    if draft is not None:
        await draft_cb(draft)


def _run_attention(user_id: str, text: str, turn_id: int) -> None:
    """§17.1 注意力漂移：每轮推进话题权重（显式转题立即置顶），只存 topic id。"""
    from . import attention_state as _attention
    from .context import topic_switch_hint

    key = _attention.topic_key(text)
    recent = [str(m["content"] or "") for m in db.recent_messages(user_id, 4)]
    prev = recent[:-1] if recent and recent[-1].strip() == text.strip() else recent
    if topic_switch_hint(prev, text):
        _attention.switch_topic(user_id, key, turn_id=turn_id or 0)
    else:
        _attention.advance_turn(user_id, key, turn_id=turn_id or 0)


def _run_open_question(user_id: str, text: str, turn_id: int) -> None:
    """G03：用户明确要求「以后有结果告诉我」才记下待查（确定性正则，无 LLM）。"""
    from .open_questions import detect_tracking_request, track_question

    topic = detect_tracking_request(text)
    if topic:
        track_question(user_id, topic, source_message_id=turn_id or None)


def _run_humor_feedback(user_id: str, text: str, turn_id: int) -> None:
    """L04：明确反馈（「这个梗好」/「别玩这个梗」）记到上一轮实际用过的梗上。"""
    from .humor_memory import record_feedback_from_reply

    record_feedback_from_reply(user_id, text, turn_id=turn_id or None)


def _run_companion_response(user_id: str, text: str, turn_id: int) -> None:
    """G04：她 24h 内发过求助，用户这句若接受/拒绝就走同一 respond；猜不到不打扰。"""
    from .companion_requests import respond_to_reply

    responded = respond_to_reply(user_id, text, message_id=turn_id or None)
    if responded:
        logger.info("[pipeline] 求助回应已入账：{}", responded.get("status"))


def _run_rhythm(user_id: str, text: str, turn_id: int) -> None:
    """P2-05：晚安/停止/换题取消旧追发；临时离开时挂一条有期限追发。"""
    from .conversation_rhythm import handle_user_turn

    handle_user_turn(user_id, turn_id, text)


def _run_preference_teaching(user_id: str, text: str, turn_id: int) -> None:
    """P2-02 用户教学：明确指令确定性提取入账；疑似推断不出手。"""
    from .user_preferences import propose_preference

    propose_preference(user_id, text, turn_id)


def _run_learning_candidate(user_id: str, text: str, turn_id: int) -> None:
    """§17.2 学习候选生产者：明确教学句式 → 候选（低风险自动确认）。"""
    from .learning_pipeline import propose_from_message

    propose_from_message(user_id, text, source_message_id=turn_id or None)


def _run_instant_bonus(
    user_id: str, text: str, *, nickname_pref: str | None, persona_name: str
) -> None:
    """即时关键词奖励：不打 LLM、不依赖语义感知，保证即时反馈。"""
    if affection.check_nickname_used(text, nickname_pref):
        affection.try_daily_bonus(
            user_id, "nickname", affection.NICKNAME_BONUS, f"用{persona_name}的称呼交流"
        )
    from .memory import looks_like_recall

    if looks_like_recall(text):
        affection.try_daily_bonus(
            user_id, "memory", affection.MEMORY_REFERENCE_BONUS, "提到共同经历/回忆"
        )


def _schedule_lazy_extraction(user_id: str, prev_ts: str | None) -> None:
    """惰性提炼调度：事实 / 画像 / 话题 / 三元组，全部丢后台，不阻塞本轮回复。"""
    from . import pipeline as _pipeline
    from .features import flag
    from .tasks import schedule

    unseen = db.max_message_id(user_id) - db.get_last_fact_msg_id(user_id)
    if unseen >= 10 or (unseen >= _pipeline._IDLE_MIN_NEW and _pipeline._long_gap(prev_ts)):
        from .daily import extract_facts

        schedule(f"facts:{user_id}", lambda: extract_facts(user_id))

    if flag("profile_enabled"):
        p_unseen = db.max_message_id(user_id) - db.get_last_profile_msg_id(user_id)
        if p_unseen >= 10 or (
            p_unseen >= _pipeline._IDLE_MIN_NEW and _pipeline._long_gap(prev_ts)
        ):
            schedule(f"profile:{user_id}", lambda: _pipeline._extract_profile(user_id))

    if _pipeline._long_gap(prev_ts):
        schedule(f"topic:{user_id}", lambda: _pipeline._extract_topic_lazy(user_id))
        schedule(f"triples:{user_id}", lambda: _pipeline._extract_triples_lazy(user_id))


def _schedule_situation_update(user_id: str) -> None:
    """D9：后台调度局势档案增量更新（事件驱动 + 每 10 轮；LLM 受 D10 门控）。"""
    from .features import flag
    from .situation import update_after_turn
    from .tasks import schedule

    if not flag("situation_enabled"):
        return
    schedule(f"situation:{user_id}", lambda: update_after_turn(user_id))


async def run_turn_effects(
    user_id: str,
    text: str,
    *,
    turn_id: int,
    ephemeral: bool,
    nickname_pref: str | None = None,
    persona_name: str = "",
    prev_ts: str | None = None,
    draft_cb: Callable[[Any], Any] | None = None,
    messages: list[dict] | None = None,
) -> EffectLedger:
    """执行本轮全部确定性旁路副作用，返回台账。

    临时轮（``ephemeral``）不产生任何持久副作用，直接返回空台账——与旧行为一致。
    ``messages`` 传入时，聊天派活的系统提示会追加进去（必须在本轮 prompt 组装之前
    调用，否则提示会落在 LLM 看不到的位置）。
    """
    ledger = EffectLedger("turn_effects")
    if ephemeral:
        return ledger

    from .reunion import observe_user_turn

    ledger.run("重逢状态推进", observe_user_turn, user_id, turn_id, text)
    ledger.run("注意力推进", _run_attention, user_id, text, turn_id)
    ledger.run("开放问题登记", _run_open_question, user_id, text, turn_id)
    ledger.run("幽默记忆反馈", _run_humor_feedback, user_id, text, turn_id)
    await ledger.run_async(
        "活动草稿",
        _run_activity_draft,
        user_id,
        text,
        turn_id=turn_id,
        draft_cb=draft_cb,
    )
    ledger.run("求助回应", _run_companion_response, user_id, text, turn_id)
    ledger.run("会话节奏", _run_rhythm, user_id, text, turn_id)
    ledger.run("偏好教学", _run_preference_teaching, user_id, text, turn_id)
    ledger.run("学习候选", _run_learning_candidate, user_id, text, turn_id)
    await ledger.run_async(
        "聊天派活", _run_dispatch_agent, user_id, text, messages
    )
    ledger.run(
        "即时好感奖励",
        _run_instant_bonus,
        user_id,
        text,
        nickname_pref=nickname_pref,
        persona_name=persona_name,
    )
    ledger.run("惰性提炼调度", _schedule_lazy_extraction, user_id, prev_ts)
    ledger.run("局势档案调度", _schedule_situation_update, user_id)
    return ledger


__all__ = ["run_turn_effects"]
