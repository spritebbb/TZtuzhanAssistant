# -*- coding: utf-8 -*-
"""§17.3 工具循环加固：签名熔断、结果预算、结构化错误（不泄堆栈/密钥）。

在既有 tool_loop 契约上叠加（docs/Zcode技术指导.md §17.3 + 总纲批次 12）：

- 单轮工具调用上限 8 次（既有 MAX_TOOL_CALLS）；
- **同签名 2 次即熔断**：同一 (name, args) 指纹第 2 次出现时拒绝执行并给
  模型一条结构化错误（既有 seen 缓存只复用结果，本闸门阻断重试意图）；
- **总结果预算 32KB**：累计工具结果超出后，剩余调用直接给结构化错误，
  不再执行；
- schema 错误、权限拒绝、溢出、provider error 统一走
  :func:`structured_tool_error`——给模型一条可读的结构化错误，
  绝不把堆栈/密钥/环境细节塞回上下文；
- 超时用单调时钟测真实 elapsed（ToolResult.elapsed_ms 已是
  time.monotonic 语义，本模块补充预算计量的同源时钟）。
"""
from __future__ import annotations

import json
import re
import time

from ..core.log import logger
from .base import ToolRegistry

MAX_TOOL_CALLS = 8                 # 单轮调用上限
SAME_SIGNATURE_LIMIT = 2           # 同签名第 2 次熔断
TOTAL_RESULT_BUDGET = 32 * 1024    # 单轮工具结果总预算（字节）

_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9]{8,}|Bearer\s+\S+|api[_-]?key\s*[=:]\s*\S+|"
    r"Authorization\s*[:=]\s*\S+|[A-Za-z0-9_]*TOKEN[A-Za-z0-9_]*\s*[=:]\s*\S+)",
    re.IGNORECASE,
)
_STACK_MARKERS = ("Traceback (most recent call last):", 'File "', "raise ", "  ^^^")


def sanitize_error_text(text: str, *, limit: int = 200) -> str:
    """把任意错误文本收敛为可回给模型/用户的安全摘要：
    掐断堆栈、抹掉密钥形态的串、限长。"""
    if not text:
        return "工具执行失败"
    lines = [ln for ln in str(text).splitlines() if ln.strip()]
    # Traceback 开头 → 保留最后一条非框架行（通常是异常类型+消息）
    if any("Traceback (most recent call last):" in ln for ln in lines):
        lines = [ln for ln in lines if not ln.startswith(" ")
                 and 'File "' not in ln and "Traceback" not in ln
                 and "raise " not in ln and "^^^" not in ln]
        text = lines[-1] if lines else ""
    else:
        text = lines[0] if lines else str(text)
    text = _SECRET_RE.sub("[已隐去]", text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text or "工具执行失败"


def structured_tool_error(*, kind: str, name: str, detail: str = "") -> str:
    """给模型的单条结构化错误（kind: schema/permission/budget/breaker/provider）。"""
    detail = sanitize_error_text(detail)
    return f"[工具错误 {kind}] {name}：{detail or '无法执行'}"


class ToolLoopGuard:
    """单轮工具循环的熔断与预算记账（每次 run_tool_loop 实例化一个）。"""

    def __init__(self) -> None:
        self._signatures: dict[str, int] = {}
        self.result_bytes = 0
        self.started = time.monotonic()

    def check_signature(self, name: str, args: dict) -> str | None:
        """同签名第 2 次返回熔断错误文本；否则计数并返回 None。"""
        fingerprint = name + ":" + json.dumps(
            args, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        count = self._signatures.get(fingerprint, 0) + 1
        self._signatures[fingerprint] = count
        if count >= SAME_SIGNATURE_LIMIT:
            return structured_tool_error(
                kind="breaker", name=name,
                detail=f"重复调用已被熔断（同一签名已执行 {count - 1} 次）",
            )
        return None

    def check_budget(self, name: str) -> str | None:
        if self.result_bytes >= TOTAL_RESULT_BUDGET:
            return structured_tool_error(
                kind="budget", name=name,
                detail=f"工具结果总预算已用尽（{self.result_bytes} 字节）",
            )
        return None

    def record_result(self, body: str) -> None:
        self.result_bytes += len(body.encode("utf-8", errors="replace"))

    @property
    def elapsed_ms(self) -> int:
        """单调时钟真实耗时（不用模型报时）。"""
        return int((time.monotonic() - self.started) * 1000)
