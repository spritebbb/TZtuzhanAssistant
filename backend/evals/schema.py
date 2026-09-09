"""Q1 横向评测的稳定数据契约。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

SCORER_VERSION = "q1-v1"
CASE_VARIANTS = ("normal", "boundary", "counterexample")
CASE_GROUPS = (
    "signature", "boundary", "dual_gate", "emotion", "defense",
    "refusal", "reunion", "uncertainty", "tool_failure",
)


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    group: str
    variant: str
    input: str
    history: tuple[dict[str, str], ...] = ()
    state: dict[str, Any] = field(default_factory=dict)
    expected_invariants: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    reference_reply: str = ""
    scorer_version: str = SCORER_VERSION

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EvalCase":
        case = cls(
            case_id=str(raw["case_id"]),
            group=str(raw["group"]),
            variant=str(raw["variant"]),
            input=str(raw["input"]),
            history=tuple(dict(turn) for turn in raw.get("history", ())),
            state=dict(raw.get("state") or {}),
            expected_invariants=tuple(map(str, raw.get("expected_invariants", ()))),
            forbidden=tuple(map(str, raw.get("forbidden", ()))),
            reference_reply=str(raw.get("reference_reply", "")),
            scorer_version=str(raw.get("scorer_version", SCORER_VERSION)),
        )
        case.validate()
        return case

    def validate(self) -> None:
        if not self.case_id or not self.input or not self.reference_reply:
            raise ValueError("case_id/input/reference_reply 不得为空")
        if self.group not in CASE_GROUPS:
            raise ValueError(f"{self.case_id}: 未知 group {self.group}")
        if self.variant not in CASE_VARIANTS:
            raise ValueError(f"{self.case_id}: 未知 variant {self.variant}")
        if self.scorer_version != SCORER_VERSION:
            raise ValueError(f"{self.case_id}: scorer_version 不兼容")
        for turn in self.history:
            if set(turn) != {"role", "content"} or turn["role"] not in {"user", "assistant"}:
                raise ValueError(f"{self.case_id}: history 格式错误")


@dataclass(frozen=True)
class CandidateOutput:
    reply: str
    naturalness: float = 4.0
    evidence: str = "offline reference"

    def validate(self) -> None:
        if not 1 <= float(self.naturalness) <= 5:
            raise ValueError("naturalness 必须在 1 到 5 之间")
        if not self.evidence.strip():
            raise ValueError("自然度评分必须附证据")


def fixture_hash(cases: Iterable[EvalCase]) -> str:
    """对排序后的完整 fixture 做稳定哈希，避免基线和候选错集比较。"""
    serializable = [asdict(case) for case in sorted(cases, key=lambda item: item.case_id)]
    payload = json.dumps(serializable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
