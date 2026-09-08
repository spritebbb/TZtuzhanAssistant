# -*- coding: utf-8 -*-
"""§17.1 表达必要性与注意力漂移。

验收锚点（docs/Zcode技术指导.md §17.1 + 总纲批次 12）：
- necessity 公式版本化、各项 0..1、总分 clamp；
- 门控只作用于主动候选（≥0.6 进 arbiter）；用户消息永不门控；
- 注意力 ≤5 主题、当前 +0.35 / 其余 ×0.7、<0.1 删除；
- 显式转题置顶；候选只登记不劫持；来源删除即移除；
- 临时轮内存态（不落 kv）。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_171_"))

from backend.core import attention_state as att
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


def test_attention_bump_decay_and_cap() -> int:
    uid = "171-att"
    db.ensure_user(uid)
    att.advance_turn(uid, "topic_a", turn_id=1)
    att.advance_turn(uid, "topic_b", turn_id=2)
    att.advance_turn(uid, "topic_c", turn_id=3)
    snap = {s["topic_id"]: s["weight"] for s in att.snapshot(uid)}
    # 当前主题 +0.35，其余 ×0.7
    assert snap["topic_c"] > 0.3, snap
    assert snap["topic_a"] < snap["topic_b"] < snap["topic_c"], snap
    # 长期不出现 → 衰减到 <0.1 自动删除
    for turn in range(4, 12):
        att.advance_turn(uid, "topic_c", turn_id=turn)
    snap = {s["topic_id"]: s["weight"] for s in att.snapshot(uid)}
    assert "topic_a" not in snap and "topic_b" not in snap, snap
    # 上限 5 个主题
    for i in range(8):
        att.switch_topic(uid, f"topic_{i}", turn_id=20 + i)
    assert len(att.snapshot(uid)) <= att.MAX_TOPICS
    print("[OK] 衰减 / 删除 / ≤5 主题")
    return 0


def test_switch_observe_drop_and_ephemeral() -> int:
    uid = "171-att2"
    db.ensure_user(uid)
    att.advance_turn(uid, "reading", turn_id=1)
    # 显式转题立即置顶
    att.switch_topic(uid, "trip_plan", turn_id=2, source_version="v1")
    snap = {s["topic_id"]: s["weight"] for s in att.snapshot(uid)}
    assert snap["trip_plan"] > snap["reading"], snap
    # 候选只登记不劫持：已有 reading 权重不变
    before = snap["reading"]
    att.observe_topic(uid, "new_candidate", source_version="v1")
    after = {s["topic_id"]: s["weight"] for s in att.snapshot(uid)}
    assert after.get("new_candidate") == 0.05 and after["reading"] == before
    att.observe_topic(uid, "reading")  # 已在注意力内：不修改
    after2 = {s["topic_id"]: s["weight"] for s in att.snapshot(uid)}
    assert after2["reading"] == before
    # 来源删除即移除
    att.drop_topic(uid, "trip_plan")
    assert "trip_plan" not in {s["topic_id"] for s in att.snapshot(uid)}
    # 临时轮：内存态，不落 kv
    att.advance_turn(uid, "ephemeral_topic", turn_id=99, ephemeral=True)
    raw = kv_get(uid, "attention:topics")
    assert "ephemeral_topic" not in raw, raw
    print("[OK] 置顶 / 候选不劫持 / 来源删除 / 临时轮不落盘")
    return 0


def test_kv_registry_covers_attention() -> int:
    from backend.core.kv_registry import match_spec

    spec = match_spec("attention:topics")
    assert spec is not None and spec.module == "attention_state"
    # 运行后无未登记键
    uid = "171-att3"
    db.ensure_user(uid)
    att.advance_turn(uid, "t", turn_id=1)
    from backend.core.relationship_export import unregistered_kv_keys

    assert "attention:topics" not in unregistered_kv_keys(uid)
    print("[OK] kv 登记覆盖")
    return 0


def main() -> int:
    failed = (
        test_necessity_formula_and_gate()
        + test_attention_bump_decay_and_cap()
        + test_switch_observe_drop_and_ephemeral()
        + test_kv_registry_covers_attention()
    )
    if failed:
        print(f"\n=== §17.1：{failed} 项失败 ===")
        return 1
    print("\n=== §17.1 表达必要性与注意力：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
