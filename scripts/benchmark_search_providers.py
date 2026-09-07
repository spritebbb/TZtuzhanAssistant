"""Bocha/Tavily 合成搜索基准；默认 dry-run，无密钥时明确 unavailable。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "backend" / "evals" / "fixtures" / "search_queries.jsonl"
PROVIDERS = {
    "tavily": {"key_ref": "TAVILY_API_KEY", "url": "https://api.tavily.com/search"},
    "bocha": {"key_ref": "BOCHA_API_KEY", "url": "https://api.bochaai.com/v1/web-search"},
}


def load_queries(path: Path = FIXTURE) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) < 40:
        raise ValueError("搜索合成查询集必须至少 40 条")
    required = {"zh", "en", "fresh", "conflict"}
    if not required <= {str(row.get("category")) for row in rows}:
        raise ValueError("查询集必须覆盖中文、英文、时效、冲突四类")
    return rows


def _checkpoint(query: dict, provider: str, seed: int) -> str:
    value = {"query": query, "provider": {**PROVIDERS[provider], "key_ref": PROVIDERS[provider]["key_ref"]}, "seed": seed}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def build_plan(samples: int, providers: list[str], seed: int) -> list[dict]:
    queries = load_queries()
    random.Random(seed).shuffle(queries)
    return [
        {"checkpoint_key": _checkpoint(query, provider, seed), "query": query,
         "provider": provider, "endpoint": PROVIDERS[provider]["url"],
         "key_ref": PROVIDERS[provider]["key_ref"]}
        for query in queries[:samples] for provider in providers
    ]


def _extract_results(provider: str, payload: dict) -> list[dict]:
    if provider == "tavily":
        rows = payload.get("results") or []
    else:
        rows = ((payload.get("data") or {}).get("webPages") or {}).get("value") or []
    results = []
    for row in rows:
        url = str(row.get("url") or "")
        if url:
            results.append({"url": url, "title": str(row.get("title") or row.get("name") or "")})
    return results


async def _request_provider(item: dict, key: str, retries: int) -> dict:
    provider = item["provider"]
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {"query": item["query"]["query"], "max_results": 8, "search_depth": "basic"} if provider == "tavily" else {
        "query": item["query"]["query"], "summary": True, "count": 8,
    }
    started = time.perf_counter()
    error = ""
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=True) as client:
                response = await client.post(item["endpoint"], headers=headers, json=body)
                response.raise_for_status()
                rows = _extract_results(provider, response.json())
                domains = sorted({urlparse(row["url"]).netloc.lower() for row in rows if urlparse(row["url"]).netloc})
                return {**item, "status": "success", "attempt": attempt + 1,
                        "latency_sec": round(time.perf_counter() - started, 4),
                        "result_count": len(rows), "domains": domains, "results": rows,
                        "cost_cny": None, "missing_metrics": ["cost_cny"]}
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:160]}"
    return {**item, "status": "failed", "attempt": retries + 1,
            "latency_sec": round(time.perf_counter() - started, 4), "error": error,
            "cost_cny": None, "missing_metrics": ["cost_cny"]}


def summarize(results: list[dict]) -> dict:
    summary = {}
    for provider in PROVIDERS:
        rows = [row for row in results if row["provider"] == provider]
        successes = [row for row in rows if row["status"] == "success"]
        domains = Counter(domain for row in successes for domain in row.get("domains", []))
        summary[provider] = {
            "attempted": len(rows), "successful": len(successes),
            "coverage": round(len(successes) / len(rows), 4) if rows else None,
            "latency_sec": [row["latency_sec"] for row in successes],
            "domain_distribution": dict(domains.most_common()),
        }
    return summary


def _args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--providers", default="tavily,bocha")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--max-calls", type=int, default=0)
    parser.add_argument("--max-cost", type=float, default=30.0)
    parser.add_argument("--reserve-cost-per-call", type=float, default=0.5)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260907)
    parser.add_argument("--output", type=Path, default=ROOT / "deliverables" / "search-benchmark.json")
    return parser.parse_args(argv)


async def run(argv=None) -> dict:
    args = _args(argv)
    if args.samples < 1 or args.concurrency not in (1, 2) or not 0 <= args.retries <= 2:
        raise ValueError("samples>=1、concurrency<=2、retries<=2")
    if args.max_cost <= 0 or args.max_cost > 30 or args.reserve_cost_per_call <= 0:
        raise ValueError("max-cost 必须在 (0,30]，且每次预留费用必须为正")
    providers = [value.strip() for value in args.providers.split(",") if value.strip()]
    if any(provider not in PROVIDERS for provider in providers):
        raise ValueError("provider 仅支持 tavily,bocha")
    plan = build_plan(args.samples, providers, args.seed)
    previous = {}
    if args.output.exists():
        try:
            previous = {row["checkpoint_key"]: row for row in json.loads(args.output.read_text(encoding="utf-8")).get("results", []) if row.get("status") == "success"}
        except (OSError, ValueError, KeyError):
            previous = {}
    report = {"mode": "live" if args.live else "dry-run", "seed": args.seed, "plan": plan,
              "results": list(previous.values()), "unavailable": [], "summary": {},
              "limits": {"max_calls": args.max_calls, "max_cost_cny": args.max_cost,
                         "concurrency": args.concurrency, "retries": args.retries}}
    if args.live:
        if args.max_calls < 1:
            raise ValueError("live 必须显式设置 --max-calls")
        available = []
        for item in plan:
            if item["checkpoint_key"] in previous:
                continue
            key = os.getenv(item["key_ref"], "").strip()
            if not key:
                report["unavailable"].append({"checkpoint_key": item["checkpoint_key"], "provider": item["provider"], "reason": f"missing {item['key_ref']}"})
            else:
                available.append((item, key))
        allowance = min(args.max_calls, int(args.max_cost / args.reserve_cost_per_call))
        semaphore = asyncio.Semaphore(args.concurrency)

        async def guarded(item, key):
            async with semaphore:
                return await _request_provider(item, key, args.retries)

        report["results"].extend(await asyncio.gather(*(guarded(*pair) for pair in available[:allowance])))
    report["summary"] = summarize(report["results"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    result = asyncio.run(run())
    print(f"{result['mode']}: {len(result['plan'])} planned, {len(result['results'])} completed")
