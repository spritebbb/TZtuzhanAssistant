"""P0-04B/C2：搜索合成集、离线默认与无凭据状态。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import benchmark_search_providers as bench


def suite_fixture_has_required_coverage() -> None:
    rows = bench.load_queries()
    assert len(rows) >= 40
    categories = {row["category"] for row in rows}
    assert {"zh", "en", "fresh", "conflict"} <= categories
    assert len({row["id"] for row in rows}) == len(rows)


def suite_dry_run_never_calls_network(tmp_path, monkeypatch) -> None:
    async def forbidden(*args, **kwargs):
        raise AssertionError("dry-run 不应联网")

    monkeypatch.setattr(bench, "_request_provider", forbidden)
    output = tmp_path / "search.json"
    report = asyncio.run(bench.run(["--samples", "4", "--output", str(output)]))
    assert report["mode"] == "dry-run" and len(report["plan"]) == 8
    assert report["results"] == [] and output.exists()


def suite_live_without_keys_is_unavailable_not_fake_result(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    report = asyncio.run(bench.run([
        "--live", "--samples", "2", "--max-calls", "4", "--output", str(tmp_path / "missing.json")
    ]))
    assert len(report["unavailable"]) == 4
    assert report["results"] == []
    assert all("missing" in item["reason"] for item in report["unavailable"])


def suite_summary_reports_coverage_latency_and_domains() -> None:
    rows = [
        {"provider": "tavily", "status": "success", "latency_sec": 0.2, "domains": ["a.example", "b.example"]},
        {"provider": "tavily", "status": "failed", "latency_sec": 1.0},
        {"provider": "bocha", "status": "success", "latency_sec": 0.3, "domains": ["a.example"]},
    ]
    summary = bench.summarize(rows)
    assert summary["tavily"]["coverage"] == 0.5
    assert summary["tavily"]["latency_sec"] == [0.2]
    assert summary["tavily"]["domain_distribution"]["a.example"] == 1
