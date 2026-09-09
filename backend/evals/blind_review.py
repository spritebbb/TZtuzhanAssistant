"""Q1 A/B 盲评包：隐藏系统名并用固定种子打乱左右顺序。"""
from __future__ import annotations

import hashlib
import random
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .schema import EvalCase


@dataclass(frozen=True)
class BlindItem:
    case_id: str
    input: str
    history: tuple[dict[str, str], ...]
    state: dict
    response_a: str
    response_b: str


def build_blind_review(
    cases: Sequence[EvalCase],
    candidate_one: Mapping[str, str],
    candidate_two: Mapping[str, str],
    *,
    seed: int = 20260908,
) -> tuple[list[dict], dict[str, dict[str, str]]]:
    rng = random.Random(seed)
    items: list[dict] = []
    reveal: dict[str, dict[str, str]] = {}
    for case in sorted(cases, key=lambda item: item.case_id):
        one = candidate_one[case.case_id]
        two = candidate_two[case.case_id]
        swapped = bool(rng.getrandbits(1))
        left, right = (two, one) if swapped else (one, two)
        blind_id = hashlib.sha256(f"{seed}:{case.case_id}".encode()).hexdigest()[:12]
        item = BlindItem(case.case_id, case.input, case.history, case.state, left, right)
        payload = asdict(item)
        payload["blind_id"] = blind_id
        items.append(payload)
        reveal[blind_id] = {
            "A": "candidate_two" if swapped else "candidate_one",
            "B": "candidate_one" if swapped else "candidate_two",
        }
    return items, reveal


def flag_review_disagreements(reviews: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """找出同一匿名回复评分差大于 1 的项目，交由人工复核。"""
    grouped: dict[tuple[str, str], list[tuple[float, str]]] = {}
    for review in reviews:
        blind_id = str(review.get("blind_id", "")).strip()
        response = str(review.get("response", "")).strip().upper()
        evidence = str(review.get("evidence", "")).strip()
        try:
            score = float(review.get("score"))
        except (TypeError, ValueError) as exc:
            raise ValueError("盲评分数必须是 1 到 5") from exc
        if not blind_id or response not in {"A", "B"} or not evidence or not 1 <= score <= 5:
            raise ValueError("盲评记录需要 blind_id、A/B、1-5 分和证据")
        grouped.setdefault((blind_id, response), []).append((score, evidence))
    return [
        {
            "blind_id": key[0],
            "response": key[1],
            "score_gap": max(score for score, _ in values) - min(score for score, _ in values),
            "reviews": [{"score": score, "evidence": evidence} for score, evidence in values],
        }
        for key, values in sorted(grouped.items())
        if len(values) >= 2 and max(score for score, _ in values) - min(score for score, _ in values) > 1
    ]
