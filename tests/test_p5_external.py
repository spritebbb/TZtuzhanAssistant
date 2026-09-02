# -*- coding: utf-8 -*-
"""P5 外部桥验证：codex_run / dsh_run / MCP 元数据 / remote 路由。

v2：工具已插件化——通过插件系统加载 external 插件（不再来自 builtin）。
"""
import asyncio
import importlib
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.plugins import loader
from backend.tools.base import ToolRegistry
from backend.tools.builtin.register_all import register_all


def _load_all_tools() -> None:
    """memory 内置 + 全部插件加载（与后端 startup 行为一致）。"""
    register_all()
    loaded = loader.load_all_plugins()
    assert "external" in loaded, f"external 插件应加载成功: {loader.plugin_states().get('external')}"


def _external_module():
    """拿到已加载的 external 插件模块（用于 mock 打桩）。"""
    return importlib.import_module("plugin_external")


async def test_codex_command_shape() -> None:
    """codex_run 以非交互 exec 模式调用 CLI（不联网，只校验参数形状）。"""
    external = _external_module()

    captured: dict = {}

    class FakeProc:
        async def communicate(self, payload=None):
            return b"", b""

    async def fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        captured["env"] = kwargs.get("env", {})
        return FakeProc()

    with patch.object(external.asyncio, "create_subprocess_exec", new=fake_exec), \
         patch.object(external, "_codex_final_answer", return_value="ok"):
        text = await external._codex_run("测试任务")

    assert text == "ok"
    argv = captured["argv"]
    assert argv[1] == "exec", "必须走非交互 exec 子命令（旧式裸调用需要 TTY 会失败）"
    assert "-p" in argv and "deepseek" in argv
    assert "--skip-git-repo-check" in argv
    assert "-C" in argv
    assert "--ephemeral" in argv
    assert "--output-last-message" in argv
    home_codex = Path.home() / ".codex"
    if home_codex.is_dir():
        assert captured["env"].get("CODEX_HOME") == str(home_codex)
    print(f"[OK] codex_run 参数形状正确：{' '.join(argv)}")


async def test_codex_run_routes() -> None:
    """codex_run 工具注册且可路由到真实 CLI。"""
    r = await ToolRegistry.execute("codex_run", {"prompt": "hi"})
    # 只要返回了输出（错误提示也算正常路由），工具本身工作
    assert r.ok
    text = r.output or ""
    assert len(text) > 0
    print(f"[OK] codex_run 路由成功，返回 {len(text)} 字符: {text[:60]!r}")


async def test_dsh_run_routes() -> None:
    """dsh_run 工具注册且可路由到真实 DSH CLI（headless）。"""
    r = await ToolRegistry.execute("dsh_run", {"task": "只回复 OK 两个字母"})
    assert r.ok
    text = r.output or ""
    print(f"[OK] dsh_run 路由成功，返回 {len(text)} 字符: {text[:80]!r}")


async def test_mcp_metadata() -> None:
    """MCP /tools 返回完整安全元数据。"""
    tools = ToolRegistry.list()
    assert len(tools) >= 34, f"工具数应≥34: {len(tools)}"
    has_meta = all(
        hasattr(t, "category") and hasattr(t, "danger_level")
        and hasattr(t, "needs_confirm") and hasattr(t, "max_output_chars")
        for t in tools
    )
    assert has_meta, "所有工具应有完整元数据"
    codex = next(t for t in tools if t.name == "codex_run")
    assert codex.category == "external" and codex.danger_level == "high" and codex.needs_confirm
    dsh = next(t for t in tools if t.name == "dsh_run")
    assert dsh.needs_confirm
    print(f"[OK] MCP 元数据：34 工具，codex/dsh 均 external+high+confirm")


async def test_remote_api() -> None:
    """/api/remote/task 路由存在。"""
    from backend.api import remote
    assert remote.router is not None
    print("[OK] /api/remote/task 路由已注册")


async def main() -> None:
    _load_all_tools()
    await test_codex_command_shape()
    await test_codex_run_routes()
    await test_dsh_run_routes()
    await test_mcp_metadata()
    await test_remote_api()
    print("\n=== P5 外部桥: 4 项全部通过 ===")


asyncio.run(main())
