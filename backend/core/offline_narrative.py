"""久别后的离线叙事上下文。

只负责从已有对话与结构化记忆里整理“可用素材”和叙事模式，不调用 LLM，
也不把生成内容写回记忆。greeting / initiative 可把产物作为受约束的提示注入。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable


_MAX_RECENT_LINES = 3
_MAX_TRIPLES = 2
_MAX_ITEM_CHARS = 100
_NOISE_RE = re.compile(r"\[图片[^\]]*\]|\x00[A-Z]+\x00")


@dataclass(frozen=True)
class OfflineNarrativeContext:
    mode: str
    gap_hours: float
    recent_lines: tuple[str, ...]
    triple_lines: tuple[str, ...]

    def prompt_hint(self, stage: str) -> str:
        """转成一段有事实边界的生成提示。"""
        mode_hint = {
            "dream": (
                "如果衔接自然，可以随口提一句昨晚梦到上次聊的事，梦可以有一点荒诞；"
                "这只是你的梦，不能说成对方现实里做过的事。"
            ),
            "research": (
                "可以随口提一句离线时在研究所读东西、整理观察或琢磨问题；"
                "不要虚构具体论文标题、作者、数据或现实新闻。"
            ),
            "small_life": (
                "可以随口分享一个很小的离线生活片段或刚冒出的念头，"
                "像真的各自生活后又碰面，不要写成小说。"
            ),
        }[self.mode]
        evidence = list(self.recent_lines) + list(self.triple_lines)
        evidence_text = "\n".join(f"- {line}" for line in evidence) if evidence else "- 没有可靠的共同记忆素材"
        distance = (
            "当前还在初识/熟悉阶段，不要用梦或回忆制造暧昧，只能分享轻微的研究/生活碎片。"
            if stage in {"初识", "熟悉"}
            else "可以有一点在意，但仍保持克制，不要突然煽情或黏人。"
        )
        return (
            "这是一次久别后的主动开场。让离线时间像真的流逝过，但最多用一两句带过。\n"
            f"叙事方向：{mode_hint}\n"
            f"关系分寸：{distance}\n"
            "可参考的既有记忆（只作背景，禁止逐条复述）：\n"
            f"{evidence_text}\n"
            "只可引用这些素材；没有可靠素材就讲你自己的无外部后果的小事。"
            "不要捏造对方说过/做过的事，不要声称真的查阅了某篇具体论文，不要解释这是离线叙事。"
        )


def choose_mode(gap_hours: float, recent_rows: list[Any], *, now: datetime | None = None) -> str:
    """按久别长度与上次聊天时段选叙事模式。"""
    now = now or datetime.now()
    late_exchange_count = 0
    for row in recent_rows:
        ts = _field(row, "ts")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(str(ts))
        except ValueError:
            continue
        if dt.hour >= 22 or dt.hour < 4:
            late_exchange_count += 1
    # 只在亲密阶段由调用方真正采用 dream；这里要求前一晚有至少四条深夜往来，
    # 避免仅一条深夜消息就反复“梦到你”。
    if 6 <= gap_hours <= 36 and late_exchange_count >= 4:
        return "dream"
    if gap_hours >= 18:
        return "research"
    return "small_life"


def collect_offline_context(
    user_id: str,
    gap_hours: float,
    *,
    db_obj=None,
    triple_query: Callable[..., list[tuple[str, str, str, str, str]]] | None = None,
    now: datetime | None = None,
) -> OfflineNarrativeContext:
    """收集近期对话和相关三元组；任何记忆层故障都退化为空素材。"""
    if db_obj is None:
        from .userdb import db as db_obj
    rows: list[Any] = []
    try:
        rows = list(db_obj.recent_messages_with_ids(user_id, 12))
    except Exception:
        rows = []

    recent_lines: list[str] = []
    recent_user_texts: list[str] = []
    for row in reversed(rows):
        role = str(_field(row, "role") or "")
        content = _clean_content(str(_field(row, "content") or ""))
        if role not in {"user", "assistant"} or len(content) < 4:
            continue
        if role == "user":
            recent_user_texts.append(content)
        label = "对方提过" if role == "user" else "你们上次聊到"
        line = f"{label}：{content}"
        if line not in recent_lines:
            recent_lines.append(line)
        if len(recent_lines) >= _MAX_RECENT_LINES:
            break
    recent_lines.reverse()

    triple_lines: list[str] = []
    if triple_query is None:
        try:
            from .triple_memory import query_triples as triple_query
        except Exception:
            triple_query = None
    if triple_query and recent_user_texts:
        query = " ".join(reversed(recent_user_texts[:3]))[:300]
        try:
            triples = triple_query(user_id, query, top_k=_MAX_TRIPLES)
        except Exception:
            triples = []
        for subject, _subject_type, predicate, obj, _object_type in triples[:_MAX_TRIPLES]:
            line = _clean_content(f"{subject}{predicate}{obj}")
            if line:
                triple_lines.append("记忆事实：" + line)

    return OfflineNarrativeContext(
        mode=choose_mode(gap_hours, rows, now=now),
        gap_hours=max(0.0, gap_hours),
        recent_lines=tuple(recent_lines),
        triple_lines=tuple(triple_lines),
    )


def _field(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, None)


def _clean_content(text: str) -> str:
    text = _NOISE_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:_MAX_ITEM_CHARS]

