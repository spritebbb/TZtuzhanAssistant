"""Per-turn privacy semantics shared by the chat API and conversation pipeline."""
from __future__ import annotations

import re


_EPHEMERAL_REQUEST_RE = re.compile(
    r"(?:"
    r"陪我(?:说|聊)(?:完|一会儿|一下)?[^。！？!?]{0,12}(?:但|不过)?(?:别|不要)(?:记|保存|留下)"
    r"|(?:这(?:件事|句话|段话|些话|段|次|轮)(?:对话|聊天)?|刚才(?:这些|这段)?)[^。！？!?]{0,10}(?:别|不要)(?:记住|保存|留下)"
    r"|(?:别|不要)(?:把)?(?:这(?:件事|句话|段话|些话|段对话)|刚才(?:这些|这段)?)(?:记住|保存|留下)"
    r"|(?:临时|无痕)(?:对话|聊天|聊聊|聊一下)"
    r"|(?:这(?:段|次|轮)(?:对话|聊天)?)[^。！？!?]{0,8}不留痕"
    r")"
)


def is_ephemeral_request(text: str, *, explicit: bool = False) -> bool:
    """Return whether this single turn must leave no conversational or memory trace.

    ``explicit`` is set by the UI's one-shot privacy switch. Natural-language
    detection intentionally requires a complete phrase instead of matching a
    bare “别记”, avoiding false positives such as “别忘记提醒我”.
    """
    return bool(explicit or _EPHEMERAL_REQUEST_RE.search(text.strip()))


def ephemeral_prompt() -> str:
    """Instruction shown to the model for a no-trace turn."""
    return (
        "这是一次由对方明确开启的临时对话。本轮内容不会进入会话历史、长期记忆、"
        "画像、日记、关系状态或主动回访。正常陪对方把话说完，不要声称已经记住；"
        "不要调用会新增、修改或删除持久数据的工具。"
    )


_SENSITIVE_PATTERNS = (
    (re.compile(r"(?i)\bsk-(?:proj-)?[A-Za-z0-9_-]{8,}\b"), "[API_KEY已隐去]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}"), "Bearer [TOKEN已隐去]"),
    (re.compile(r"(?i)\b(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|cookie|set-cookie)\s*[:=]\s*[^\s,;]+"), "[凭据已隐去]"),
    (re.compile(r"(?i)(?:恢复口令|recovery[_ -]?(?:code|phrase)|passphrase)\s*[:=：]\s*[^\s,;]+"), "[恢复材料已隐去]"),
    (re.compile(r"(?i)(?:[A-Za-z]:\\|/)(?:[^\s\"'<>]+[\\/])*[^\s\"'<>]+\.(?:png|jpe?g|gif|webp|mp3|wav|mp4)\b"), "[媒体路径已隐去]"),
)


def redact_sensitive(value: object, *, limit: int | None = None) -> str:
    """移除日志与错误摘要中的凭据、恢复材料和本地媒体路径。"""
    text = str(value or "")
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    if limit is not None and len(text) > limit:
        text = text[: max(0, limit - 3)] + "..."
    return text


def redact_log_record(record: dict) -> bool:
    """loguru filter：在进入任一 sink 前净化消息和异常，避免堆栈旁路泄密。"""
    message = redact_sensitive(record.get("message", ""))
    exception = record.get("exception")
    if exception is not None:
        exc_type = getattr(exception, "type", None)
        exc_value = getattr(exception, "value", None)
        type_name = getattr(exc_type, "__name__", "Exception")
        safe_exception = redact_sensitive(f"{type_name}: {exc_value}", limit=300)
        message += f" | exception={safe_exception}"
        # Loguru 默认会在 message 格式化后追加完整 traceback；清空以阻断旁路。
        record["exception"] = None
    record["message"] = message
    return True
