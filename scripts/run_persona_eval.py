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
import sys
import tempfile
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
    return parser.parse_args()


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
    if args.tag:
        cases = [case for case in cases if case.tag == args.tag]
    if args.limit > 0:
        cases = cases[: args.limit]
    if not cases:
        print("没有匹配的评测场景")
        return 2

    results: list[EvalResult] = []
    with tempfile.TemporaryDirectory(prefix="tuzhan-persona-eval-") as tmp:
        temp_dir = Path(tmp)
        for index, case in enumerate(cases, 1):
            reply = await _live_reply(case, temp_dir) if args.live else case.reference
            result = evaluate_deterministic(case, reply)
            if args.live:
                judgement = await judge_with_llm(case, reply)
                result = merge_judgement(result, judgement)
            results.append(result)
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

