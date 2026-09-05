# -*- coding: utf-8 -*-
"""第二梯队修复回归：AgentSession 计划步骤级确认 + 审计日志查询 API。"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agent import session
from backend.agent.session import AgentTask, TaskStep
from backend.tools.audit import log_tool_call, query_log, count_log, clear_log

# 隔离审计日志路径：审计是安全特性，测试不得清空/污染真实 data/tool_log.jsonl
import backend.tools.audit as _audit

_audit._LOG_PATH = Path(tempfile.mkdtemp(prefix="tz_audit_test_")) / "tool_log.jsonl"


def test_step_confirm():
    """计划步骤级确认：逐条确认 + 整体放行 + 持久化。"""
    t = AgentTask(id="t2", user_id="u", objective="test",
                  created_at=time.time(), updated_at=time.time())
    t.plan = [TaskStep(title="a"), TaskStep(title="b"), TaskStep(title="c")]
    for i in range(3):
        t.step_confirmations[str(i)] = "pending"
    session._save(t)

    # 逐条确认
    r = session.confirm_step("t2", 1, True)
    assert r.step_confirmations["1"] == "allowed"
    assert session.pending_steps(r) == [0, 2], f"pending应为[0,2]: {session.pending_steps(r)}"
    print("[OK] 逐条确认: 步骤1已允许，剩余待确认 [0,2]")

    # 拒绝某步
    r = session.confirm_step("t2", 0, False)
    assert r.step_confirmations["0"] == "denied"
    assert session.pending_steps(r) == [2]
    print("[OK] 拒绝步骤: 步骤0已拒绝")

    # 整体放行
    r = session.confirm_all("t2", True)
    assert session.pending_steps(r) == [], "整体放行后应无待确认"
    print("[OK] 整体放行: 全部允许")

    # 持久化（重新加载）
    r2 = session._load("t2")
    assert r2.step_confirmations["1"] == "allowed"
    assert r2.step_confirmations["0"] == "allowed"
    d = session.to_dict(r2)
    assert "step_confirmations" in d and "pending_steps" in d
    print("[OK] 持久化 + to_dict 含新字段")

    # 越界校验
    try:
        session.confirm_step("t2", 99, True)
        assert False, "越界应抛 ValueError"
    except ValueError:
        print("[OK] 越界步骤被拒绝")


def test_audit_query():
    """审计日志：过滤/计数/清空，并保留不可静默擦除的清理记录。"""
    clear_log()
    log_tool_call(tool="run_command", args={"command": "echo hi"},
                  confirmed="allow", ok=True, result="hi", user="test")
    log_tool_call(tool="write_file", args={"path": "a.txt"},
                  confirmed="deny", ok=False, error="用户拒绝", user="test")

    r2 = query_log(tool="run_command")
    assert len(r2) == 1 and r2[0]["tool"] == "run_command"
    assert count_log(tool="run_command") == 1
    print("[OK] 按工具过滤 + 计数")

    r3 = query_log(confirmed="deny")
    assert len(r3) == 1 and r3[0]["confirmed"] == "deny"
    print("[OK] 按确认状态过滤")

    r4 = query_log(ok=True)
    assert len(r4) == 2
    assert all(row["ok"] is True for row in r4)
    assert {row["tool"] for row in r4} == {"run_command", "__audit__"}
    print("[OK] 按成功状态过滤")

    assert count_log() == 3
    cleared = clear_log()
    assert cleared == 3
    remaining = query_log()
    assert len(remaining) == 1 and remaining[0]["tool"] == "__audit__"
    assert remaining[0]["args"] == {"action": "clear_log", "cleared": 3}
    print("[OK] 清空: 清除 3 条并保留清理审计")


def main():
    test_step_confirm()
    test_audit_query()
    print("\n=== 第二梯队回归: 全部通过 ===")


main()
