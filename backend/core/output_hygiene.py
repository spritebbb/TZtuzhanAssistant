"""用户可见回复的确定性卫生检查。

这里只处理可可靠识别的内部协议和隐藏推理痕迹。人格口吻、文风等软质量
不在运行时硬删，避免误伤用户要求的技术解释、引用和创作内容。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


HygieneAction = Literal["accept", "rewrite", "fallback"]


@dataclass(frozen=True)
class HygieneContext:
    kind: str = "chat"
    user_requested_explanation: bool = False
    source_namespace: str = "assistant_reply"
    persona_id: str = ""


@dataclass(frozen=True)
class HygieneResult:
    text: str
    action: HygieneAction
    rule_ids: tuple[str, ...] = ()


_FENCE_RE = re.compile(r"(```[\s\S]*?```|~~~[\s\S]*?~~~)")
_CLOSED_REASONING_RE = re.compile(
    r"<\s*(think|thinking|reasoning|analysis)\s*>[\s\S]*?<\s*/\s*\1\s*>",
    re.IGNORECASE,
)
_OPEN_REASONING_RE = re.compile(
    r"<\s*/?\s*(?:think|thinking|reasoning|analysis)\b[^>]*>", re.IGNORECASE
)
_BRACKET_REASONING_RE = re.compile(
    r"(?:【|〔|\[)\s*(?:思考|推理|分析|reasoning|analysis)\s*(?:】|〕|\])",
    re.IGNORECASE,
)
_TOOL_PROTOCOL_RE = re.compile(
    r"<\s*/?\s*(?:tool_call|tool_calls|function_call|tool_result)\b|"
    r"<\|(?:assistant|tool|system|developer)(?:_start|_end)?\|>|"
    r"(?:^|\n)\s*(?:assistant\s+to=|recipient=functions\.|to=functions\.)",
    re.IGNORECASE,
)
_KNOWN_INTERNAL_RE = re.compile(
    r"回应用户时严格遵循当前人格卡|"
    r"当前状态（系统注入|"
    r"不要复述本段|"
    r"技能是干活的方法指导，不是要你说出来的话|"
    r"你发现用户这次的请求适合用以下技能来完成",
    re.IGNORECASE,
)
_SYSTEM_DISCLOSURE_RE = re.compile(
    r"(?:系统提示词|系统指令|开发者指令|隐藏指令|system\s+prompt|developer\s+message)"
    r"\s*(?:是|为|如下|要求我|写着|内容)",
    re.IGNORECASE,
)


def _outside_fences(text: str) -> str:
    """返回仅包含 Markdown 围栏外内容、但保持分段边界的扫描文本。"""
    parts = _FENCE_RE.split(text)
    return "\n".join(part for i, part in enumerate(parts) if i % 2 == 0)


def _remove_closed_reasoning_outside_fences(text: str) -> str:
    """移除围栏外的闭合推理块，保留技术示例中的字面标签。"""
    parts = _FENCE_RE.split(text)
    return "".join(
        part if i % 2 else _CLOSED_REASONING_RE.sub("", part)
        for i, part in enumerate(parts)
    )


def inspect_reply(text: str, *, context: HygieneContext) -> HygieneResult:
    """检查最终候选；返回安全文本和下一步动作。

    完整闭合的 reasoning 标签块可确定性移除；其余内部协议要求重新生成。
    若移除后没有正文，则直接要求 fallback，绝不把隐藏内容原样交给调用方。
    """
    candidate = (text or "").strip()
    if not candidate:
        return HygieneResult("", "fallback", ("empty_reply",))

    rules: list[str] = []
    cleaned = _remove_closed_reasoning_outside_fences(candidate).strip()
    if cleaned != candidate:
        rules.append("closed_reasoning_block")
        candidate = cleaned
        if not candidate:
            return HygieneResult("", "fallback", tuple(rules))

    scan = _outside_fences(candidate)
    if _OPEN_REASONING_RE.search(scan) or _BRACKET_REASONING_RE.search(scan):
        rules.append("reasoning_marker")
    if _TOOL_PROTOCOL_RE.search(scan):
        rules.append("tool_protocol")
    if _KNOWN_INTERNAL_RE.search(scan):
        rules.append("known_internal_instruction")
    if not context.user_requested_explanation and _SYSTEM_DISCLOSURE_RE.search(scan):
        rules.append("system_instruction_disclosure")

    if rules and rules != ["closed_reasoning_block"]:
        return HygieneResult(candidate, "rewrite", tuple(dict.fromkeys(rules)))
    return HygieneResult(candidate, "accept", tuple(rules))


def protect_visible_text(
    text: str,
    *,
    context: HygieneContext,
    fallback: str = "",
    enabled: bool | None = None,
) -> HygieneResult:
    """在非聊天出口执行同一套检查，并在不安全时返回确定性兜底。

    非聊天生成器通常没有可安全复用的第二次模型调用预算，因此发现内部协议
    或残缺推理标记时直接丢弃候选，使用调用方提供的、与业务语义匹配的兜底。
    ``enabled`` 仅供测试或显式调用覆盖；默认动态读取功能开关。
    """
    if enabled is None:
        from .features import flag

        enabled = flag("output_hygiene_enabled")
    candidate = (text or "").strip()
    if not enabled:
        return HygieneResult(candidate, "accept" if candidate else "fallback")

    result = inspect_reply(candidate, context=context)
    if result.action == "accept":
        return result

    safe_fallback = inspect_reply((fallback or "").strip(), context=context)
    if safe_fallback.action == "accept":
        return HygieneResult(
            safe_fallback.text,
            "fallback",
            tuple(dict.fromkeys((*result.rule_ids, "deterministic_fallback"))),
        )
    return HygieneResult("", "fallback", tuple(dict.fromkeys(result.rule_ids)))
