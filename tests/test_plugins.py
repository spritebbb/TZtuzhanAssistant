# -*- coding: utf-8 -*-
"""插件系统 v2 回归测试：快照恢复（CODE-REVIEW #15）/ 元信息 / 管理 API / 网关路由 /
钩子 / 定时任务清理 / 禁用持久化。

独立可运行脚本风格：python tests/test_plugins.py；也由 test_suite_runner.py 接入 pytest。
不依赖真实 LLM/网络。
"""
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.plugins import loader
from backend.plugins import context as plugctx
from backend.tools.base import FunctionTool, ToolRegistry


async def _async_part(root: Path):
    """需要事件循环的部分（定时任务注册）。"""
    # 准备一个会注册定时任务 + 路由 + 钩子的插件
    (root / "plug_a.py").write_text(
        "PLUGIN_META = {'name': '插件A', 'version': '1.2.3', 'description': '测试A', 'author': 't'}\n"
        "from backend.tools.base import ToolRegistry\n"
        "def register(ctx):\n"
        "    ToolRegistry.register_func(name='test_tool_a', description='A工具',\n"
        "        func=lambda **kw: 'a', is_async=False,\n"
        "        input_schema={'type': 'object', 'properties': {}})\n"
        "    ToolRegistry.register_func(name='test_overridden_tool', description='A覆盖版',\n"
        "        func=lambda **kw: 'a-override', is_async=False,\n"
        "        input_schema={'type': 'object', 'properties': {}})\n"
        "    ctx.schedule(3600, lambda: None, name='a-task')\n"
        "    ctx.on_system_prompt(lambda: 'A-提示注入')\n"
        "    ctx.on_user_message(lambda t: t + '[A]')\n"
        "    ctx.on_reply(lambda r: r + '[AR]')\n"
        "    ctx.route('GET', '/hello', lambda req: {'ok': True, 'from': 'A'})\n",
        encoding="utf-8")
    (root / "plug_b.py").write_text(
        "# 无 META、无参 register 的兼容插件\n"
        "from backend.tools.base import ToolRegistry\n"
        "def register():\n"
        "    ToolRegistry.register_func(name='test_tool_b', description='B工具',\n"
        "        func=lambda **kw: 'b', is_async=False,\n"
        "        input_schema={'type': 'object', 'properties': {}})\n",
        encoding="utf-8")
    (root / "_helper.py").write_text("# 辅助文件，应被忽略\n", encoding="utf-8")

    # ---- 1) 快照恢复回归（P0 / CODE-REVIEW #15）----
    original = FunctionTool(
        name="test_overridden_tool", description="内置原版",
        func=lambda **kw: "builtin", is_async=False,
        input_schema={"type": "object", "properties": {}},
        owner="builtin")
    ToolRegistry.register(original)

    name = loader.load_plugin(root / "plug_a.py")
    assert name == "plug_a", f"plug_a 应加载成功: {loader.plugin_states().get('plug_a')}"
    overridden = ToolRegistry.get("test_overridden_tool")
    assert overridden is not original, "插件应覆盖同名工具"
    assert overridden.owner == "plug_a", f"覆盖工具应标记 owner=插件名: {overridden.owner}"
    assert ToolRegistry.get("test_tool_a") is not None, "插件新工具应已注册"
    print("[OK] P0 插件加载并覆盖内置工具（owner 标记正确）")

    removed = loader.unload_plugin("plug_a")
    assert removed, "卸载应移除插件工具"
    restored = ToolRegistry.get("test_overridden_tool")
    assert restored is original, "卸载后应精确恢复内置原工具（快照恢复）"
    assert ToolRegistry.get("test_tool_a") is None, "插件工具应已移除"
    print("[OK] P0 卸载后内置工具按快照精确恢复，无残留")

    # 重载场景：加载 → 重载 → 卸载，仍应恢复原版
    assert loader.load_plugin(root / "plug_a.py") == "plug_a"
    assert loader.load_plugin(root / "plug_a.py") == "plug_a"  # 重载
    loader.unload_plugin("plug_a")
    assert ToolRegistry.get("test_overridden_tool") is original, "重载后再卸载也应恢复原版"
    print("[OK] P0 重载→卸载路径同样精确恢复")

    # ---- 2) 元信息 + 能力清单 ----
    assert loader.load_plugin(root / "plug_a.py") == "plug_a"  # 重新加载（上段结尾已卸载）
    assert loader.load_plugin(root / "plug_b.py") == "plug_b"
    states = loader.plugin_states()
    a = states["plug_a"]
    assert a["display_name"] == "插件A" and a["version"] == "1.2.3", f"元信息应被记录: {a}"
    assert "test_tool_a" in a["tools"] and "test_overridden_tool" in a["tools"], f"工具清单应完整: {a['tools']}"
    assert a["routes"] == ["GET /hello"], f"路由清单: {a['routes']}"
    assert "a-task" in a["tasks"], f"任务清单: {a['tasks']}"
    b = states["plug_b"]
    assert not b["display_name"] or b["display_name"] == "plug_b", "无 META 插件展示名应回落为文件名"
    assert not b["description"] and not b["author"], "无 META 插件描述/作者应为空"
    assert ToolRegistry.get("test_tool_b") is not None, "无参 register() 兼容插件应加载"
    print("[OK] P1 PLUGIN_META 元信息 + 无参 register() 向后兼容")

    # ---- 3) 定时任务注册与卸载清理 ----
    task_pairs = plugctx._SCHED_TASKS.get("plug_a", [])
    assert any(tn == "a-task" for tn, _ in task_pairs), f"定时任务应已注册: {task_pairs}"
    loader.unload_plugin("plug_a")
    assert "plug_a" not in plugctx._SCHED_TASKS, "卸载应取消定时任务"
    assert ToolRegistry.get("test_tool_a") is None, "任务插件卸载后工具应移除"
    assert ToolRegistry.get("test_overridden_tool") is original, "任务插件卸载后内置工具恢复"
    assert loader.load_plugin(root / "plug_a.py") == "plug_a"
    print("[OK] P4a 定时任务注册 + 卸载自动取消")

    # ---- 4) 钩子链（user_message / reply / system_prompt）----
    assert plugctx.apply_user_message("hi") == "hi[A]", "用户消息钩子应生效"
    assert plugctx.apply_reply("你好") == "你好[AR]", "回复钩子应生效"
    contrib = plugctx.system_prompt_contributions()
    assert "A-提示注入" in contrib, f"系统提示注入应生效: {contrib!r}"
    loader.unload_plugin("plug_a")
    assert plugctx.apply_user_message("hi") == "hi", "卸载后用户消息钩子应消失"
    assert plugctx.apply_reply("你好") == "你好", "卸载后回复钩子应消失"
    assert "A-提示注入" not in plugctx.system_prompt_contributions(), "卸载后提示注入应消失"
    # 异常钩子不阻断
    (root / "plug_c.py").write_text(
        "def register(ctx):\n"
        "    def boom(t):\n"
        "        raise RuntimeError('boom')\n"
        "    ctx.on_user_message(boom)\n"
        "    ctx.on_system_prompt(lambda: 1 / 0)\n",
        encoding="utf-8")
    assert loader.load_plugin(root / "plug_c.py") == "plug_c"
    assert plugctx.apply_user_message("hi") == "hi", "钩子异常应跳过不阻断"
    assert "boom" not in plugctx.system_prompt_contributions()
    loader.unload_plugin("plug_c")
    print("[OK] P4b/d 三类钩子接线 + 卸载清理 + 异常隔离")
    # 重新加载 plug_a（后续 API/网关测试需要它是已加载状态）
    assert loader.load_plugin(root / "plug_a.py") == "plug_a"


def _api_part(root: Path):
    """管理 API + 网关路由（TestClient，最小 app，不触发主应用 startup）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api import plugins as plugins_api

    app = FastAPI()
    app.include_router(plugins_api.router)
    app.include_router(plugins_api.gateway)
    client = TestClient(app)

    # 列表
    r = client.get("/api/plugins")
    assert r.status_code == 200 and r.json()["ok"], r.text
    names = {p["name"] for p in r.json()["plugins"]}
    assert {"plug_a", "plug_b"} <= names, f"列表应含测试插件: {names}"
    print("[OK] P2 GET /api/plugins 列表")

    # 网关路由
    r = client.get("/plugins/plug_a/hello")
    assert r.status_code == 200 and r.json()["from"] == "A", r.text
    r = client.get("/plugins/plug_a/nope")
    assert r.status_code == 404, "未注册路由应 404"
    print("[OK] P4c 插件网关路由分发 + 未命中 404")

    # 禁用 → 网关立即失效 + 持久化 + 列表标记
    r = client.post("/api/plugins/plug_a/disable")
    assert r.status_code == 200 and r.json()["ok"], r.text
    st = {p["name"]: p for p in client.get("/api/plugins").json()["plugins"]}
    assert st["plug_a"]["disabled"] and not st["plug_a"]["loaded"]
    assert client.get("/plugins/plug_a/hello").status_code == 404, "禁用后网关应立即失效"
    saved = json.loads(loader._STATE_FILE.read_text(encoding="utf-8"))
    assert "plug_a" in saved.get("disabled", []), f"禁用应持久化: {saved}"
    print("[OK] P2/P4c 禁用：卸载 + 网关失效 + 状态持久化")

    # 禁用状态下 reload 应拒绝
    r = client.post("/api/plugins/plug_a/reload")
    assert r.status_code == 400, "禁用状态下 reload 应 400"

    # 启用 → 重新加载 + 网关恢复 + 持久化移除
    r = client.post("/api/plugins/plug_a/enable")
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert client.get("/plugins/plug_a/hello").json()["from"] == "A", "启用后网关应恢复"
    saved = json.loads(loader._STATE_FILE.read_text(encoding="utf-8"))
    assert "plug_a" not in saved.get("disabled", []), "启用应从持久化移除"
    print("[OK] P2 启用：重载 + 网关恢复 + 持久化更新")

    # reload 接口
    r = client.post("/api/plugins/plug_a/reload")
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert client.get("/plugins/plug_a/hello").status_code == 200
    # 不存在的插件
    assert client.post("/api/plugins/no_such/disable").status_code == 404
    assert client.get("/plugins/no_such/hello").status_code == 404
    print("[OK] P2 reload + 404 边界")


def _cleanup(root: Path):
    for n in ("plug_a", "plug_b", "plug_c"):
        loader.unload_plugin(n)
    ToolRegistry.unregister("test_overridden_tool")


def main():
    # 临时目录放工作区内（系统 TEMP 可能被沙箱/权限拦截）
    root = Path(__file__).resolve().parent / "_tmp_plugins"
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    # 临时接管 loader 全局（不污染真实 plugins/ 与 data/plugins.json）
    orig_dir, orig_state = loader.PLUGINS_DIR, loader._STATE_FILE
    loader.PLUGINS_DIR = root
    loader._STATE_FILE = root / "plugins_state.json"
    try:
        asyncio.run(_async_part(root))
        _api_part(root)
    finally:
        _cleanup(root)
        loader.PLUGINS_DIR = orig_dir
        loader._STATE_FILE = orig_state
        shutil.rmtree(root, ignore_errors=True)
    print("\n=== 插件系统 v2: 全部通过 ===")


main()
