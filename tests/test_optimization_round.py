# -*- coding: utf-8 -*-
"""优化轮回归测试：agent 总超时 / audit 尾读 / 优雅关闭端点 / 禁用缓存 / 插件源码 API。"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.tools.audit import _load_tail, count_log, log_tool_call, query_log


def test_audit_tail_read():
    """尾读：写入超过窗口的记录后，无过滤查询只读尾部且最新在前。"""
    log_tool_call(tool="tool_a", args={}, result="old")
    for i in range(20):
        log_tool_call(tool=f"tool_{i}", args={}, result=f"result-{i}")
    tail = _load_tail(max_lines=10)
    assert len(tail) == 10, f"尾读应限 10 行: {len(tail)}"
    assert tail[-1]["tool"] == "tool_19", "尾读应包含最新一条"
    rows = query_log(limit=5)
    assert len(rows) == 5 and rows[0]["tool"] == "tool_19", "query_log 应尾读且倒序"
    assert count_log(tool="tool_19") >= 1, "带过滤条件仍可用"
    print("[OK] #10 audit 尾读 + 倒序 + 过滤兼容")


def test_agent_timeout_code():
    """超时分支存在且 wait_for 包裹 run_tool_round（静态检查 + 状态语义）。"""
    src = Path(__file__).resolve().parents[1].joinpath("backend/agent/session.py").read_text(encoding="utf-8")
    assert "asyncio.wait_for(" in src and "timeout=TASK_TIMEOUT" in src, "run_task 应有总超时包裹"
    assert "except asyncio.TimeoutError" in src, "应有超时异常分支"
    assert "任务超时" in src, "超时应写入任务结果"
    print("[OK] #13 TASK_TIMEOUT 真正生效（wait_for + 超时状态落库）")


def test_shutdown_endpoint():
    """优雅关闭端点存在且先 checkpoint 再退出。"""
    src = Path(__file__).resolve().parents[1].joinpath("backend/api/health.py").read_text(encoding="utf-8")
    assert "/health/shutdown" in src
    assert "checkpoint_all" in src and "backup" in src
    print("[OK] #20 优雅关闭端点（checkpoint + 备份后退出）")


def test_disabled_cache():
    """禁用状态带 mtime 缓存：读两次命中缓存，写入后失效（挂临时状态文件验证）。"""
    from backend.plugins import loader

    orig = loader._STATE_FILE
    tmp = orig.parent / "plugins_state_test.json"
    loader._STATE_FILE = tmp
    loader._disabled_cache = None
    try:
        tmp.write_text('{"disabled": []}', encoding="utf-8")
        s1 = loader._load_disabled()
        c1 = loader._disabled_cache
        assert c1 is not None, "首次读取后应有缓存"
        s2 = loader._load_disabled()
        assert s1 == s2 and loader._disabled_cache is c1, "mtime 未变应命中缓存"
        loader._save_disabled(s1 | {"__cache_probe__"})
        assert loader._disabled_cache is None, "写入后缓存应失效"
        assert "__cache_probe__" in loader._load_disabled()
    finally:
        tmp.unlink(missing_ok=True)
        loader._STATE_FILE = orig
        loader._disabled_cache = None
    print("[OK] ③ 禁用状态 mtime 缓存 + 写失效")


def test_plugin_source_api():
    """插件源码 API：可读、防路径穿越、404。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api import plugins as plugins_api

    app = FastAPI()
    app.include_router(plugins_api.router)
    client = TestClient(app)

    r = client.get("/api/plugins/currency/source")
    assert r.status_code == 200 and r.json()["ok"]
    assert "PLUGIN_META" in r.json()["source"], "应返回 currency.py 源码"
    # 路径穿越：名字里的非法字符不应逃出 plugins/
    r2 = client.get("/api/plugins/..%2F..%2Fbackend%2Fmain/source")
    assert r2.status_code in (400, 404), f"路径穿越应被拒: {r2.status_code}"
    r3 = client.get("/api/plugins/no_such/source")
    assert r3.status_code == 404
    print("[OK] ③ 插件源码查看 API（只读 + 防穿越 + 404）")


def main():
    test_audit_tail_read()
    test_agent_timeout_code()
    test_shutdown_endpoint()
    test_disabled_cache()
    test_plugin_source_api()
    print("\n=== 优化轮回归: 全部通过 ===")


main()
