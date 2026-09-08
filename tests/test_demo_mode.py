# -*- coding: utf-8 -*-
"""演示模式（AGENT_DEMO_MODE）：确认摩擦降为零，硬底线不动。

验收锚点：
- 关闭时行为完全不变（无通道 + 写类 → deny）；
- 开启时写/命令/外部类自动放行（无前端通道也放行）；
- 硬底线仍生效：危险命令黑名单照拒、路径白名单照拒、critical 工具照拒；
- 只读工具在两种模式下都放行。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_demo_"))

from backend.core.config import config
from backend.tools import confirm as confirm_mod
from backend.tools.base import DANGER_CRITICAL, FunctionTool, ToolRegistry
from backend.tools.safety import check_command, check_path


class _Spec:
    def __init__(self, category: str, danger: str = "normal") -> None:
        self.category = category
        self.danger_level = danger


def _run_hook(category: str):
    return asyncio.run(
        confirm_mod.default_confirm_hook("demo_tool", {"x": 1}, _Spec(category), {})
    )


def test_off_by_default_unchanged() -> int:
    config.agent_demo_mode = False
    config.agent_confirm_enabled = True
    config.agent_confirm_no_channel = "deny"
    confirm_mod.current_sse_push.set(None)
    assert _run_hook("write") == "deny"
    assert _run_hook("run") == "deny"
    assert _run_hook("read") == "allow"
    print("[OK] 关闭时行为不变（写/命令拒绝、只读放行）")
    return 0


def test_on_allows_but_keeps_hard_floor() -> int:
    config.agent_demo_mode = True
    confirm_mod.current_sse_push.set(None)
    assert _run_hook("write") == "allow"
    assert _run_hook("run") == "allow"
    assert _run_hook("external") == "allow"
    # 硬底线一：危险命令黑名单仍拒绝
    ok, msg = check_command("rm -rf /tmp/x")
    assert ok is False and "拒绝" in msg, (ok, msg)
    ok, msg = check_command("shutdown /s")
    assert ok is False
    # 正常命令不受影响
    assert check_command("git status")[0] is True
    # 硬底线二：路径白名单仍拒绝项目外路径
    outside = "C:/Windows/System32/drivers/etc/hosts"
    ok, _ = check_path(outside)
    assert ok is False, outside
    ok, _ = check_path(str(ROOT / "backend" / "main.py"))
    assert ok is True
    # 硬底线三：critical 工具在钩子之前就被拒绝（base.py 第一步）
    called = {"hook": False}

    async def _hook(*a, **k):
        called["hook"] = True
        return "allow"

    async def _impl(**kwargs):
        return "should not run"

    tool = FunctionTool(name="demo_critical", description="x", func=_impl,
                        danger_level=DANGER_CRITICAL)
    ToolRegistry.register(tool)
    ToolRegistry.set_confirm_hook(_hook)
    try:
        result = asyncio.run(ToolRegistry.execute("demo_critical", {}))
        assert result.ok is False and result.confirmed == "blocked"
        assert called["hook"] is False, "critical 工具不应走到确认钩子"
    finally:
        ToolRegistry.unregister("demo_critical")
        ToolRegistry.set_confirm_hook(None)
    print("[OK] 演示模式放行，但命令黑名单/路径白名单/critical 三道硬底线不变")
    return 0


def main() -> int:
    failed = test_off_by_default_unchanged() + test_on_allows_but_keeps_hard_floor()
    config.agent_demo_mode = False  # 复位，避免影响同进程后续用例
    if failed:
        print(f"\n=== 演示模式：{failed} 项失败 ===")
        return 1
    print("\n=== 演示模式：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
