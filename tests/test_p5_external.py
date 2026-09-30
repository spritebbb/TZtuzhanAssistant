# -*- coding: utf-8 -*-
"""P5 验证：MCP 元数据 / remote 路由。

v3（2026-09-30）：codex_run / dsh_run 外部 Agent 桥已按用户拍板删除
（external 插件整体移除），本文件只保留 MCP 元数据与 remote 路由验证。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.plugins import loader
from backend.tools.base import ToolRegistry
from backend.tools.builtin.register_all import register_all


def _load_all_tools() -> None:
    """memory 内置 + 全部插件加载（与后端 startup 行为一致）。"""
    register_all()
    loaded = loader.load_all_plugins()
    assert "subagent" in loaded, f"subagent 插件应加载成功: {loader.plugin_states().get('subagent')}"


async def test_mcp_metadata() -> None:
    """MCP /tools 返回完整安全元数据。"""
    tools = ToolRegistry.list()
    assert len(tools) >= 32, f"工具数应≥32: {len(tools)}"
    has_meta = all(
        hasattr(t, "category") and hasattr(t, "danger_level")
        and hasattr(t, "needs_confirm") and hasattr(t, "max_output_chars")
        for t in tools
    )
    assert has_meta, "所有工具应有完整元数据"
    # 外部类工具的代表换成 subagent 插件的 agent_run（external 桥已删）
    agent = next(t for t in tools if t.name == "agent_run")
    assert agent.category == "external" and agent.needs_confirm
    assert not any(t.name in ("codex_run", "dsh_run") for t in tools), \
        "外部桥工具应已从注册表消失"
    print(f"[OK] MCP 元数据：{len(tools)} 工具，agent_run 为 external+confirm，桥工具已移除")


async def test_remote_api() -> None:
    """/api/remote/task 路由存在。"""
    from backend.api import remote
    assert remote.router is not None
    print("[OK] /api/remote/task 路由已注册")


async def main() -> None:
    _load_all_tools()
    await test_mcp_metadata()
    await test_remote_api()
    print("\n=== P5（MCP 元数据/remote）: 2 项全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
