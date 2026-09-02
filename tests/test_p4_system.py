# -*- coding: utf-8 -*-
"""P4 本机操控验证：安全黑名单、沙箱、工具执行、白名单路径。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.tools.base import ToolRegistry
from backend.tools.builtin.register_all import register_all
from backend.tools.safety import check_command, check_path, allowed_roots


async def test_safety_blacklist() -> None:
    """危险命令黑名单：直接拒绝，不弹确认。"""
    cases = [
        "format c:",
        "shutdown /s",
        "rd /s /q C:\\Windows",
        "rm -rf /",
        "taskkill /f /im explorer.exe",
        "powershell -enc ABC",
        "dir"  # 正常命令放行
    ]
    for cmd in cases:
        ok, err = check_command(cmd)
        if cmd == "dir":
            assert ok, f"dir 应放行: {err}"
        else:
            assert not ok, f"{cmd} 应被拒绝"
    print("[OK] 危险命令黑名单：6 条危险命令被拒，dir 放行")


async def test_python_sandbox() -> None:
    """run_python 沙箱：拒绝危险模块。"""
    r = await ToolRegistry.execute("run_python", {"code": "import os\nprint('x')"})
    assert r.ok is False or "不允许" in (r.error or r.output), f"应拒绝 os 模块: {r}"
    # 正常代码
    r2 = await ToolRegistry.execute("run_python", {"code": "print(1+1)"})
    assert "2" in r2.output, f"应输出 2: {r2.output}"
    print("[OK] Python 沙箱：拒绝 os，正常执行 1+1=2")


async def test_system_info() -> None:
    r = await ToolRegistry.execute("system_info", {})
    assert "系统" in r.output
    print("[OK] system_info 返回:", r.output.splitlines()[0])


async def test_screenshot() -> None:
    r = await ToolRegistry.execute("screenshot", {})
    assert r.ok, f"截图失败: {r.error}"
    assert r.output.startswith("screenshots/"), r.output
    print("[OK] 截图保存:", r.output)


async def test_path_whitelist() -> None:
    roots = allowed_roots()
    print("   允许根目录:", [str(r) for r in roots])
    # 项目内路径应允许
    ok, err = check_path(str(Path(__file__).resolve()))
    assert ok, f"项目内路径应允许: {err}"
    # 系统目录应拒绝
    ok2, err2 = check_path("C:\\Windows\\System32")
    assert not ok2, "系统目录应被拒绝"
    print("[OK] 路径白名单：项目内允许，系统目录拒绝")


async def main() -> None:
    from tests._helpers import load_all_tools

    load_all_tools()
    await test_safety_blacklist()
    await test_python_sandbox()
    await test_system_info()
    await test_screenshot()
    await test_path_whitelist()
    print("\n=== P4 本机操控: 5 项全部通过 ===")


asyncio.run(main())
