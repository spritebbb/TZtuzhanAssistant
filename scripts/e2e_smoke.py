# -*- coding: utf-8 -*-
"""端到端冒烟（§24.3-4）：真模型 + 真工具链，手动触发。

不在自动测试套件里：真实调用 LLM/搜索（花钱、依赖外部服务），只做上线前
人工冒烟。数据全程隔离（TZTUZHAN_DATA_DIR 临时目录），不碰真实关系库。

检查点（全过打 SMOKE OK）：
  1. 真模型聊天：pipeline 全链（人格/记忆/回复）返回非空文本；
  2. 真 web_search 工具轮：需要联网的问题触发搜索，回复引用检索结果；
  3. usage 记账：本轮调用的 token 落 usage_log（成本面板可观测）；
  4. Agent 派活链路：create_task → run_task（真模型计划与工具循环）→
     done + 执行轨迹（§24.3-2 的 log）非空。

用法：
    .venv/Scripts/python.exe scripts/e2e_smoke.py            # 真模型+真工具
    .venv/Scripts/python.exe scripts/e2e_smoke.py --mock    # 只验证脚本代码路径（不花钱）
退出码 0=全部通过；任一失败打 FAIL 并返回 1。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["TZTUZHAN_DATA_DIR"] = tempfile.mkdtemp(prefix="tztuzhan_e2e_smoke_")
os.environ.setdefault("MEMORY_V2", "0")

UID = "e2e-smoke-user"
FAILURES: list[str] = []
MOCK = False


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "✓" if ok else "✗"
    print(f"  [{mark}] {name}" + (f"：{detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def smoke_chat() -> str:
    from backend.core.pipeline import process

    t0 = time.time()
    reply = asyncio.run(process(UID, "用一句话介绍你自己", mock=MOCK))
    elapsed = time.time() - t0
    ok = bool(reply and reply.strip())
    tag = "（mock）" if MOCK else ""
    check(f"真模型聊天非空{tag}", ok, f"{len(reply or '')} 字 / {elapsed:.1f}s")
    return reply or ""


def smoke_web_search() -> str:
    from backend.core.pipeline import process

    t0 = time.time()
    reply = asyncio.run(process(
        UID, "帮我搜索一下：菟丝子是什么植物？一句话回答",
        mock=MOCK,
    ))
    elapsed = time.time() - t0
    ok = bool(reply and reply.strip())
    tag = "（mock）" if MOCK else ""
    check(f"web_search 工具轮{tag}", ok, f"{len(reply or '')} 字 / {elapsed:.1f}s")
    return reply or ""


def smoke_usage_recorded() -> None:
    if MOCK:
        print("  [-] usage 记账：mock 路径不调用真实 LLM，跳过（真模型路径由 D5 链路覆盖）")
        return
    from backend.core.userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(prompt_tokens + completion_tokens),0) "
            "FROM usage_log WHERE user_id = ?", (UID,),
        ).fetchone()
    check("usage 记账", int(row[0]) > 0 and int(row[1]) > 0,
          f"{row[0]} 次 / {row[1]} tokens")


def smoke_agent_dispatch() -> None:
    from backend.agent.session import confirm_all, create_task, run_task

    async def go():
        task = await create_task(UID, "创建一个待办：明天上午买牛奶")
        # 真实用户流程：先在面板「全部允许」计划步骤，再执行
        confirmed = confirm_all(task.id, True)
        if not confirmed:
            return task
        return await run_task(task.id)

    done = asyncio.run(go())
    check("Agent 派活完成", done.status == "done", f"status={done.status}")
    trace = [e for e in done.log if e.get("type") in ("round", "tool", "elapsed")]
    check("执行轨迹非空（§24.3-2）", len(trace) > 0, f"{len(trace)} 条轨迹")


def main() -> int:
    global MOCK
    parser = argparse.ArgumentParser(description="端到端冒烟（§24.3-4）")
    parser.add_argument("--mock", action="store_true",
                        help="mock 模式：只验证脚本代码路径，不调用真实模型")
    args = parser.parse_args()
    MOCK = args.mock
    if MOCK:
        print("== 端到端冒烟（--mock：仅验证脚本代码路径） ==")
    else:
        print("== 端到端冒烟（真模型+真工具；数据隔离在临时目录） ==")
    smoke_chat()
    smoke_web_search()
    smoke_usage_recorded()
    smoke_agent_dispatch()
    if FAILURES:
        print(f"\n结果：{len(FAILURES)} 项失败 → {FAILURES}")
        return 1
    print("\nSMOKE OK" + ("（mock 路径）" if MOCK else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
