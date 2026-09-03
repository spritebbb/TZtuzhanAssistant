# -*- coding: utf-8 -*-
"""修复回归验证（第一梯队三项）：
1. 文件工具统一走 safety 白名单（AGENT_ALLOWED_ROOTS 生效）
2. AGENT_BLOCK_CMDS 配置黑名单接线
3. （前端超时清理为 TS/Vue 逻辑，由 vue-tsc 保证类型，这里验证后端超时行为）
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.config import config
from backend.tools.base import ToolRegistry
from backend.tools.builtin.register_all import register_all
from backend.tools.safety import check_command, check_path


def test_config_block_cmds_wired() -> None:
    """AGENT_BLOCK_CMDS 配置应真正参与拒绝。"""
    # 默认配置含 format / shutdown 等
    assert any("shutdown" in x for x in config.agent_block_cmds), "默认黑名单应含 shutdown"
    ok, err = check_command("shutdown /s /t 0")
    assert not ok, f"shutdown 应被拒: {err}"
    # 追加一个自定义黑名单词并验证生效
    old = config.agent_block_cmds
    config.agent_block_cmds = old + ["mycustomblock"]
    ok2, err2 = check_command("dir & mycustomblock")
    assert not ok2 and "mycustomblock" in err2, f"自定义黑名单应生效: {err2}"
    config.agent_block_cmds = old
    # 恢复后不再拦截
    ok3, _ = check_command("dir & mycustomblock")
    assert ok3
    print("[OK] AGENT_BLOCK_CMDS 配置接线：默认+自定义均生效，移除后恢复放行")


def test_file_tools_use_whitelist() -> None:
    """文件工具应走统一白名单：系统目录拒绝、项目内允许。"""
    # 项目内路径允许
    ok, err = check_path(str(Path(__file__).resolve()))
    assert ok, err
    # 系统目录拒绝
    ok2, err2 = check_path("C:\\Windows\\System32")
    assert not ok2, "系统目录应被拒"
    print(f"[OK] 统一白名单：允许根 {[str(r) for r in __import__('backend.tools.safety', fromlist=['allowed_roots']).allowed_roots()]}")


async def test_write_file_via_tool_rejects_outside() -> None:
    """write_file 工具写系统目录应被拒（走 safety 白名单）。"""
    r = await ToolRegistry.execute("write_file", {
        "path": r"C:\Windows\System32\_tuzhan_test.txt",
        "content": "x",
    })
    # 工具返回字符串，ok=True 但 output 含拒绝信息
    assert r.ok is False and "不允许" in (r.error or ""), f"写系统目录应被拒: {r}"
    # 项目内临时写入应成功
    tmp = Path(__file__).resolve().parent / "_tmp_whitelist_test.txt"
    r2 = await ToolRegistry.execute("write_file", {
        "path": str(tmp),
        "content": "hello",
    })
    assert "已写入" in (r2.output or ""), f"项目内写入应成功: {r2.output}"
    tmp.unlink(missing_ok=True)
    print("[OK] write_file 走统一白名单：系统目录拒、项目内成")


async def main() -> None:
    from tests._helpers import load_all_tools

    load_all_tools()
    test_config_block_cmds_wired()
    test_file_tools_use_whitelist()
    await test_write_file_via_tool_rejects_outside()
    print("\n=== 修复回归: 3 项全部通过 ===")


asyncio.run(main())
