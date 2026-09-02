# -*- coding: utf-8 -*-
"""每步确认服务（ConfirmService）：工具执行前的授权挂起/恢复。

机制：
- ToolRegistry 注册全局 confirm_hook → 本服务的 default_hook
- 工具需确认时：生成 request_id → 通过 contextvar 里的 SSE 推送器把
  confirm_request 事件推给前端 → 挂起（asyncio.Event）等用户响应
- 用户 POST /api/confirm 后：设置 decision 并唤醒挂起的工具执行
- 超时（默认 60s，可配置）自动按拒绝处理
- 无 SSE 通道（MCP/后台/测试）时按配置放行，审计标记 confirmed=auto
"""
from __future__ import annotations

import asyncio
import contextvars
import time
import uuid
from typing import Any

from ..core.config import config
from ..core.log import logger

# 当前会话的 SSE 推送器（由 chat 流程在 _runner 里 set；async (event: dict) -> None）
current_sse_push: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "current_sse_push", default=None
)


class ConfirmService:
    """确认请求注册表：request_id → 挂起状态。"""

    _pending: dict[str, dict] = {}
    _lock = asyncio.Lock()
    # 挂起请求上限：超过时丢弃最旧的（防御性，正常不会触发）
    _PENDING_MAX = 100

    @classmethod
    async def request(cls, tool: str, args: dict, spec: Any, *,
                      push: Any, timeout: int) -> str:
        """发起一次确认请求，挂起直到用户确认/超时。

        Args:
            tool: 工具名
            args: 清洗后的参数
            spec: ToolSpec（含 danger_level 等）
            push: async (event: dict) -> None，把 confirm_request 推给前端
            timeout: 超时秒数

        Returns:
            decision: "allow" | "deny"（超时按 deny）
        """
        rid = uuid.uuid4().hex[:12]
        event = asyncio.Event()
        state = {
            "event": event,
            "decision": None,
            "tool": tool,
            "args": args,
            "ts": time.time(),
        }
        async with cls._lock:
            cls._gc_pending_locked(timeout)
            cls._pending[rid] = state
        try:
            # 构造给前端展示的确认请求（参数脱敏/截断）
            display_args = _display_args(args)
            await push({
                "type": "confirm_request",
                "request_id": rid,
                "tool": tool,
                "args": display_args,
                "danger": getattr(spec, "danger_level", "normal"),
                "message": _human_message(tool, display_args),
                "timeout": timeout,
            })
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.info("[确认] 请求 {} 超时（{}s），按拒绝处理", rid, timeout)
                return "deny"
            return state.get("decision") or "deny"
        finally:
            async with cls._lock:
                cls._pending.pop(rid, None)

    @classmethod
    async def resolve(cls, request_id: str, allow: bool) -> bool:
        """用户对某请求做出决定：唤醒挂起的工具执行。返回是否找到该请求。"""
        async with cls._lock:
            state = cls._pending.get(request_id)
            if state is None:
                return False
            state["decision"] = "allow" if allow else "deny"
            state["event"].set()
            return True

    @classmethod
    def pending_count(cls) -> int:
        return len(cls._pending)

    @classmethod
    def _gc_pending_locked(cls, timeout: int) -> None:
        """清理超过超时仍未响应的挂起请求 + 超出上限时丢弃最旧。"""
        now = time.time()
        expired = [
            rid for rid, s in cls._pending.items()
            if now - s.get("ts", 0) > timeout
        ]
        for rid in expired:
            cls._pending.pop(rid, None)
        while len(cls._pending) >= cls._PENDING_MAX:
            # 丢弃最旧的挂起请求（dict 保持插入序，取第一个即可）
            oldest = next(iter(cls._pending))
            cls._pending.pop(oldest, None)


# 敏感参数名（展示时脱敏）
_SENSITIVE = ("api_key", "token", "password", "secret", "authorization", "key")


def _display_args(args: dict) -> dict:
    """确认框展示用参数：敏感键脱敏、超长值截断。"""
    out: dict = {}
    for k, v in (args or {}).items():
        if any(s in str(k).lower() for s in _SENSITIVE):
            out[str(k)] = "****"
        elif isinstance(v, str) and len(v) > 200:
            out[str(k)] = v[:200] + f"...({len(v)}字符)"
        else:
            out[str(k)] = v
    return out


def _human_message(tool: str, args: dict) -> str:
    """把工具调用转成一句给用户看的确认文案。"""
    msg = f"即将执行操作：{tool}"
    # 常用工具的可读文案
    if tool == "run_command":
        cmd = args.get("command", "")
        return f"要执行系统命令：`{cmd[:80]}`"
    if tool == "run_python":
        code = args.get("code", "")
        # 展示代码摘要（前 200 字符），让用户能审阅要执行的代码
        preview = code[:200].replace("\n", " ⏎ ")
        return (
            f"⚠️ 将执行任意 Python 代码（{len(code)} 字符）——以本机当前用户权限运行，"
            "等同本机权限，非隔离沙箱（仅拦截部分危险操作）："
            f"`{preview}`"
        )
    if tool == "write_file":
        return f"要写入文件：`{args.get('path', '')}`（{len(str(args.get('content', '')))} 字符）"
    if tool == "edit":
        return f"要编辑文件：`{args.get('path', '')}`"
    if tool in ("todo_delete", "todo_complete", "todo_update"):
        return f"待办操作：{tool} #{args.get('task_id', '?')}"
    if tool == "codex_run":
        cwd = config.agent_codex_cwd or "项目根"
        timeout = int(config.agent_codex_timeout)
        return (
            f"要派发独立任务给本机 Codex CLI（工作目录：{cwd}，时限 {timeout}s）。"
            "⚠️ 放行 = 允许 Codex 在该目录内自主执行文件读写与命令，"
            "不受本助手的安全黑名单约束"
        )
    if tool == "dsh_run":
        timeout = int(getattr(config, "agent_dsh_timeout", 120))
        return (
            f"要派发任务给本机 DSH CLI（时限 {timeout}s）。"
            "⚠️ 放行 = 允许 DSH 自主执行操作，不受本助手的安全黑名单约束"
        )
    if tool in ("agent_run", "agent_fanout"):
        p = args.get("prompt", "") or str(args.get("tasks_json", ""))
        return f"要派发外部 AI 任务：`{p[:80]}`"
    # 通用兜底
    keys = ", ".join(f"{k}={v}" for k, v in list(args.items())[:4])
    return msg + (f"（{keys}）" if keys else "")


async def default_confirm_hook(name: str, args: dict, spec: Any, ctx: dict) -> str:
    """全局默认确认钩子（注册进 ToolRegistry）。

    有 SSE 通道且确认开启 → 挂起等用户确认；
    无通道（MCP 直调/后台任务/未知来源）→ write/run/external 类工具默认拒绝，
    只读工具放行；AGENT_CONFIRM_NO_CHANNEL=allow 可恢复旧的本地信任行为。
    """
    if not config.agent_confirm_enabled:
        return "allow"
    push = current_sse_push.get()
    if push is None:
        if getattr(config, "agent_confirm_no_channel", "deny") == "allow":
            return "allow"
        # 无前端通道且是写/命令/外部类：拒绝（避免未知来源静默执行危险操作）
        category = getattr(spec, "category", "")
        if category in ("write", "run", "external"):
            return "deny"
        return "allow"
    try:
        return await ConfirmService.request(
            name, args, spec, push=push,
            timeout=int(config.agent_confirm_timeout),
        )
    except Exception:
        # 钩子内部异常（push 失败/服务异常等）绝不能变成"未经确认执行"：
        # 危险类别（写/命令/外部）一律拒绝，只读类可放行（与无通道策略一致）。
        # 注意：这里若返回 allow 会吞掉 base.py 的"钩子异常 → deny"兜底。
        category = getattr(spec, "category", "")
        if category in ("write", "run", "external"):
            logger.exception("[确认] 确认钩子异常，危险操作按拒绝处理")
            return "deny"
        logger.exception("[确认] 确认钩子异常，只读操作放行")
        return "allow"
