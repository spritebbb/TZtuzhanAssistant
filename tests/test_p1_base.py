# -*- coding: utf-8 -*-
"""P1 基础改动验证：工具注册/类别/压缩/确认钩子。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.tools.base import ToolRegistry, compress_text
from tests._helpers import load_all_tools


async def main() -> None:
    load_all_tools()
    tools = ToolRegistry.list()
    print("tools:", len(tools))
    rc = ToolRegistry.get("run_command")
    print("run_command category/danger/confirm:", rc.category, rc.danger_level, rc.needs_confirm)
    print("compress:", compress_text("a" * 100, 20))

    # 确认钩子：deny 场景
    async def deny_hook(name, args, spec, ctx):
        return "deny"

    ToolRegistry.set_confirm_hook(deny_hook)
    r = await ToolRegistry.execute("run_command", {"command": "echo hi"})
    print("deny -> ok:", r.ok, "confirmed:", r.confirmed, "error:", r.error)

    # 确认钩子：allow 场景
    async def allow_hook(name, args, spec, ctx):
        return "allow"

    ToolRegistry.set_confirm_hook(allow_hook)
    r = await ToolRegistry.execute("run_command", {"command": "echo hi"})
    print("allow -> ok:", r.ok, "confirmed:", r.confirmed, "output:", r.output.strip())

    ToolRegistry.set_confirm_hook(None)


asyncio.run(main())
