"""P0-04B：模型基准默认离线、预算门槛与断点键。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import benchmark_model_routes as bench


def suite_dry_run_never_calls_model(tmp_path, monkeypatch) -> None:
    called = False

    async def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("dry-run 不应调用模型")

    monkeypatch.setattr(bench, "_one", forbidden)
    output = tmp_path / "model.json"
    report = asyncio.run(bench.run(["--samples", "3", "--output", str(output)]))
    assert report["mode"] == "dry-run" and len(report["plan"]) == 6
    assert report["results"] == [] and not called and output.exists()


def suite_checkpoint_is_stable_and_route_sensitive(monkeypatch) -> None:
    monkeypatch.delenv("MODEL_ROUTE_CHAT_ROUTINE_MODEL", raising=False)
    first = bench.build_plan(2, ["chat_routine"], 7)
    second = bench.build_plan(2, ["chat_routine"], 7)
    assert [x["checkpoint_key"] for x in first] == [x["checkpoint_key"] for x in second]
    monkeypatch.setenv("MODEL_ROUTE_CHAT_ROUTINE_MODEL", "another-model")
    changed = bench.build_plan(2, ["chat_routine"], 7)
    assert [x["checkpoint_key"] for x in first] != [x["checkpoint_key"] for x in changed]


def suite_live_requires_explicit_call_limit_and_hard_caps(tmp_path) -> None:
    for argv in (
        ["--live", "--output", str(tmp_path / "a.json")],
        ["--live", "--max-calls", "1", "--max-cost", "31", "--output", str(tmp_path / "b.json")],
        ["--live", "--max-calls", "1", "--concurrency", "3", "--output", str(tmp_path / "c.json")],
        ["--live", "--max-calls", "1", "--retries", "3", "--output", str(tmp_path / "d.json")],
    ):
        try:
            asyncio.run(bench.run(argv))
        except ValueError:
            pass
        else:
            raise AssertionError(f"应拒绝参数：{argv}")


def suite_success_checkpoint_is_not_charged_twice(tmp_path, monkeypatch) -> None:
    output = tmp_path / "resume.json"
    calls = 0

    async def fake_one(item, **kwargs):
        nonlocal calls
        calls += 1
        return {**item, "status": "success", "cost_cny": 0.01}

    monkeypatch.setattr(bench, "_one", fake_one)
    argv = ["--live", "--samples", "1", "--tasks", "chat_routine", "--max-calls", "1", "--output", str(output)]
    asyncio.run(bench.run(argv))
    asyncio.run(bench.run(argv))
    assert calls == 1
