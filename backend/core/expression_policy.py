# -*- coding: utf-8 -*-
"""§17.1 表达必要性：主动候选的必要性评分门（0.6），用户消息永不门控。

契约（docs/Zcode技术指导.md §17.1 + 总纲批次 12）：

- necessity = relevance + novelty + relationship_value - repetition
  - interruption_cost，各项 0..1、规则版本化（RULE_VERSION）；总分 clamp 0..1；
- 评分只用于**主动候选**和可省略的装饰性句子；用户发起的一对一消息永远
  有答复，绝不能被评分门控为沉默；
- 主动候选默认 necessity ≥ 0.6 才进入既有 arbiter（initiative）；
  阈值是离线校准的工程默认，不在线自调；
- 输入由调用方给确定性信号（来源优先级/近期相似度/关系值），本模块不做
  LLM 调用、不读聊天正文。
"""
from __future__ import annotations

RULE_VERSION = 1
NECESSITY_THRESHOLD = 0.6  # 离线校准默认；不在线自调


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def score_necessity(*, relevance: float, novelty: float,
                    relationship_value: float, repetition: float = 0.0,
                    interruption_cost: float = 0.0) -> dict:
    """按版本化公式给一条候选打分。输入应已被调用方 clamp 到 0..1。"""
    n = (
        _clamp01(relevance)
        + _clamp01(novelty)
        + _clamp01(relationship_value)
        - _clamp01(repetition)
        - _clamp01(interruption_cost)
    )
    return {
        "necessity": _clamp01(n),
        "rule_version": RULE_VERSION,
    }


def gate_proactive_candidate(score: dict, *, threshold: float = NECESSITY_THRESHOLD) -> bool:
    """主动候选门：necessity 达标才进 arbiter；阈值离线校准，不在线自调。"""
    return float(score["necessity"]) >= float(threshold)
