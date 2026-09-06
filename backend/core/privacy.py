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
