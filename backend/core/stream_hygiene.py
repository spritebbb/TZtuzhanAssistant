"""流式回复的增量卫生出口。

模型片段先聚合为完整句段；只有已闭合、通过硬规则扫描的句段才会提前展示。
最终候选经过插件与完整卫生检查后再对齐，保证前端正文与持久化正文一致。
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from .output_hygiene import HygieneContext, remove_closed_reasoning, scan_rules


_RESET_MARK = "\x00RESET\x00"
_CHUNK_SIZE = 6
_REASONING_PREFIX_RE = re.compile(
    r"<\s*/?\s*(?:t(?:h(?:i(?:n(?:k(?:i(?:n(?:g)?)?)?)?)?)?)?|"
    r"r(?:e(?:a(?:s(?:o(?:n(?:i(?:n(?:g)?)?)?)?)?)?)?)?|"
    r"a(?:n(?:a(?:l(?:y(?:s(?:i(?:s)?)?)?)?)?)?)?)\s*$",
    re.IGNORECASE,
)


def _complete_unit_end(text: str) -> int:
    """返回首个可放行句段的结束位置；Markdown 围栏内不切分。"""
    fence: str | None = None
    i = 0
    while i < len(text):
        marker = text[i:i + 3]
        if marker in ("```", "~~~"):
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            i += 3
            continue
        if fence is None:
            ch = text[i]
            if ch in "。！？；":
                return i + 1
            if ch == "…":
                return i + 2 if text[i:i + 2] == "……" else i + 1
            if ch in ".?!" and (i + 1 == len(text) or text[i + 1].isspace()):
                return i + 1
            if text.startswith("\n\n", i):
                return i + 2
        i += 1
    return 0


def _has_ambiguous_tail(text: str) -> bool:
    """保留可能在下一 chunk 组成内部标签的尾部。"""
    tail = text[-40:]
    if _REASONING_PREFIX_RE.search(tail):
        return True
    # 任意未闭合尖括号都延迟到下一块；普通比较表达式只会降低流式粒度。
    return tail.rfind("<") > tail.rfind(">")


class IncrementalHygieneStream:
    """按安全句段发送模型流，并用最终正文完成一致性对齐。"""

    def __init__(
        self,
        emit: Callable[[str], Awaitable[None]] | None,
        *,
        context: HygieneContext,
        enabled: bool = True,
    ) -> None:
        self._emit = emit
        self._context = context
        self._enabled = bool(enabled and emit is not None)
        self._buffer = ""
        self._emitted = ""
        self._blocked = False

    @property
    def emitted_text(self) -> str:
        return self._emitted

    async def _send(self, text: str) -> None:
        if not self._enabled or not text or self._emit is None:
            return
        try:
            await self._emit(text)
        except Exception:
            self._enabled = False
            return
        self._emitted += text

    async def _send_chunks(self, text: str) -> None:
        for i in range(0, len(text), _CHUNK_SIZE):
            await self._send(text[i:i + _CHUNK_SIZE])

    async def feed(self, piece: str) -> None:
        if not self._enabled or self._blocked or not piece:
            return
        self._buffer += piece

        # 完整闭合的隐藏推理可以直接剔除；残缺标签继续留在缓冲区。
        self._buffer = remove_closed_reasoning(self._buffer)
        if scan_rules(self._buffer, context=self._context):
            self._blocked = True
            return

        while True:
            end = _complete_unit_end(self._buffer)
            if not end:
                return
            unit = self._buffer[:end]
            if _has_ambiguous_tail(unit):
                return
            if scan_rules(unit, context=self._context):
                self._blocked = True
                return
            self._buffer = self._buffer[end:]
            await self._send_chunks(unit)

    async def finish(self, final_text: str) -> None:
        """让已展示内容与最终权威正文完全一致。"""
        final_text = final_text or ""
        if not self._enabled:
            return
        if final_text.startswith(self._emitted):
            await self._send_chunks(final_text[len(self._emitted):])
            return
        if self._emitted:
            # RESET 不属于正文，不能计入 emitted_text。
            emitted = self._emitted
            self._emitted = ""
            try:
                if self._emit is not None:
                    await self._emit(_RESET_MARK)
            except Exception:
                self._enabled = False
                self._emitted = emitted
                return
        await self._send_chunks(final_text)
