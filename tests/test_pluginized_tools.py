# -*- coding: utf-8 -*-
"""工具插件化冒烟测试：全部工具经插件系统装载，owner/状态正确。

v2 迁移后：memory 保留内置（记忆系统），其余 11 个工具模块均在 plugins/ 目录，
由插件系统加载（与后端 startup 行为一致）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.plugins import loader
from backend.tools.base import ToolRegistry

# 插件名 → 该插件应注册的工具
EXPECTED = {
    "web_search": {"web_search"},
    "web_fetch": {"web_fetch"},
    "file_ops": {"read_file", "write_file", "list_dir"},
    "file_search": {"glob", "grep"},
    "file_edit": {"edit"},
    "todo": {"todo_create", "todo_list", "todo_get", "todo_update", "todo_complete", "todo_delete"},
    "subagent": {"agent_run", "agent_fanout"},
    "skill": {"skill_search", "skill_load"},
    "code_exec": {"run_python", "run_command"},
    "system": {"system_info", "list_process", "kill_process", "list_window", "activate_window",
               "open_app", "screenshot", "clipboard_get", "clipboard_set", "browser_open"},
    "external": {"codex_run", "dsh_run"},
    "currency": {"currency_convert"},
}


async def main():
    from tests._helpers import load_all_tools

    loaded = load_all_tools()
    missing_plugins = set(EXPECTED) - set(loaded)
    assert not missing_plugins, f"插件未加载成功: {missing_plugins}\n状态: {loader.plugin_states()}"
    print(f"[OK] 全部 {len(loaded)} 个插件加载成功")

    registry = {t.name: t for t in ToolRegistry.list_tools()}
    # 1) 每个插件的工具都在，且 owner 正确
    for pname, tools in EXPECTED.items():
        miss = tools - set(registry)
        assert not miss, f"插件 {pname} 缺少工具: {miss}"
        bad_owner = {t for t in tools if registry[t].owner != pname}
        assert not bad_owner, f"插件 {pname} 工具 owner 不正确: {bad_owner}"
    print(f"[OK] {sum(len(v) for v in EXPECTED.values())} 个插件工具全部注册且 owner 正确")

    # 2) 记忆系统保留内置
    mem_tools = {n: t for n, t in registry.items() if n.startswith("memory_")}
    assert mem_tools, "记忆工具应保留内置"
    assert all(t.owner == "builtin" for t in mem_tools.values()), f"记忆工具应为 builtin: {mem_tools}"
    print(f"[OK] 记忆工具保留内置: {sorted(mem_tools)}")

    # 3) 插件状态带元信息
    states = loader.plugin_states()
    assert states["file_ops"]["display_name"] == "文件操作"
    assert states["system"]["version"] == "1.0.0"
    assert states["currency"]["loaded"] and not states["currency"]["disabled"]
    print("[OK] 插件元信息/状态正确（管理面板可直接展示）")

    # 4) 工具数与迁移前一致（34 个：memory 内置 + 插件工具）
    total = len(registry)
    assert total >= 34, f"工具总数应≥34: {total}"
    print(f"[OK] 工具总数 {total}（迁移前 34）")

    print("\n=== 工具插件化冒烟: 全部通过 ===")


import asyncio

asyncio.run(main())
