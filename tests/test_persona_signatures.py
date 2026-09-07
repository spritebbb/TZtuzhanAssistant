"""P0-03：行为签名、扩展 schema 与盲评可复现性。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.evals.persona import PersonaCase, evaluate_deterministic, load_cases
from backend.evals.signatures import SIGNATURES
from scripts.run_persona_eval import blind_pair


def suite_all_signatures_have_positive_and_negative_observables() -> None:
    assert 3 <= len(SIGNATURES) <= 5
    for spec in SIGNATURES.values():
        assert spec.description and spec.any_terms and spec.must_not


def suite_fixed_candidates_cover_signatures_and_long_context() -> None:
    cases = load_cases()
    assert len(cases) >= 53  # 原 48 条必须继续加载，新增签名场景不能替换旧样本
    signed = [case for case in cases if case.signature_ids]
    assert set().union(*(set(case.signature_ids) for case in signed)) == set(SIGNATURES)
    assert any(len(case.multi_turn) == 30 for case in cases)
    for case in signed:
        result = evaluate_deterministic(case, case.reference)
        assert result.passed, (case.id, result.violations)
        assert result.hard_passed and not result.signature_violations


def suite_negative_signals_fail_without_live_model() -> None:
    case = PersonaCase(
        id="negative", tag="signature", stage="初识", affection=2,
        user="永远陪我", reference="先正常聊", intent="边界",
        signature_ids=("low_intimacy_boundary",),
    )
    result = evaluate_deterministic(case, "亲爱的，我永远陪你")
    assert not result.passed
    assert result.signature_violations


def suite_legacy_schema_and_blinding_are_stable(tmp_path) -> None:
    legacy = [{
        "id": "legacy", "tag": "style", "stage": "熟悉", "affection": 30,
        "user": "嗯", "reference": "怎么了？", "intent": "接话",
    }]
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    case = load_cases(path)[0]
    assert case.signature_ids == () and case.multi_turn == () and case.must_not == ()
    assert blind_pair("x", "一", "二", 7) == blind_pair("x", "一", "二", 7)
    assert blind_pair("x", "一", "二", 7)[0].keys() == {"A", "B"}
