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

from backend.core import features as _features  # noqa: E402
# D12：本组断言「投递成功」，主动意愿骰子按分钟播种会引入波动；
# 意愿层由 test_willingness_roll 专测，这里关掉保持契约确定性。
_features.set_flag("willingness_enabled", False)


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


def test_schedule_and_retry() -> int:
    """定时：到点才进 due；重试：失败后可重跑、受上限约束。"""
    import time as _time

    tmp = Path(tempfile.mkdtemp(prefix="tztuzhan_sched_reports_"))
    old_dir = ag._REPORT_DIR
    ag._REPORT_DIR = tmp
    uid = "agent-sched-user"
    task = asyncio.run(ag.create_task(uid, "分几步整理周报"))
    # 未定时 → 不在 due 里
    assert task.id not in ag.due_tasks()
    # 排到 10 分钟后 → 不在 due；排到过去 → 在 due
    ag.schedule_task(task.id, _time.time() + 600)
    assert task.id not in ag.due_tasks()
    ag.schedule_task(task.id, _time.time() - 1)
    assert task.id in ag.due_tasks()
    # 定时 = 明确授权 → 步骤自动放行
    reloaded = ag._load(task.id)
    assert all(v == "allowed" for v in reloaded.step_confirmations.values())

    # 重试：造一个 failed 任务
    async def boom(messages, **kwargs):
        raise RuntimeError("模拟失败")

    async def ok(messages, **kwargs):
        return "第二次成功了。"

    old_round = ag.run_tool_round
    try:
        ag.run_tool_round = boom
        ag.confirm_all(task.id, True)
        failed = asyncio.run(ag.run_task(task.id))
        assert failed.status == "failed"
        # 上限内的重试
        ag.run_tool_round = ok
        retried = asyncio.run(ag.retry_task(task.id))
        assert retried.status == "done" and retried.attempt == 1, retried.status
        assert any(item.get("type") == "retry" for item in retried.log)
    finally:
        ag.run_tool_round = old_round
        ag._REPORT_DIR = old_dir
    print("[OK] 定时到点判定 + 重试（含 attempt 记录）")
    return 0


def test_unified_start_context_claim_and_recovery() -> int:
    """所有后台入口共享用户身份、原子抢占、完成事件与崩溃恢复契约。"""
    import contextlib
    import time as _time

    from backend.api import agent as api_agent
    from backend.core.current_user import current_user_id

    tmp = Path(tempfile.mkdtemp(prefix="tztuzhan_unified_reports_"))
    old_dir = ag._REPORT_DIR
    old_chat = ag.chat
    old_round = ag.run_tool_round
    ag._REPORT_DIR = tmp

    async def fake_plan(messages, **kwargs):
        return '[{"title":"执行","detail":"检查身份"}]'

    async def scenario() -> None:
        task = await ag.create_task("persona-unified-run", "检查后台任务身份")
        ag.confirm_all(task.id, True)
        started = asyncio.Event()
        release = asyncio.Event()
        seen_users: list[str] = []

        async def blocked_round(messages, **kwargs):
            seen_users.append(current_user_id.get())
            started.set()
            await release.wait()
            return "完成"

        ag.run_tool_round = blocked_round
        token = current_user_id.set("caller-persona")
        try:
            first = api_agent._start_agent_task(task.id)
            second = api_agent._start_agent_task(task.id)
            assert first is not None and second is None, (first, second)
            await asyncio.wait_for(started.wait(), timeout=2)
            queue = api_agent._channel(task.id)
            assert queue.empty(), "真实任务完成前不能出现伪 task_done"
            assert api_agent._agent_bg_by_id.get(task.id) is first
            release.set()
            await asyncio.wait_for(first, timeout=2)
            assert seen_users == ["persona-unified-run"], seen_users
            assert current_user_id.get() == "caller-persona"
            event = queue.get_nowait()
            assert event == {"type": "task_done", "task_id": task.id}, event
            assert task.id not in api_agent._agent_bg_by_id
        finally:
            current_user_id.reset(token)
            cleanup = api_agent._channel_cleanup_by_id.pop(task.id, None)
            if cleanup is not None:
                cleanup.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cleanup
            api_agent._task_channels.pop(task.id, None)

        stale = await ag.create_task("persona-stale-run", "恢复中断任务")
        ag.confirm_all(stale.id, True)
        assert ag._claim_running(stale.id)
        moment = _time.time()
        stale_row = ag._load(stale.id)
        assert stale_row is not None
        stale_row.updated_at = moment - ag.TASK_TIMEOUT - 1
        ag._save(stale_row)
        assert ag.recover_stale_tasks(now=moment, stale_after=ag.TASK_TIMEOUT) == 1
        recovered = ag._load(stale.id)
        assert recovered is not None and recovered.status == "failed"
        assert any(item.get("type") == "interrupted" for item in recovered.log)

    ag.chat = fake_plan
    try:
        asyncio.run(scenario())
    finally:
        ag.chat = old_chat
        ag.run_tool_round = old_round
        ag._REPORT_DIR = old_dir
    print("[OK] 统一启动器：任务身份 / 原子抢占 / 完成事件 / 中断恢复")
    return 0


def test_prepare_then_remind() -> int:
    """主动 Agent：需要准备的约定先备料再汇报；不需要的交给普通跟进；备料失败降级。"""
    import datetime as _dt

    from backend.core import initiative as ini
    from backend.core.userdb import db, save_promise

    uid = "agent-prepare-user"
    db.ensure_user(uid)
    save_promise(uid, "帮我查一下三家竞品的定价", follow_up=_dt.date.today().isoformat())

    prepared_calls: list[str] = []

    async def fake_prepare(user_id, promise):
        prepared_calls.append(str(promise.get("content")))
        return "三家定价 99/129/199。"

    sent: list[str] = []

    async def fake_enqueue(user_id, text, image=None, epoch=None):
        sent.append(text)
        return True

    old_prepare, old_enqueue = ini._prepare_material, ini.enqueue_proactive
    ini._prepare_material = fake_prepare
    ini.enqueue_proactive = fake_enqueue
    ini._prepare_attempted_today.clear()
    try:
        text = asyncio.run(ini.maybe_prepare_then_remind(uid))
        assert text and "99/129/199" in text and "先替你把功课做了" in text, text
        assert prepared_calls == ["帮我查一下三家竞品的定价"], prepared_calls

        # 备料失败 → 降级为普通提醒（不编内容）
        ini._prepare_attempted_today.clear()
        db.ensure_user("agent-prepare-fail")
        save_promise("agent-prepare-fail", "帮我整理一下上次的资料",
                     follow_up=_dt.date.today().isoformat())

        async def none_prepare(user_id, promise):
            return ""

        async def fake_followup(user_id, promise):
            return "说好的资料整理呢？"

        ini._prepare_material = none_prepare
        old_followup = ini._generate_promise_followup
        ini._generate_promise_followup = fake_followup
        try:
            text2 = asyncio.run(ini.maybe_prepare_then_remind("agent-prepare-fail"))
            assert text2 == "说好的资料整理呢？", text2
        finally:
            ini._generate_promise_followup = old_followup

        # 不需要准备的约定 → 不占用这条链路
        db.ensure_user("agent-prepare-plain")
        save_promise("agent-prepare-plain", "一起吃个饭", follow_up=_dt.date.today().isoformat())
        ini._prepare_attempted_today.clear()
        assert asyncio.run(ini.maybe_prepare_then_remind("agent-prepare-plain")) is None
    finally:
        ini._prepare_material, ini.enqueue_proactive = old_prepare, old_enqueue
    print("[OK] 先做事再汇报：备料→汇报 / 失败降级 / 无关约定不触发")
    return 0


def main() -> int:
    failed = (
        test_detect_dispatch_request()
        + test_report_written_on_success()
        + test_report_failure_does_not_break_task()
        + test_schedule_and_retry()
        + test_unified_start_context_claim_and_recovery()
        + test_prepare_then_remind()
    )
    if failed:
        print(f"\n=== Agent 派活与产物：{failed} 项失败 ===")
        return 1
    print("\n=== Agent 派活与产物：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
