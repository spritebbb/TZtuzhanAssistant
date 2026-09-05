# -*- coding: utf-8 -*-
"""MEMORY_V2 验收门槛：完整性、质量、性能和资源任一退化都应失败。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMORY_V2", "0")

from scripts.validate_memory_v2 import evaluate  # noqa: E402


def _report() -> dict:
    return {
        "index": {"facts": {"missing": 0}, "long_memory": {"missing": 0}},
        "runtime": {
            "mode": "model:BAAI/bge-m3",
            "warmup_seconds": 16.0,
            "rss_delta_mb": 670.0,
        },
        "historical_self_recall": {
            "lm": {"vector_rate": 1.0, "fused_rate": 1.0, "lexical_rate": 1.0, "vector_avg_ms": 70.0},
            "facts": {"vector_rate": 1.0, "fused_rate": 1.0, "lexical_rate": 1.0, "vector_avg_ms": 55.0},
        },
        "semantic_cases": {"vector_rate": 0.833, "lexical_rate": 0.0},
    }


def main() -> None:
    report = _report()
    assert evaluate(report, model_required=True)["passed"] is True

    report = _report()
    report["index"]["facts"]["missing"] = 1
    assert evaluate(report, model_required=True)["checks"]["index_complete"] is False

    report = _report()
    report["runtime"]["rss_delta_mb"] = 1300.0
    assert evaluate(report, model_required=True)["checks"]["memory"] is False

    report = _report()
    report["historical_self_recall"]["lm"]["fused_rate"] = 0.9
    assert evaluate(report, model_required=True)["checks"]["historical_not_worse"] is False

    report = _report()
    report["semantic_cases"]["vector_rate"] = 0.5
    assert evaluate(report, model_required=True)["checks"]["semantic_gain"] is False
    print("[OK] MEMORY_V2 验收门槛可重复判定退化")


if __name__ == "__main__":
    main()
