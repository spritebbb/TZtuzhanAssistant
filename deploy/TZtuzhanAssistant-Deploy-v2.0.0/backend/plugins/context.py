# -*- coding: utf-8 -*-
"""插件上下文（PluginContext）：给插件注册定时任务 / 钩子 / HTTP 路由的能力面。

每个插件在 register(ctx) 时拿到一个属于自己的 PluginContext，所有注册都记录在
模块级注册表里，插件卸载/重载时 `cleanup_plugin(name)` 统一撤销：

- ctx.schedule(interval, fn, name="")   周期定时任务（asyncio task，自动取消）
- ctx.on_system_prompt(fn)              系统提示注入：fn() -> str | None
- ctx.on_user_message(fn)               用户消息钩子：fn(text) -> str | None（可改写）
- ctx.on_reply(fn)                      回复钩子：fn(reply) -> str | None（可改写）
- ctx.route(method, path, handler)      HTTP 路由 → /plugins/{插件名}/{path}

钩子调用一律逐个执行、异常跳过并记日志，永不阻断主流程。
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable

from ..core.log import logger

# ---- 各类注册表（插件名 → 条目列表） ----

_SCHED_TASKS: dict[str, list[tuple[str, asyncio.Task]]] = {}   # name → [(task_name, task)]
_SYSTEM_PROMPT_HOOKS: dict[str, list[Callable[[], Any]]] = {}
_USER_MSG_HOOKS: dict[str, list[Callable[[str], Any]]] = {}
_REPLY_HOOKS: dict[str, list[Callable[[str], Any]]] = {}
_ROUTES: dict[str, dict[tuple[str, str], Callable[..., Any]]] = {}  # name → {(method, path): handler}


class PluginContext:
    """单个插件的注册上下文（register(ctx) 收到的对象）。"""

    def __init__(self, name: str, state) -> None:  # state: loader.PluginState
        self.name = name
        self._state = state

    # ---- a) 定时任务 ----

    def schedule(self, interval: float, fn: Callable[..., Any], name: str = "") -> str:
        """注册周期任务：每 interval 秒执行一次 fn（支持 async/sync）。

        返回任务名；插件卸载/重载时任务自动取消。
        需要事件循环运行中（启动加载与热加载均在 async 上下文内执行）。
        """
        if interval <= 0:
            raise ValueError("interval 必须大于 0")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as e:
            raise RuntimeError(
                "ctx.schedule 需要事件循环：插件须在异步上下文中加载"
                "（后端 startup / 热加载 / 管理 API 均满足；同步脚本请用 asyncio.run 包裹）"
            ) from e
        task_name = name or f"{self.name}:job{len(_SCHED_TASKS.get(self.name, [])) + 1}"

        async def _runner() -> None:
            while True:
                try:
                    await asyncio.sleep(interval)
                except asyncio.CancelledError:
                    raise
                try:
                    r = fn()
                    if inspect.isawaitable(r):
                        await r
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("[插件任务] {} 执行失败（继续）", task_name)

        task = loop.create_task(_runner())
        _SCHED_TASKS.setdefault(self.name, []).append((task_name, task))
        if self._state is not None:
            self._state.tasks.append(task_name)
        logger.info("[插件] {} 注册定时任务 {}（每 {}s）", self.name, task_name, interval)
        return task_name

    # ---- b) 系统提示钩子 ----

    def on_system_prompt(self, fn: Callable[[], Any]) -> None:
        """注册系统提示贡献：fn() -> str | None，逐条追加到 system prompt 末尾。"""
        _SYSTEM_PROMPT_HOOKS.setdefault(self.name, []).append(fn)
        self._bump_hook("system_prompt")

    # ---- c) HTTP 路由 ----

    def route(self, method: str, path: str, handler: Callable[..., Any]) -> None:
        """注册 HTTP 路由：最终挂到 /plugins/{插件名}{path}（经插件网关分发）。

        handler 为 FastAPI 风格：async def handler(request) -> Response | dict | str。
        path 不含插件前缀，如 "/status"；支持同一路径多 method 各自注册。
        """
        m = method.strip().upper()
        p = "/" + path.strip().strip("/")
        _ROUTES.setdefault(self.name, {})[(m, p)] = handler
        label = f"{m} {p}"
        if self._state is not None and label not in self._state.routes:
            self._state.routes.append(label)
        logger.info("[插件] {} 注册路由 /plugins/{}/{}", self.name, self.name, p.lstrip("/"))

    # ---- d) 消息钩子 ----

    def on_user_message(self, fn: Callable[[str], Any]) -> None:
        """注册用户消息钩子：fn(text) -> str | None；返回 str 时改写用户消息文本。"""
        _USER_MSG_HOOKS.setdefault(self.name, []).append(fn)
        self._bump_hook("user_message")

    def on_reply(self, fn: Callable[[str], Any]) -> None:
        """注册回复钩子：fn(reply) -> str | None；返回 str 时改写最终回复文本。"""
        _REPLY_HOOKS.setdefault(self.name, []).append(fn)
        self._bump_hook("reply")

    def _bump_hook(self, kind: str) -> None:
        if self._state is not None:
            self._state.hooks[kind] = self._state.hooks.get(kind, 0) + 1


# ---- 钩子统一调用入口（供 pipeline / persona 接线） ----

def _call_each(registry: dict[str, list], value: str) -> str:
    """逐插件执行钩子链；返回 str 则替换当前文本，返回 None/其他则保持不变。"""
    for name, hooks in registry.items():
        for fn in hooks:
            try:
                out = fn(value)
                if isinstance(out, str) and out:
                    value = out
            except Exception:
                logger.exception("[插件钩子] {} 执行失败（跳过）", name)
    return value


def apply_user_message(text: str) -> str:
    """pipeline 入口调用：依次过所有插件的 on_user_message 钩子。"""
    return _call_each(_USER_MSG_HOOKS, text)


def apply_reply(reply: str) -> str:
    """pipeline 出口调用：依次过所有插件的 on_reply 钩子。"""
    return _call_each(_REPLY_HOOKS, reply)


def system_prompt_contributions() -> str:
    """persona 组装 system prompt 时调用：收集所有插件贡献的文本（\n 拼接）。"""
    parts: list[str] = []
    for name, hooks in _SYSTEM_PROMPT_HOOKS.items():
        for fn in hooks:
            try:
                out = fn()
                if isinstance(out, str) and out.strip():
                    parts.append(out.strip())
            except Exception:
                logger.exception("[插件钩子] system_prompt {} 执行失败（跳过）", name)
    return "\n".join(parts)


# ---- HTTP 路由网关（供 api/plugins.py 挂接） ----

def dispatch_route(plugin: str, method: str, path: str) -> Callable[..., Any] | None:
    """按 (插件名, METHOD, path) 查找 handler；未命中返回 None。"""
    return _ROUTES.get(plugin, {}).get((method.strip().upper(), path))


def plugin_route_count() -> int:
    return sum(len(v) for v in _ROUTES.values())


# ---- 卸载清理 ----

def cleanup_plugin(name: str) -> None:
    """撤销插件注册的全部定时任务/钩子/路由（卸载与重载时由 loader 调用）。"""
    for _tname, task in _SCHED_TASKS.pop(name, []):
        task.cancel()
    _SYSTEM_PROMPT_HOOKS.pop(name, None)
    _USER_MSG_HOOKS.pop(name, None)
    _REPLY_HOOKS.pop(name, None)
    _ROUTES.pop(name, None)
