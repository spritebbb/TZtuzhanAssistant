# -*- coding: utf-8 -*-
"""Q4 本地自身开销预算。固定数据与 provider，不测真实模型或网络延迟。"""
from __future__ import annotations

import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["TZTUZHAN_DATA_DIR"] = tempfile.mkdtemp(prefix="tztuzhan-latency-")
os.environ["MEMORY_V2"] = "0"
os.environ["MEMORY_MEM0"] = "0"


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[max(0, int(len(ordered) * 0.95) - 1)]


def test_prompt_compile_and_output_filter_p95() -> None:
    from backend.core import persona_slices
    from backend.core.output_hygiene import HygieneContext, inspect_reply

    view = persona_slices.build_state_view(
        stage="熟悉", affection=40, energy=70, trust=45, intimacy=38
    )
    ctx = HygieneContext(kind="chat", persona_id="default")
    reply = "这是固定 fake provider 的普通回复。" * 80
    samples: list[float] = []
    for _ in range(200):
        persona_slices._compile_cache.clear()
        started = time.perf_counter()
        compiled = persona_slices.compile_slices(view, profile_id="default")
        checked = inspect_reply(reply, context=ctx)
        samples.append((time.perf_counter() - started) * 1000)
        assert compiled is not None and checked.action == "accept"
    p95 = _p95(samples)
    assert p95 <= 100, f"人格编译+输出过滤 p95={p95:.2f}ms > 100ms"
    print(f"[PASS] 普通回复编译/过滤 p95={p95:.2f}ms（预算 100ms）")


def test_registry_1000_candidates_p95() -> None:
    from backend.core import context_registry as registry

    class SyntheticProvider(registry.ContextProvider):
        def __init__(self):
            self.entry = registry.ContextEntry(
                id="synthetic", namespace="test/synthetic",
                source_type="synthetic", priority=50,
            )
            self.rows = [
                registry.ContextCandidate(
                    entry_id="synthetic", source_id=str(i), source_version="1",
                    text=f"item-{i}", token_count=8,
                    source_namespace="test/synthetic", priority=float(i % 20),
                    relevance=float((1000 - i) % 100) / 100,
                )
                for i in range(1000)
            ]

        def collect(self, user_id, query, state, turn_id):
            return list(self.rows)

        def render(self, candidates):
            return "\n".join(item.text for item in candidates)

    previous = dict(registry._PROVIDERS)
    registry._PROVIDERS.clear()
    registry.register_provider(SyntheticProvider())
    samples: list[float] = []
    try:
        for turn in range(60):
            started = time.perf_counter()
            selected = registry.collect_context(
                "latency-user", "fixed query", turn_id=turn + 1, budget=1200
            )
            samples.append((time.perf_counter() - started) * 1000)
            assert 0 < len(selected.items) <= registry.MAX_DYNAMIC_ITEMS
    finally:
        registry._PROVIDERS.clear()
        registry._PROVIDERS.update(previous)
    p95 = _p95(samples)
    assert p95 <= 80, f"registry 1000 条检索 p95={p95:.2f}ms > 80ms"
    print(f"[PASS] registry 1000 条候选 p95={p95:.2f}ms（预算 80ms）")


def test_real_scale_query_plan_and_latency() -> None:
    from backend.core.userdb import db

    rows = [
        ("scale-a" if i % 2 else "scale-b", f"fixture-{i}", "2026-01-01T00:00:00")
        for i in range(20_000)
    ]
    with db._lock:
        db.conn.executemany(
            "INSERT INTO facts(user_id,content,ts) VALUES(?,?,?)", rows
        )
        db.conn.commit()
        plan = db.conn.execute(
            "EXPLAIN QUERY PLAN SELECT id,content FROM facts "
            "WHERE user_id=? ORDER BY id DESC LIMIT 50",
            ("scale-a",),
        ).fetchall()
    detail = " ".join(str(row[3]) for row in plan)
    assert "idx_facts_user" in detail, detail

    samples: list[float] = []
    for _ in range(100):
        started = time.perf_counter()
        with db._lock:
            result = db.conn.execute(
                "SELECT id,content FROM facts WHERE user_id=? ORDER BY id DESC LIMIT 50",
                ("scale-a",),
            ).fetchall()
        samples.append((time.perf_counter() - started) * 1000)
        assert len(result) == 50
    p95 = _p95(samples)
    assert p95 <= 80, f"2万行事实查询 p95={p95:.2f}ms > 80ms"
    print(f"[PASS] 2万行真实 SQLite 规模使用 idx_facts_user，p95={p95:.2f}ms")


def main() -> None:
    test_prompt_compile_and_output_filter_p95()
    test_registry_1000_candidates_p95()
    test_real_scale_query_plan_and_latency()
    print("\nQ4 latency budgets: 3/3 passed")


if __name__ == "__main__":
    main()
