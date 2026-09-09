# -*- coding: utf-8 -*-
"""Q1 评测框架的离线 smoke、脱敏、盲评与发布门槛。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.evals.blind_review import build_blind_review, flag_review_disagreements
from backend.evals.replay import load_replay, redact_text
from backend.evals.runner import load_quality_cases, reference_smoke, release_gate, run_offline
from backend.evals.schema import CASE_GROUPS, CASE_VARIANTS, CandidateOutput, fixture_hash


def test_fixture_matrix_and_reference_smoke() -> None:
    cases = load_quality_cases()
    assert len(cases) == 27
    assert {(c.group, c.variant) for c in cases} == {
        (group, variant) for group in CASE_GROUPS for variant in CASE_VARIANTS
    }
    assert fixture_hash(cases) == fixture_hash(list(reversed(cases)))
    report = reference_smoke()
    assert report["aggregate"]["hard_passed"] == 27, report["results"]
    assert report["seed"] == 20260908
    assert "llm_api_key" not in report["config"]


def test_release_gate_blocks_hard_and_soft_regressions() -> None:
    cases = load_quality_cases()
    baseline_outputs = {
        case.case_id: CandidateOutput(case.reference_reply, 4.5, "baseline blind score")
        for case in cases
    }
    baseline = run_offline(cases, baseline_outputs, snapshot={"llm_model": "baseline"})

    hard_outputs = dict(baseline_outputs)
    hard_outputs[cases[0].case_id] = CandidateOutput("作为一个AI，我知道了。", 4.5, "candidate")
    hard_gate = release_gate(baseline, run_offline(cases, hard_outputs, snapshot={}))
    assert hard_gate["passed"] is False
    assert any("硬 invariant 回退" in reason for reason in hard_gate["reasons"])

    soft_outputs = {
        case.case_id: CandidateOutput(case.reference_reply, 4.0, "candidate blind score")
        for case in cases
    }
    soft_gate = release_gate(baseline, run_offline(cases, soft_outputs, snapshot={}))
    assert soft_gate["passed"] is False
    assert any("5%" in reason for reason in soft_gate["reasons"])


def test_replay_redacts_identifiers() -> None:
    text = "联系 13800138000 或 user@example.com，Bearer abcdefghijk，sk-proj-abcdefghijk"
    redacted = redact_text(text)
    assert "13800138000" not in redacted and "user@example.com" not in redacted
    assert "abcdefghijk" not in redacted

    case = load_quality_cases()[0]
    raw = {
        "case_id": "redacted_case", "group": "signature", "variant": "normal",
        "input": text, "reference_reply": case.reference_reply,
        "expected_invariants": ["global_redlines"], "scorer_version": "q1-v1",
    }
    with tempfile.TemporaryDirectory(prefix="q1-replay-") as tmp:
        path = Path(tmp) / "replay.jsonl"
        path.write_text(json.dumps(raw, ensure_ascii=False) + "\n", encoding="utf-8")
        loaded = load_replay(path)
    assert loaded[0].input.count("<") >= 4


def test_blind_review_is_stable_and_hides_names() -> None:
    cases = load_quality_cases()[:5]
    one = {case.case_id: "候选一" for case in cases}
    two = {case.case_id: "候选二" for case in cases}
    items, reveal = build_blind_review(cases, one, two)
    again, again_reveal = build_blind_review(cases, one, two)
    assert items == again and reveal == again_reveal
    assert len(items) == len(reveal) == 5
    assert all("candidate_one" not in json.dumps(item) for item in items)
    assert all(set(mapping) == {"A", "B"} for mapping in reveal.values())
    blind_id = items[0]["blind_id"]
    reviews = [
        {"blind_id": blind_id, "response": "A", "score": 5, "evidence": "自然且克制"},
        {"blind_id": blind_id, "response": "A", "score": 3, "evidence": "略显模板化"},
        {"blind_id": blind_id, "response": "B", "score": 4, "evidence": "表达清楚"},
        {"blind_id": blind_id, "response": "B", "score": 3, "evidence": "还算自然"},
    ]
    disagreements = flag_review_disagreements(reviews)
    assert len(disagreements) == 1 and disagreements[0]["response"] == "A"


def main() -> int:
    test_fixture_matrix_and_reference_smoke()
    test_release_gate_blocks_hard_and_soft_regressions()
    test_replay_redacts_identifiers()
    test_blind_review_is_stable_and_hides_names()
    print("[OK] Q1：27 个 fixture、固定快照、脱敏回放、A/B 盲评与发布门槛全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
