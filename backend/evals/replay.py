"""Q1 脱敏 fixture 回放：只接受 JSONL，不保留凭据和直接身份标识。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .schema import EvalCase

_REDACTIONS = (
    (re.compile(r"(?i)\b(?:sk|sk-proj)-[A-Za-z0-9_-]{8,}\b"), "<API_KEY>"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~-]{8,}\b"), "Bearer <TOKEN>"),
    (re.compile(r"\b1[3-9]\d{9}\b"), "<PHONE>"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "<EMAIL>"),
)


def redact_text(value: str) -> str:
    result = value
    for pattern, replacement in _REDACTIONS:
        result = pattern.sub(replacement, result)
    return result


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact(item) for key, item in value.items()}
    return value


def load_replay(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: JSON 无效") from exc
        case = EvalCase.from_dict(_redact(raw))
        if case.case_id in seen:
            raise ValueError(f"重复 case_id: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    return cases
