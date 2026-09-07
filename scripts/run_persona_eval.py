"""运行菟菚人格评测。

默认只校验评测集参考答案，不调用网络：
    python scripts/run_persona_eval.py

显式调用真实模型生成并用 LLM 裁判：
    python scripts/run_persona_eval.py --live
    python scripts/run_persona_eval.py --live --tag relationship --limit 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.evals.persona import (  # noqa: E402
    EvalResult,
    evaluate_deterministic,
    judge_with_llm,
    load_cases,
    merge_judgement,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="菟菚人格一致性评测")
    parser.add_argument("--live", action="store_true", help="调用真实 LLM 生成并判分")
    parser.add_argument("--tag", help="只运行指定 tag")
    parser.add_argument("--limit", type=int, default=0, help="最多运行多少个场景，0=全部")
    parser.add_argument("--output", type=Path, help="把完整结果写入 JSON")
    parser.add_argument("--candidate-file", type=Path, help="离线候选 JSON（case_id -> reply）")
    parser.add_argument("--compare-file", type=Path, help="第二组离线候选，按 seed 盲化为 A/B")
    parser.add_argument("--seed", type=int, default=20260907, help="盲评顺序随机种子")
    return parser.parse_args()


def _candidate_map(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("候选文件必须是 case_id -> reply 的 JSON 对象")
    return {str(key): str(value) for key, value in data.items()}


def blind_pair(case_id: str, first: str, second: str, seed: int) -> tuple[dict[str, str], dict[str, str]]:
    """稳定盲化两个候选；返回展示值与只写报告的来源映射。"""
    swapped = random.Random(f"{seed}:{case_id}").randrange(2) == 1
    values = {"A": second, "B": first} if swapped else {"A": first, "B": second}
    sources = {"A": "candidate_2", "B": "candidate_1"} if swapped else {"A": "candidate_1", "B": "candidate_2"}
    return values, sources


async def _live_reply(case, temp_dir: Path) -> str:
    # 必须先改 data_dir 再导入 userdb/pipeline，确保评测不碰真实用户数据。
    from backend.core.config import config

    config.data_dir = temp_dir
    from backend.core import affection
    from backend.core.pipeline import process
    from backend.core.userdb import db

    uid = f"persona-eval-{case.id}"
    db.ensure_user(uid)
    affection.set_affection(uid, case.affection)
    if case.address:
        db.set_nickname(uid, case.address)
    if not case.first_chat:
        db.set_first_chat_done(uid)
    return await process(uid, case.user, mock=False)


async def _run() -> int:
    args = _args()
    cases = load_cases()
    candidates = _candidate_map(args.candidate_file)
    comparison = _candidate_map(args.compare_file)
    if args.live and candidates:
        raise ValueError("--live 与离线 --candidate-file 不能同时使用")
    if args.live:
        from backend.core.model_routes import resolve_route

        candidate_route = resolve_route("chat_routine")
        judge_route = resolve_route("judge")
        if (candidate_route.base_url.rstrip("/"), candidate_route.model) == (
            judge_route.base_url.rstrip("/"), judge_route.model
        ):
            raise ValueError("live 人格评测要求 judge 与生成使用不同的实际 endpoint/model")
    if args.compare_file and not args.candidate_file:
        raise ValueError("--compare-file 必须和 --candidate-file 一起使用")
    if args.tag:
        cases = [case for case in cases if case.tag == args.tag]
    if args.limit > 0:
        cases = cases[: args.limit]
    if not cases:
        print("没有匹配的评测场景")
        return 2

    results: list[EvalResult] = []
    timings: dict[str, float] = {}
    blind: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="tuzhan-persona-eval-") as tmp:
        temp_dir = Path(tmp)
        for index, case in enumerate(cases, 1):
            started = time.perf_counter()
            reply = await _live_reply(case, temp_dir) if args.live else candidates.get(case.id, case.reference)
            result = evaluate_deterministic(case, reply)
            if args.live:
                judgement = await judge_with_llm(case, reply)
                result = merge_judgement(result, judgement)
            results.append(result)
            timings[case.id] = round(time.perf_counter() - started, 6)
            if comparison:
                pair, sources = blind_pair(case.id, reply, comparison.get(case.id, case.reference), args.seed)
                blind.append({
                    "case_id": case.id,
                    "candidates": pair,
                    "source_map": sources,
                    "deterministic": {
                        label: evaluate_deterministic(case, text).__dict__ for label, text in pair.items()
                    },
                })
            mark = "PASS" if result.passed else "FAIL"
            print(f"[{index:02d}/{len(cases):02d}] {mark} {case.id}  score={result.score:g}")
            if not result.passed:
                for violation in result.violations:
                    print(f"    - {violation}")
                print(f"    reply: {reply}")

    passed = sum(result.passed for result in results)
    summary = {
        "mode": "live" if args.live else "reference",
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(passed / len(results), 4),
        "hard_passed": sum(result.hard_passed for result in results),
        "subjective_passed": sum(result.subjective_passed is True for result in results) if args.live else None,
        "latency_seconds": timings,
        "usage": {"input_tokens": None, "output_tokens": None, "cost_cny": None},
        "missing_metrics": ["token_usage", "cost_cny"],
        "seed": args.seed,
        "routes": ({
            "candidate": {"base_url": resolve_route("chat_routine").base_url, "model": resolve_route("chat_routine").model},
            "judge": {"base_url": resolve_route("judge").base_url, "model": resolve_route("judge").model},
        } if args.live else None),
        "blind_comparison": blind,
        "results": [result.__dict__ for result in results],
    }
    print(f"\n人格评测：{passed}/{len(results)} 通过 ({summary['pass_rate']:.1%})")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告：{args.output}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
