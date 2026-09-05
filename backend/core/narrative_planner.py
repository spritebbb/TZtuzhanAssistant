# -*- coding: utf-8 -*-
"""M5 Narrative Planner（轻量版）：决定哪条心事"现在可以表达"。

职责边界：
- 只做门控与择优（阶段边界 / 优先级 / 到点 / 尝试余量），不生成文案——
  表达文案由主动性链路用她的人格卡现场展开。
- 表达永远经 initiative.enqueue_proactive（额度/冷却/勿扰/队列制不变）。
- 每日最多一条心事主动表达，绝不连续盘问用户。
"""
from __future__ import annotations

from . import pending_thoughts
from .log import logger

_DAILY_LIMIT = 1


def plan_next(user_id: str, stage: str) -> dict | None:
    """返回本轮最值得表达的心事（或 None = 保持沉默）。"""
    return pending_thoughts.next_thought_for_stage(user_id, stage)


def build_express_prompt(thought: dict) -> str:
    """把心事翻成一句给 LLM 的叙事指令（只含素材，不含可执行指令）。"""
    return (
        f"你心里一直惦记一件小事：{thought['content']}。"
        "现在时机合适，主动自然地提一句——像想起来随口一问，不像提醒事项、"
        "不像系统播报；一句到两句就够。如果对方可能不想聊，也要给台阶，"
        "别追问、别盘问。符合当前人格卡的性格与说话方式，别加括号动作。"
    )
