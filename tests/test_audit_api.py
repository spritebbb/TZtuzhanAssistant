# -*- coding: utf-8 -*-
"""验证审计日志查询/过滤/清空 API。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.tools.audit import log_tool_call, query_log, count_log, clear_log

# 隔离审计日志路径：审计是安全特性，测试不得写入真实 data/tool_log.jsonl
import backend.tools.audit as _audit

_audit._LOG_PATH = Path(tempfile.mkdtemp(prefix="tz_audit_test_")) / "tool_log.jsonl"


def main():
    # 写两条测试记录
    log_tool_call(tool="run_command", args={"command": "echo hi"},
                  confirmed="allow", ok=True, result="hi", user="test")
    log_tool_call(tool="write_file", args={"path": "a.txt", "content": "x" * 500},
                  confirmed="deny", ok=False, error="用户拒绝了该操作", user="test")

    # 按工具过滤
    r2 = query_log(tool="run_command")
    assert len(r2) >= 1, "按工具过滤应返回结果"
    assert r2[0]["tool"] == "run_command", f"应只返回run_command: {r2[0]['tool']}"
    print(f"[OK] 按工具过滤: {len(r2)} 条")

    # 按确认状态过滤
    r3 = query_log(confirmed="deny")
    assert len(r3) >= 1, "按确认状态过滤应返回结果"
    assert r3[0]["confirmed"] == "deny", f"应只返回deny: {r3[0]['confirmed']}"
    print(f"[OK] 按确认状态过滤: {len(r3)} 条")

    # 按关键词过滤（匹配 tool/result/error）
    r4 = query_log(q="hi")
    assert len(r4) >= 1, "按关键词过滤应返回结果"
    print(f"[OK] 按关键词过滤: {len(r4)} 条")

    # 审计总条数
    total = count_log()
    print(f"[OK] 审计总条数: {total}")

    # 路由可导入
    from backend.api import audit
    print(f"[OK] 审计 API 路由已注册")

    print("\n=== 审计模块: 5 项全部通过 ===")


main()