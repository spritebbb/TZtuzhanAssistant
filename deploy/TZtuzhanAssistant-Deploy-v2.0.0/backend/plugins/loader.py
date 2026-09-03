# -*- coding: utf-8 -*-
"""插件系统 v2：自动发现并加载 plugins/*.py（对标 Harness 的插件扩展）。

插件 = 项目根下 plugins/ 目录里的一个 Python 文件，暴露 `register(ctx)`（或无参 `register()`），
在函数内通过 ToolRegistry.register_func 注册工具，或通过 ctx 注册更多能力：

```python
PLUGIN_META = {"name": "示例", "version": "1.0.0", "description": "演示插件", "author": "you"}

def register(ctx):
    def my_tool(**kw):
        return "hello"
    ToolRegistry.register_func(name="my_tool", description="…", func=my_tool,
                               input_schema={"type": "object", "properties": {}})
    ctx.schedule(60, my_job)          # 定时任务（卸载自动取消）
    ctx.on_system_prompt(lambda: "今天天气晴")   # 系统提示注入
    ctx.route("GET", "/status", handler)         # HTTP 路由 → /plugins/示例/status
    ctx.on_user_message(lambda t: t)             # 用户消息钩子（可改写）
    ctx.on_reply(lambda r: r)                    # 回复钩子（可改写）
```

加载顺序：先内置工具，后插件（插件可覆盖同名工具，插件优先级更高）。

卸载语义（v2 修复）：加载前对全部工具做快照；卸载时移除插件工具，并把被覆盖的
内置工具按快照精确恢复 —— 不会残留，也不会丢内置。

热加载：`start_watch()` 启动后台任务监听 plugins/ 目录，新增/修改/删除 .py 文件时
自动加载/重载/卸载，无需重启服务。禁用的插件（data/plugins.json）不加载。
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..core.log import logger
from ..tools.base import FunctionTool, ToolRegistry

# 插件目录：项目根下 plugins/
PLUGINS_DIR = Path(__file__).resolve().parents[2] / "plugins"

# 禁用状态持久化文件：项目根下 data/plugins.json
_STATE_FILE = Path(__file__).resolve().parents[2] / "data" / "plugins.json"


def plugin_name_ok(name: str) -> bool:
    """插件名必须是非空、纯 [A-Za-z0-9_-] 的单层文件名。

    拒绝 ../、子目录、空串等越界名——enable/reload/source 等 API 用 name
    拼路径前先过此校验（纵深防御，与 source 端点的 is_relative_to 一致）。
    """
    return bool(name) and all(ch.isalnum() or ch in "_-" for ch in name)


# 轮询间隔（秒）
_WATCH_INTERVAL = 2.0


# 禁用状态缓存：(文件 mtime, 集合)——热加载每 2s 轮询多次调用 _load_disabled，
# mtime 未变直接用缓存，避免反复磁盘 IO
_disabled_cache: tuple[float, set[str]] | None = None


def _load_disabled() -> set[str]:
    """读取持久化的禁用插件集合（带 mtime 缓存；文件缺失/损坏返回空集）。"""
    global _disabled_cache
    try:
        mtime = _STATE_FILE.stat().st_mtime
    except FileNotFoundError:
        return set()
    except OSError:
        mtime = 0.0
    cached = _disabled_cache
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        disabled = {str(n) for n in data.get("disabled", [])} if isinstance(data, dict) else set()
    except FileNotFoundError:
        disabled = set()
    except Exception:
        logger.exception("[插件] 禁用状态文件读取失败（忽略）")
        disabled = set()
    _disabled_cache = (mtime, disabled)
    return disabled


def _save_disabled(disabled: set[str]) -> None:
    global _disabled_cache
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(
            json.dumps({"disabled": sorted(disabled)}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        _disabled_cache = None  # 失效缓存，下次读取以磁盘为准
    except Exception:
        logger.exception("[插件] 禁用状态写入失败")


@dataclass
class PluginState:
    """记录一个插件的加载状态，用于热加载对比与管理 API 展示。"""
    mtime: float = 0.0                    # 文件的最后修改时间
    meta: dict = field(default_factory=dict)          # PLUGIN_META（无则空）
    tools: list[str] = field(default_factory=list)    # 该插件注册的工具名
    overridden: dict[str, FunctionTool] = field(default_factory=dict)  # 被覆盖的旧工具快照
    owned: dict[str, FunctionTool] = field(default_factory=dict)  # 工具名 → 本插件注册的对象（卸载按对象归属判断）
    routes: list[str] = field(default_factory=list)   # 注册的 HTTP 路由（"METHOD /path"）
    tasks: list[str] = field(default_factory=list)    # 定时任务名
    hooks: dict[str, int] = field(default_factory=dict)  # 各类钩子数量
    loaded: bool = False                # 是否成功加载
    disabled: bool = False              # 是否被禁用（用户手动）
    error: str = ""                     # 最近一次加载失败的错误信息


# 插件名 → 状态（进程内全局，供热加载与 API 使用）
_PLUGIN_STATE: dict[str, PluginState] = {}


def _module_name(name: str) -> str:
    return f"plugin_{name}"


def _registered_tools_before() -> dict[str, FunctionTool]:
    """当前 ToolRegistry 里 name → FunctionTool 快照。"""
    return {t.name: t for t in ToolRegistry.list_tools()}


def _apply_overrides_owner(name: str, before: dict[str, FunctionTool],
                           after: dict[str, FunctionTool]) -> None:
    """把本次加载新注册/覆盖的工具标 owner=插件名（卸载恢复用）。"""
    for tname, tool in after.items():
        old = before.get(tname)
        if old is None or old is not tool:
            tool.owner = name


def _cleanup_context(name: str) -> None:
    """清理插件通过 ctx 注册的定时任务/钩子/路由（无 ctx 系统时静默跳过）。"""
    try:
        from .context import cleanup_plugin

        cleanup_plugin(name)
    except ImportError:
        pass
    except Exception:
        logger.exception("[插件] 上下文清理失败: {}", name)


def _build_ctx(name: str, state: PluginState):
    """构建插件上下文（无 ctx 系统时返回 None，插件用无参 register()）。"""
    try:
        from .context import PluginContext
    except ImportError:
        return None
    return PluginContext(name, state)


def _load_module(path: Path, name: str, state: PluginState):
    """加载插件模块并执行 register()，返回模块（失败抛异常由调用方捕获）。"""
    # 清理旧模块缓存，确保 import 的是磁盘最新内容
    sys.modules.pop(_module_name(name), None)
    spec = importlib.util.spec_from_file_location(_module_name(name), path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_module_name(name)] = module
    spec.loader.exec_module(module)
    # 元信息（可选）
    meta = getattr(module, "PLUGIN_META", None)
    state.meta = dict(meta) if isinstance(meta, dict) else {}
    register = getattr(module, "register", None)
    if not callable(register):
        raise ValueError(f"插件 {path.name} 未暴露 register() 函数")
    ctx = _build_ctx(name, state)
    if ctx is not None and _register_takes_arg(register):
        register(ctx)
    else:
        register()  # 无参 register()（v1 兼容）或无 ctx 系统
    return module


def _register_takes_arg(register) -> bool:
    """判断 register 是否接受位置参数（def register(ctx) → True；def register() → False）。"""
    import inspect

    try:
        sig = inspect.signature(register)
    except (TypeError, ValueError):
        return False
    for p in sig.parameters.values():
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
            return True
    return False


def load_plugin(path: Path) -> str | None:
    """加载单个插件文件，返回插件名（失败返回 None；被禁用也返回 None）。

    v2 卸载语义：加载前快照全部工具，加载后对比得到
    - tools：本插件新注册的工具名；
    - overridden：被本插件覆盖的同名旧工具快照（卸载时精确恢复）。
    """
    name = path.stem
    if name.startswith("_"):  # 忽略 _ 开头（辅助文件/草稿）
        return None
    if name in _load_disabled():
        _PLUGIN_STATE[name] = PluginState(disabled=True)
        logger.info("[插件] 已禁用，跳过: {}", name)
        return None
    # 卸载该插件先前注册的能力（重载场景）
    prev = _PLUGIN_STATE.get(name)
    if prev and prev.loaded:
        _teardown(name, prev)
    before = _registered_tools_before()
    state = PluginState(mtime=_safe_mtime(path))
    try:
        _load_module(path, name, state)
    except Exception as e:
        logger.exception("[插件] 加载失败: {}", path.name)
        # 加载失败：先把本次 register() 中途已注册/覆盖的工具精确回滚，
        # 避免"半注册"工具残留（旧版只在热重载路径清理，初次加载路径漏了）
        try:
            _rollback_registry(before)
            _cleanup_context(name)
        except Exception:
            logger.exception("[插件] 加载失败回滚异常: {}", name)
        # 清空状态（若之前有工具已卸载，保持卸载）
        _PLUGIN_STATE[name] = PluginState(mtime=0.0, loaded=False,
                                          error=f"{type(e).__name__}: {e}")
        return None
    after = _registered_tools_before()
    _apply_overrides_owner(name, before, after)
    state.tools = sorted(t for t, tool in after.items() if tool.owner == name)
    state.owned = {t: after[t] for t in state.tools if t in after}
    state.overridden = {
        t: old for t, old in before.items()
        if t in after and after[t] is not old
    }
    state.loaded = True
    state.error = ""
    _PLUGIN_STATE[name] = state
    return name


def _rollback_registry(before: dict[str, FunctionTool]) -> None:
    """把工具注册表回滚到加载前的快照：移除新增、恢复被覆盖/被移除的旧工具。"""
    after = _registered_tools_before()
    # 1) 恢复被插件覆盖或删除的旧工具
    for tname, old_tool in before.items():
        cur = after.get(tname)
        if cur is None or cur is not old_tool:
            ToolRegistry.register(old_tool)
    # 2) 移除插件新增的工具（before 里没有的）
    for tname in ToolRegistry.tool_names():
        if tname not in before:
            ToolRegistry.unregister(tname)


def _teardown(name: str, state: PluginState) -> None:
    """卸载插件已注册的全部能力（工具恢复 + 上下文清理）。"""
    # 1) 移除本插件注册的工具：按「对象身份」判断，若同名工具已被其他插件覆盖
    #    （registry 里不是本插件注册的那个对象），跳过——避免误删新插件工具
    for tname in state.tools:
        obj = state.owned.get(tname)
        cur = ToolRegistry.get(tname)
        if obj is not None and cur is obj:
            ToolRegistry.unregister(tname)
    # 2) 精确恢复被覆盖的旧工具（对象身份 + 归属插件仍加载才恢复，避免
    #    卸载顺序（A 先卸载、B 后卸载）恢复出已卸载 A 的孤儿工具）
    for tname, old_tool in state.overridden.items():
        cur = ToolRegistry.get(tname)
        if cur is not None:
            continue  # 当前仍有工具（其他插件或本插件的残留覆盖），不覆盖
        owner_ok = old_tool.owner == "builtin"
        if not owner_ok:
            owner_state = _PLUGIN_STATE.get(old_tool.owner)
            owner_ok = owner_state is not None and owner_state.loaded
        if owner_ok:
            ToolRegistry.register(old_tool)
    # 3) 清理定时任务/钩子/路由
    _cleanup_context(name)


def unload_plugin(name: str) -> bool:
    """卸载一个插件（移除其注册的工具并恢复被覆盖的内置工具），返回是否有工具被移除。"""
    state = _PLUGIN_STATE.get(name)
    if not state:
        return False
    removed = len(state.tools)
    if state.loaded:
        _teardown(name, state)
    sys.modules.pop(_module_name(name), None)
    _PLUGIN_STATE.pop(name, None)
    return removed > 0


def set_disabled(name: str, disabled: bool) -> bool:
    """设置插件禁用状态并持久化；disabled=True 时立即卸载。返回是否生效。"""
    if not plugin_name_ok(name):
        return False
    if disabled:
        if not _PLUGIN_STATE.get(name) and not (PLUGINS_DIR / f"{name}.py").exists():
            return False
        unload_plugin(name)
        st = _PLUGIN_STATE.setdefault(name, PluginState())
        st.disabled = True
    else:
        st = _PLUGIN_STATE.get(name)
        if st is not None:
            st.disabled = False
    disabled_set = _load_disabled()
    if disabled:
        disabled_set.add(name)
    else:
        disabled_set.discard(name)
    _save_disabled(disabled_set)
    return True


def plugin_states() -> dict[str, dict]:
    """全部插件状态的只读快照（供管理 API 使用）。"""
    return {
        name: {
            "name": name,
            "display_name": st.meta.get("name") or name,
            "version": st.meta.get("version", ""),
            "description": st.meta.get("description", ""),
            "author": st.meta.get("author", ""),
            "tools": list(st.tools),
            "routes": list(st.routes),
            "tasks": list(st.tasks),
            "hooks": dict(st.hooks),
            "loaded": st.loaded,
            "disabled": st.disabled,
            "error": st.error,
            "mtime": st.mtime,
        }
        for name, st in _PLUGIN_STATE.items()
    }


def load_all_plugins(plugins_dir: Path | None = None) -> list[str]:
    """扫描插件目录，加载所有未禁用的插件，返回成功加载的插件名列表。"""
    d = plugins_dir or PLUGINS_DIR
    if not d.exists():
        return []
    loaded: list[str] = []
    for path in sorted(d.glob("*.py")):
        name = load_plugin(path)
        if name:
            loaded.append(name)
    return loaded


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _scan_watch(plugins_dir: Path) -> None:
    """执行一次热加载扫描：对比磁盘与内存状态，处理新增/修改/删除。"""
    d = plugins_dir
    if not d.exists():
        return
    disabled = _load_disabled()
    # 磁盘上的 .py 文件（忽略 _ 开头），key 用插件名（不含 .py）
    disk: dict[str, Path] = {}
    for p in d.glob("*.py"):
        if not p.name.startswith("_"):
            disk[p.stem] = p

    disk_names = set(disk)
    mem_names = set(_PLUGIN_STATE)

    # 0) 被禁用的插件：保持卸载状态（记录禁用标记）
    for name in disk_names & disabled:
        st = _PLUGIN_STATE.get(name)
        if st is None or st.loaded:
            _PLUGIN_STATE[name] = PluginState(mtime=_safe_mtime(disk[name]), disabled=True)

    # 1) 删除的插件
    for name in mem_names - disk_names:
        st = _PLUGIN_STATE.get(name)
        if st is not None and st.disabled:
            continue  # 禁用中的插件文件暂时移走不算删除，保留禁用状态
        logger.info("[热加载] 插件文件已删除，卸载: {}", name)
        unload_plugin(name)

    # 2) 新增 / 修改的插件（禁用的除外）
    for name, path in disk.items():
        if name in disabled:
            continue
        mtime = _safe_mtime(path)
        state = _PLUGIN_STATE.get(name)
        if state is None or (state.disabled and not state.loaded):
            # 新增（或刚从禁用恢复）
            logger.info("[热加载] 发现新插件: {}.py", name)
            if load_plugin(path):
                logger.info("[热加载] 已加载新插件: {}.py", name)
        elif not state.loaded:
            # 上次加载失败，重试
            if load_plugin(path):
                logger.info("[热加载] 已重新加载插件: {}.py", name)
        elif abs(mtime - state.mtime) > 0.01:
            # 内容变化 → 重载
            logger.info("[热加载] 插件变化，重载: {}.py", name)
            if load_plugin(path):
                logger.info("[热加载] 重载完成: {}.py", name)


async def watch_plugins(plugins_dir: Path | None = None) -> None:
    """后台热加载循环：定期扫描插件目录，自动加载新插件/重载修改/卸载删除。"""
    d = plugins_dir or PLUGINS_DIR
    logger.info("[热加载] 开始监听插件目录: {}", d)
    while True:
        try:
            _scan_watch(d)
        except Exception:
            logger.exception("[热加载] 扫描异常（继续监听）")
        await asyncio.sleep(_WATCH_INTERVAL)


async def start_watch(plugins_dir: Path | None = None):
    """启动热加载后台任务（供 app startup 调用），返回 task。"""
    return asyncio.create_task(watch_plugins(plugins_dir))


def plugin_tool_names() -> list[str]:
    """返回所有已注册工具的完整列表（用于校验插件是否生效）。"""
    return [t.name for t in ToolRegistry.list()]
