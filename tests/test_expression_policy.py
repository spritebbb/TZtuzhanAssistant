# -*- coding: utf-8 -*-
"""§17.1 表达必要性（necessity 门）。

验收锚点（docs/Zcode技术指导.md §17.1 + 总纲批次 12）：
- necessity 公式版本化、各项 0..1、总分 clamp；
- 门控只作用于主动候选（≥0.6 进 arbiter）；用户消息永不门控；
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_171_"))

from backend.core import expression_policy as ep
from backend.core.userdb import db, kv_get


def test_necessity_formula_and_gate() -> int:
    # 公式版本化：强候选（高相关+新+高价值）达标
    strong = ep.score_necessity(relevance=0.8, novelty=0.7, relationship_value=0.6,
                                repetition=0.0, interruption_cost=0.1)
    assert strong["rule_version"] == ep.RULE_VERSION
    assert strong["necessity"] >= ep.NECESSITY_THRESHOLD, strong
    # 弱候选（重复+打断成本高）不达标
    weak = ep.score_necessity(relevance=0.3, novelty=0.2, relationship_value=0.3,
                              repetition=0.6, interruption_cost=0.4)
    assert weak["necessity"] < ep.NECESSITY_THRESHOLD, weak
    assert ep.gate_proactive_candidate(strong) is True
    assert ep.gate_proactive_candidate(weak) is False
    # 输入越界被 clamp
    over = ep.score_necessity(relevance=2.0, novelty=-1.0, relationship_value=0.5)
    assert 0.0 <= over["necessity"] <= 1.0
    print("[OK] necessity 公式 / 门控 / clamp")
    return 0


def test_necessity_for_source_and_repeat() -> int:
    uid = "171-necessity"
    db.ensure_user(uid)
    db.set_affection_absolute(uid, 60)
    first = ep.necessity_for_source(uid, "initiative:promise_followup", idle_minutes=120)
    assert ep.gate_proactive_candidate(first) is True, first
    # 24h 内同源重复 → 重复惩罚把分数压到线下
    ep.note_source_delivered(uid, "initiative:promise_followup")
    repeat = ep.necessity_for_source(uid, "initiative:promise_followup", idle_minutes=120)
    assert ep.gate_proactive_candidate(repeat) is False, repeat
    # 低关系新用户不拦高优先来源（不因关系浅就完全闭嘴）
    uid2 = "171-necessity-low"
    db.ensure_user(uid2)
    low = ep.necessity_for_source(uid2, "initiative:promise_followup", idle_minutes=120)
    assert ep.gate_proactive_candidate(low) is True, low
    # P3-05 主动权重调低 → 同条件分数下降（调制而非硬开关）
    from backend.core.persona_evolution import evolve

    base = ep.necessity_for_source(uid2, "initiative:outing_note", idle_minutes=120)
    evolve(uid2, "initiative_template_weight", -0.5, reason="test")
    lowered = ep.necessity_for_source(uid2, "initiative:outing_note", idle_minutes=120)
    assert lowered["necessity"] < base["necessity"], (base, lowered)
    print("[OK] 来源默认评分 / 重复惩罚 / 低关系不误杀 / 主动权重调制")
    return 0


def main() -> int:
    failed = (
        test_necessity_formula_and_gate()
        + test_necessity_for_source_and_repeat()
    )
    if failed:
        print(f"\n=== §17.1：{failed} 项失败 ===")
        return 1
    print("\n=== §17.1 表达必要性与注意力：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
