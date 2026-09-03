# -*- coding: utf-8 -*-
"""工具基类 + 全局注册表。

设计：
- FunctionTool：函数式工具，用普通 async/sync 函数快速注册（推荐）
- ToolRegistry：全局注册表，注册/查找/执行工具
- 工具调用方式：LLM 用原生 Function Calling（首选）或 ```tool {json}``` 代码块发起，
  经 ToolRegistry 分发
- 统一执行管线：参数校验 → 确认钩子（confirm_hook）→ 执行 → 结果压缩 → 审计
"""
from __future__ import annotations

import asyncio
import contextvars
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..models.tool import ToolResult, ToolSpec

# 工具类别（用于决定是否需要确认 / 提示分组）
CATEGORY_READ = "read"          # 只读，自动执行
CATEGORY_WRITE = "write"        # 写操作，默认需确认
CATEGORY_RUN = "run"            # 命令/进程，默认需确认
CATEGORY_EXTERNAL = "external"  # 外部 Agent（codex/dsh），默认需确认

# 危险等级（用于确认框颜色与文案）
DANGER_INFO = "info"      # 无风险
DANGER_NORMAL = "normal"  # 常规操作
DANGER_HIGH = "high"      # 高风险（删除/覆盖/命令）
DANGER_CRITICAL = "critical"  # 直接拒绝，不弹确认


def compress_text(text: str, max_chars: int = 4000) -> str:
    """结果压缩：超长输出保留头尾 + 中间摘要提示，避免撑爆上下文。"""
    if not text:
        return text
    if len(text) <= max_chars:
        return text
    head_len = int(max_chars * 0.7)
    tail_len = max_chars - head_len
    return (
        text[:head_len]
        + f"\n…[内容过长，中间省略 {len(text) - head_len - tail_len} 字符]…\n"
        + text[-tail_len:]
    )


def _is_binding_error(exc: TypeError) -> bool:
    """判断 TypeError 是否属于"调用参数绑定"错误（缺参/多参/未知关键字等）。"""
    msg = str(exc)
    hints = (
        "missing ",
        "required positional",
        "unexpected keyword",
        "got an unexpected keyword",
        "takes ",
        "positional argument",
        "multiple values for argument",
        "argument after **",
        "keyword-only",
    )
    return any(h in msg for h in hints)


@dataclass
class FunctionTool:
    """函数式工具：用普通 async/sync 函数快速注册。"""

    name: str
    description: str
    func: Callable[..., Any]
    input_schema: dict = field(default_factory=dict)
    is_async: bool = True
    # ---- Agent 协议增强 ----
    category: str = CATEGORY_READ           # read/write/run/external
    danger_level: str = DANGER_NORMAL       # info/normal/high/critical
    needs_confirm: bool = False             # 是否必须弹确认（write/run/external 默认 True）
    max_output_chars: int = 4000            # 结果截断上限（0=不截断）
    # ---- 来源标记 ----
    owner: str = "builtin"                  # 注册来源：builtin / 插件名（插件卸载恢复用）

    async def execute(self, args: dict, ctx: dict | None = None) -> ToolResult:
        t0 = time.monotonic()
        try:
            if self.is_async:
                output = await self.func(**args)
            else:
                # sync 函数运行在线程池（to_thread 不继承 contextvars），
                # 需显式拷贝当前 context，保证 current_user_id 等传递
                cur_ctx = contextvars.copy_context()
                output = await asyncio.to_thread(
                    lambda: cur_ctx.run(self.func, **args)
                )
            output = str(output)
            if self.max_output_chars and len(output) > self.max_output_chars:
                output = compress_text(output, self.max_output_chars)
            return ToolResult(ok=True, tool=self.name, output=output, elapsed_ms=ms(t0))
        except TypeError as e:
            # 只把"参数绑定"类 TypeError 当参数错误返回（缺参/多参/未知关键字）；
            # 工具函数内部自己抛出的 TypeError（如对 None 调用方法）是真实 bug，
            # 按普通异常透传，避免误导性"参数错误"文案掩盖代码缺陷
            if _is_binding_error(e):
                return ToolResult(ok=False, tool=self.name, error=f"参数错误: {e}", elapsed_ms=ms(t0))
            raise
        except Exception as e:
            return ToolResult(ok=False, tool=self.name, error=f"{type(e).__name__}: {e}", elapsed_ms=ms(t0))

    def to_spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
            category=self.category,
            danger_level=self.danger_level,
            needs_confirm=self.needs_confirm,
            max_output_chars=self.max_output_chars,
        )

    def to_openai_schema(self) -> dict:
        """转换为 OpenAI Function Calling 的 tools 条目。"""
        # OpenAI 的 required 需要在顶层 parameters 里；args 参数名取 schema 的 required
        params = dict(self.input_schema or {})
        params.setdefault("type", "object")
        params.setdefault("properties", {})
        # 兼容两种 required 位置：tool schema 可能把 required 挂在顶层
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": params,
            },
        }


def ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


class ToolRegistry:
    """全局工具注册表。

    并发安全：注册/卸载（热加载插件、外部 MCP 注册）与读取/执行可能发生在
    不同协程（事件循环单线程）与工作线程（to_thread 跑的同步工具函数）之间。
    这里用一把 threading.Lock + copy-on-write：写操作在锁内复制 dict 再替换，
    读操作在锁内取一次引用快照后立刻释放锁——既避免读到半更新状态，又不阻塞
    正常读路径。
    """

    _tools: dict[str, FunctionTool] = {}
    _lock: threading.Lock = threading.Lock()
    # 确认钩子：async (name, args, spec) -> "allow" | "deny" | "blocked" | None
    #   allow/None：放行；deny：用户拒绝（按拒绝结果返回给 LLM）；
    #   blocked：直接拒绝（不弹确认，危险命令白名单用）
    _confirm_hook: Callable[..., Awaitable[str]] | None = None

    @classmethod
    def _snapshot(cls) -> dict[str, FunctionTool]:
        """取当前注册表的只读快照引用（copy-on-write 下无需深拷贝）。"""
        with cls._lock:
            return cls._tools

    @classmethod
    def register(cls, tool: FunctionTool) -> None:
        with cls._lock:
            # copy-on-write：不原地 mutate，避免读方遍历到变更中间态
            cls._tools = {**cls._tools, tool.name: tool}

    @classmethod
    def register_func(cls, name: str, description: str, func: Callable[..., Any],
                      input_schema: dict | None = None, is_async: bool = True,
                      category: str = CATEGORY_READ, danger_level: str = DANGER_NORMAL,
                      needs_confirm: bool = False, max_output_chars: int = 4000,
                      owner: str = "builtin") -> None:
        cls.register(FunctionTool(
            name=name, description=description, func=func,
            input_schema=input_schema or {}, is_async=is_async,
            category=category, danger_level=danger_level,
            needs_confirm=needs_confirm, max_output_chars=max_output_chars,
            owner=owner,
        ))

    @classmethod
    def get(cls, name: str) -> FunctionTool | None:
        return cls._snapshot().get(name)

    @classmethod
    def unregister(cls, name: str) -> bool:
        """移除一个已注册的工具，返回是否移除成功。"""
        with cls._lock:
            if name not in cls._tools:
                return False
            cls._tools = {k: v for k, v in cls._tools.items() if k != name}
            return True

    @classmethod
    def unregister_many(cls, names: list[str]) -> int:
        """批量移除工具，返回移除数量。"""
        with cls._lock:
            drop = set(names)
            removed = sum(1 for n in names if n in cls._tools)
            if removed:
                cls._tools = {k: v for k, v in cls._tools.items() if k not in drop}
            return removed

    @classmethod
    def list(cls) -> list[ToolSpec]:
        return [t.to_spec() for t in cls._snapshot().values()]

    @classmethod
    def list_tools(cls) -> list[FunctionTool]:
        """返回全部已注册的 FunctionTool 对象（含 owner 来源，插件快照/恢复用）。"""
        return list(cls._snapshot().values())

    @classmethod
    def tool_names(cls) -> list[str]:
        """返回全部已注册工具名（只读视图，供外部遍历；避免直接触碰私有 _tools）。"""
        return list(cls._snapshot().keys())

    @classmethod
    def openai_tools(cls) -> list[dict]:
        """返回全部工具的 OpenAI Function Calling schema（用于原生工具调用）。"""
        return [t.to_openai_schema() for t in cls._snapshot().values()]

    @classmethod
    def set_confirm_hook(cls, hook: Callable[..., Awaitable[str]] | None) -> None:
        """设置全局确认钩子（由确认服务注入；None 表示直接放行）。"""
        cls._confirm_hook = hook

    @classmethod
    def get_confirm_hook(cls) -> Callable[..., Awaitable[str]] | None:
        return cls._confirm_hook

    @classmethod
    async def execute(cls, name: str, args: dict, ctx: dict | None = None) -> ToolResult:
        """统一执行管线：查工具 → 确认钩子 → 执行 → 审计。

        返回 ToolResult。确认被拒绝/拦截时返回 ok=False 但 confirmed 标注状态，
        供上层（工具循环）决定如何注入 LLM。
        """
        tool = cls._snapshot().get(name)
        if tool is None:
            return ToolResult(ok=False, tool=name, error=f"未知工具: {name}", confirmed="auto")
        from ..core.current_user import current_user_id
        user = current_user_id.get("assistant-main")
        t0 = time.monotonic()
        confirmed = "auto"
        hook = cls._confirm_hook

        # 1) 危险等级 critical：直接拒绝，不弹确认
        if tool.danger_level == DANGER_CRITICAL:
            from .audit import log_tool_call
            log_tool_call(tool=name, args=args, confirmed="blocked", ok=False,
                          result="", error="危险操作被策略拒绝", user=user)
            return ToolResult(ok=False, tool=name, error="该操作被安全策略拒绝，无法执行", confirmed="blocked",
                              elapsed_ms=ms(t0))

        # 2) 确认钩子：需要确认的工具在执行前征求用户批准
        if hook is not None and (tool.needs_confirm or tool.category in (CATEGORY_WRITE, CATEGORY_RUN, CATEGORY_EXTERNAL)):
            try:
                decision = await hook(name, args, tool.to_spec(), ctx or {})
                confirmed = decision if decision in ("allow", "deny", "blocked") else "allow"
            except asyncio.CancelledError:
                raise
            except Exception:
                # 确认钩子异常（如前端断连导致 push 抛错）：对需要确认的工具
                # 按拒绝处理，绝不未经确认执行写/命令/外部操作
                confirmed = "deny"
            if confirmed in ("deny", "blocked"):
                from .audit import log_tool_call
                reason = "用户拒绝了该操作" if confirmed == "deny" else "操作被安全策略拒绝"
                log_tool_call(tool=name, args=args, confirmed=confirmed, ok=False,
                              result="", error=reason, user=user)
                return ToolResult(ok=False, tool=name, error=reason, confirmed=confirmed,
                                  elapsed_ms=ms(t0))

        # 3) 执行
        result = await tool.execute(args, ctx)
        result.confirmed = confirmed

        # 4) 审计
        from .audit import log_tool_call
        log_tool_call(tool=name, args=args, confirmed=confirmed, ok=result.ok,
                      result=result.output, error=result.error, user=user)
        return result
