# -*- coding: utf-8 -*-
"""Agent 任务：聊天派活识别、产物落盘、与待办的衔接。

验收锚点：
- 只认「分几步/拆成几步/用任务代理…」等封闭句式，普通聊天不派活；
- 任务完成后报告落盘到工作区，结果里附路径；
- 任务状态机不受落盘失败影响（落盘失败只记日志）。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_agent_"))

from backend.agent import session as ag


def test_detect_dispatch_request() -> int:
    positives = {
        "帮我分几步查一下三个竞品的定价": "三个竞品的定价",
        "把这次搬家拆成几步来安排": "这次搬家",
        "用任务代理整理这周的会议纪要": "这周的会议纪要",
        "派个任务：跟进三家供应商的报价": "跟进三家供应商的报价",
    }
    for text, expect in positives.items():
        got = ag.detect_dispatch_request(text)
        assert got == expect, (text, got, expect)
    for text in ("今天天气不错", "帮我查一下天气", "你觉得这个方案怎么样", ""):
        assert ag.detect_dispatch_request(text) is None, text
    print("[OK] 派活识别：4 正例命中、4 反例不误判")
    return 0


def test_report_written_on_success() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="tztuzhan_reports_"))
    old_dir = ag._REPORT_DIR
    ag._REPORT_DIR = tmp

    async def fake_round(messages, **kwargs):
        return "三家竞品定价分别是 99/129/199 元，结论：中位定价最有竞争力。"

    old_round = ag.run_tool_round
    ag.run_tool_round = fake_round
    try:
        task = asyncio.run(ag.create_task("agent-report-user", "分几步查三个竞品定价"))
        for i in range(len(task.plan)):
            ag.confirm_step(task.id, i, True)
        done = asyncio.run(ag.run_task(task.id))
        assert done.status == "done", done.result
        assert done.artifact_path and Path(done.artifact_path).exists(), done.artifact_path
        content = Path(done.artifact_path).read_text(encoding="utf-8")
        assert "## 计划" in content and "## 结果" in content
        assert "中位定价" in content
        assert "完整报告已存到工作区" in done.result
        # 落盘目录可自定义（测试用临时目录），文件名含任务 id
        assert task.id in Path(done.artifact_path).name
    finally:
        ag.run_tool_round = old_round
        ag._REPORT_DIR = old_dir
    print("[OK] 任务完成 → 报告落盘 + 结果附路径")
    return 0


def test_report_failure_does_not_break_task() -> int:
    """落盘失败时任务仍应 done（落盘是加分项）。"""
    class Boom:
        def mkdir(self, *a, **k):
            raise OSError("disk full")

    old_dir = ag._REPORT_DIR
    ag._REPORT_DIR = Boom()  # type: ignore[assignment]

    async def fake_round(messages, **kwargs):
        return "完成了。"

    old_round = ag.run_tool_round
    ag.run_tool_round = fake_round
    try:
        task = asyncio.run(ag.create_task("agent-report-fail", "分几步整理资料"))
        for i in range(len(task.plan)):
            ag.confirm_step(task.id, i, True)
        done = asyncio.run(ag.run_task(task.id))
        assert done.status == "done", done.status
        assert done.artifact_path == ""
    finally:
        ag.run_tool_round = old_round
        ag._REPORT_DIR = old_dir
    print("[OK] 落盘失败不影响任务状态")
    return 0


def main() -> int:
    failed = (
        test_detect_dispatch_request()
        + test_report_written_on_success()
        + test_report_failure_does_not_break_task()
    )
    if failed:
        print(f"\n=== Agent 派活与产物：{failed} 项失败 ===")
        return 1
    print("\n=== Agent 派活与产物：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
