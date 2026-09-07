"""可复现的模型路由基准；默认只生成计划，--live 才调用网络。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.config import config  # noqa: E402
from backend.core.llm import _client_for_route  # noqa: E402
from backend.core.model_routes import resolve_route  # noqa: E402
from backend.evals.persona import load_cases  # noqa: E402
from backend.evals.persona import evaluate_deterministic  # noqa: E402

RUN_VERSION = 1


def _hash(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_plan(sample_count: int, tasks: list[str], seed: int) -> list[dict]:
    cases = load_cases()
    random.Random(seed).shuffle(cases)
    plan = []
    for case in cases[:sample_count]:
        case_data = {"id": case.id, "stage": case.stage, "affection": case.affection,
                     "first_chat": case.first_chat, "address": case.address,
                     "user": case.user, "intent": case.intent}
        for task in tasks:
            route = resolve_route(task)
            route_data = {
                "task": route.task, "base_url": route.base_url, "key_ref": route.key_ref,
                "model": route.model, "timeout_sec": route.timeout_sec, "max_tokens": route.max_tokens,
            }
            key = _hash({"case": case_data, "route": route_data, "seed": seed})
            plan.append({"checkpoint_key": key, "case": case_data, "route": route_data})
    return plan


def summarize_results(results: list[dict]) -> dict:
    cases = {case.id: case for case in load_cases()}
    by_task: dict[str, dict] = {}
    failures: list[dict] = []
    for row in results:
        task = row["route"]["task"]
        bucket = by_task.setdefault(task, {"attempted": 0, "successful": 0, "hard_passed": 0,
                                           "latency_sec": [], "estimated_cost_cny": 0.0})
        bucket["attempted"] += 1
        if row.get("status") == "success":
            bucket["successful"] += 1
            bucket["latency_sec"].append(row.get("latency_sec"))
            bucket["estimated_cost_cny"] += row.get("cost_cny") or 0.0
            case = cases.get(row["case"]["id"])
            if case:
                evaluated = evaluate_deterministic(case, row.get("reply", ""))
                row["hard_eval"] = {"passed": evaluated.passed, "violations": evaluated.violations,
                                    "signature_violations": evaluated.signature_violations}
                if evaluated.passed:
                    bucket["hard_passed"] += 1
                else:
                    failures.append({"checkpoint_key": row["checkpoint_key"], "case_id": case.id,
                                     "task": task, "violations": evaluated.violations})
        else:
            failures.append({"checkpoint_key": row["checkpoint_key"], "case_id": row["case"]["id"],
                             "task": task, "error": row.get("error", "failed")})
    for bucket in by_task.values():
        bucket["estimated_cost_cny"] = round(bucket["estimated_cost_cny"], 8)
    return {"by_task": by_task, "failure_cases": failures,
            "estimated_total_cost_cny": round(sum(row.get("cost_cny") or 0 for row in results), 8)}


async def _one(item: dict, *, retries: int, input_price: float, output_price: float) -> dict:
    route = resolve_route(item["route"]["task"])
    from backend.core.persona import build_system_prompt

    case = item["case"]
    prompt = [
        {"role": "system", "content": build_system_prompt(
            stage=case["stage"], address=case.get("address") or "", lover_confirm=case["stage"] == "恋人",
            first_chat=bool(case.get("first_chat")), affection=int(case["affection"]), user_id="persona-benchmark",
        )},
        {"role": "user", "content": case["user"]},
    ]
    started = time.perf_counter()
    last_error = ""
    for attempt in range(retries + 1):
        try:
            response = await _client_for_route(route).chat.completions.create(
                model=route.model, messages=prompt, max_tokens=route.max_tokens, temperature=0.2
            )
            text = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            pt = int(getattr(usage, "prompt_tokens", 0) or 0)
            ct = int(getattr(usage, "completion_tokens", 0) or 0)
            missing = [] if pt or ct else ["token_usage", "cost_cny"]
            cost = ((pt * input_price) + (ct * output_price)) / 1_000_000 if not missing else None
            return {
                **item, "status": "success", "attempt": attempt + 1, "reply": text,
                "latency_sec": round(time.perf_counter() - started, 4),
                "tokens": {"input": pt or None, "output": ct or None},
                "cost_cny": cost, "missing_metrics": missing,
            }
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
    return {**item, "status": "failed", "attempt": retries + 1,
            "latency_sec": round(time.perf_counter() - started, 4), "error": last_error,
            "tokens": {"input": None, "output": None}, "cost_cny": None,
            "missing_metrics": ["token_usage", "cost_cny"]}


def _args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--tasks", default="chat_routine,batch_other")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--max-calls", type=int, default=0)
    parser.add_argument("--max-cost", type=float, default=30.0)
    parser.add_argument("--reserve-cost-per-call", type=float, default=0.5)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260907)
    parser.add_argument("--output", type=Path, default=ROOT / "deliverables" / "model-benchmark.json")
    parser.add_argument("--input-price", type=float, default=config.llm_price_input_per_mtok)
    parser.add_argument("--output-price", type=float, default=config.llm_price_output_per_mtok)
    parser.add_argument("--price-source", default="configured estimate", help="价格估算来源说明")
    return parser.parse_args(argv)


async def run(argv=None) -> dict:
    args = _args(argv)
    if args.samples < 1 or args.concurrency not in (1, 2) or not 0 <= args.retries <= 2:
        raise ValueError("samples>=1、concurrency<=2、retries<=2")
    if args.max_cost <= 0 or args.max_cost > 30 or args.reserve_cost_per_call <= 0:
        raise ValueError("max-cost 必须在 (0, 30] 元，且每次预留费用必须为正")
    tasks = [part.strip() for part in args.tasks.split(",") if part.strip()]
    plan = build_plan(args.samples, tasks, args.seed)
    plan_keys = {item["checkpoint_key"] for item in plan}
    previous = {}
    if args.output.exists():
        try:
            previous = {r["checkpoint_key"]: r for r in json.loads(args.output.read_text(encoding="utf-8")).get("results", []) if r.get("status") == "success" and r.get("checkpoint_key") in plan_keys}
        except (OSError, ValueError, KeyError):
            previous = {}
    pending = [item for item in plan if item["checkpoint_key"] not in previous]
    report = {
        "mode": "live" if args.live else "dry-run", "seed": args.seed, "run_version": RUN_VERSION,
        "limits": {"max_calls": args.max_calls, "max_cost_cny": args.max_cost,
                   "concurrency": args.concurrency, "retries": args.retries},
        "pricing": {"input_cny_per_mtok": args.input_price,
                    "output_cny_per_mtok": args.output_price,
                    "source": args.price_source},
        "plan": plan, "results": list(previous.values()), "stopped_reason": None,
    }
    if args.live:
        if args.max_calls < 1:
            raise ValueError("live 必须显式设置 --max-calls")
        allowance = min(args.max_calls, int(args.max_cost / args.reserve_cost_per_call))
        selected = pending[:allowance]
        if len(selected) < len(pending):
            report["stopped_reason"] = "max_calls_or_reserved_budget"
        semaphore = asyncio.Semaphore(args.concurrency)

        async def guarded(item):
            async with semaphore:
                return await _one(item, retries=args.retries, input_price=args.input_price, output_price=args.output_price)

        for result in await asyncio.gather(*(guarded(item) for item in selected)):
            report["results"].append(result)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["summary"] = summarize_results(report["results"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    result = asyncio.run(run())
    print(f"{result['mode']}: {len(result['plan'])} planned, {len(result['results'])} completed -> {_args().output}")
