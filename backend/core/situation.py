# -*- coding: utf-8 -*-
"""D9 局势档案：≤2.5k tokens 的恒定世界快照，每轮注入，钩子永不漏召。

契约（docs/D9-D12-DESIGN-2026-09-21.md §3，2026-09-21 拍板：事件驱动+每10轮）：

- 六节状态：活跃目标 / 未兑现约定 / 悬念与开放问题 / 近期关键事件（近 7 条
  详 + 更早压缩段）/ 当前生活与场景 / 关系焦点；
- **除「更早事件压缩段」外全部由 SQL 确定性重建**——LLM 只做旧事件的叙事
  压缩，漂移面被压到最小；任何 LLM 失败保旧压缩、确定性节照常刷新；
- 增量更新在 turn 副作用里后台调度（新关系事件到达或 ≥10 轮未更新才真正
  调 LLM），受 D10 check("situation") 门控，绝不阻塞回复；
- 每日重编译（run_daily_batch 尾部）：纯 SQL 部分无成本不过闸，压缩段再生
  走闸；失败保旧；
- 注入是常驻 system 块（人格之后、记忆召回之前），受 situation_enabled
  flag 控制，关闭时整层静默退场；
- 表为派生态：进 reset 双清单、**不进关系包导出**（恢复后由重编译重建）。
"""
from __future__ import annotations

import json
from datetime import datetime

from .log import logger

FORMAT_VERSION = 1
RECENT_EVENT_WINDOW = 7          # 近期事件保留详情的条数
OLDER_EVENT_SPAN = 30            # 压缩段覆盖的更早事件范围（近 8..30 条）
TURN_UPDATE_INTERVAL = 10        # 无事件时每 N 轮强制刷新一次
DEFAULT_CHAR_BUDGET = 5000       # 注入文本字符预算（≈2.5k tokens CJK）
_MAX_COMPRESSED_CHARS = 300

_SECTION_TITLES = {
    "goals": "你们正在一起做的事",
    "promises": "还开着的约定",
    "questions": "她惦记的没解开的疑问",
    "events": "最近发生的事",
    "life": "她此刻的生活",
    "focus": "关系里的当下焦点",
}


# ---------------------------------------------------------------------------
# 确定性数据面（全部 SQL，无 LLM）
# ---------------------------------------------------------------------------

def _active_goals(user_id: str) -> list[str]:
    from .goals import list_goals

    lines = []
    for g in list_goals(user_id, limit=10):
        if g.get("status") not in ("active", "paused"):
            continue
        title = str(g.get("title") or "").strip()
        step = str(g.get("next_step") or "").strip()
        state = "进行中" if g.get("status") == "active" else "暂停中"
        if title:
            lines.append(
                f"- {title}（{state}）" + (f"：下一步 {step}" if step else "")
            )
    return lines[:5]


def _open_promises(user_id: str) -> list[str]:
    from .userdb import db

    with db._lock:
        rows = db.conn.execute(
            "SELECT content, follow_up FROM promises "
            "WHERE user_id = ? AND status = 'open' ORDER BY id DESC LIMIT 5",
            (user_id,),
        ).fetchall()
    lines = []
    for r in rows:
        content = str(r["content"] or "").strip()
        due = str(r["follow_up"] or "").strip()
        if content:
            lines.append(f"- {content}" + (f"（说好 {due} 前后）" if due else ""))
    return lines


def _open_question_lines(user_id: str) -> list[str]:
    from .open_questions import list_questions

    lines = []
    for q in list_questions(user_id, limit=10):
        if str(q.get("status") or "") not in ("open", "researching"):
            continue
        topic = str(q.get("topic") or "").strip()
        if topic:
            lines.append(f"- {topic}（还没答案）")
    return lines[:4]


def _recent_events(user_id: str, *, limit: int = RECENT_EVENT_WINDOW) -> list[dict]:
    """最近 N 条有效关系事件（新→旧）。payload 只取 title/summary 类短字段。"""
    from .userdb import db

    with db._lock:
        rows = db.conn.execute(
            "SELECT id, event_type, subject, object, payload_json, occurred_at "
            "FROM relationship_events WHERE user_id = ? "
            "AND status = 'active' AND (expires_at IS NULL OR expires_at > ?) "
            "ORDER BY id DESC LIMIT ?",
            (user_id, datetime.now().isoformat(timespec="seconds"), int(limit)),
        ).fetchall()
    events = []
    for r in rows:
        try:
            payload = json.loads(r["payload_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        brief = str(
            payload.get("title") or payload.get("summary") or payload.get("topic") or ""
        ).strip()
        subject = str(r["subject"] or "").strip()
        obj = str(r["object"] or "").strip()
        text = brief or "、".join(p for p in (subject, obj) if p) or str(r["event_type"])
        events.append({
            "id": int(r["id"]),
            "type": str(r["event_type"]),
            "text": text[:80],
            "at": str(r["occurred_at"] or "")[:10],
        })
    return events


def _older_event_count(user_id: str) -> int:
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT COUNT(*) FROM relationship_events WHERE user_id = ? "
            "AND status = 'active' AND (expires_at IS NULL OR expires_at > ?) "
            "AND id NOT IN (SELECT id FROM relationship_events WHERE user_id = ? "
            " AND status = 'active' AND (expires_at IS NULL OR expires_at > ?) "
            " ORDER BY id DESC LIMIT ?)",
            (user_id, datetime.now().isoformat(timespec="seconds"), user_id,
             datetime.now().isoformat(timespec="seconds"), RECENT_EVENT_WINDOW),
        ).fetchone()
    return int(row[0] if row else 0)


def _life_line(user_id: str) -> str:
    from .presence import current_presence

    try:
        from .seasons import current_season
        from .state import load_state

        s = load_state(user_id, create_if_missing=False)
        season = current_season(user_id, s)
        return (
            f"她此刻：{current_presence(user_id)}；心情 {s.mood}，精力 {s.energy}；"
            f"关系氛围「{season['label']}」"
        )
    except Exception:
        return f"她此刻：{current_presence(user_id)}"


def _focus_line(user_id: str) -> str:
    """关系当下焦点：近 7 天里程碑/修复/庆祝各取最新一条的确定性映射。"""
    from .userdb import db

    wanted = ("goal_completed", "promise_completed", "important_date",
              "memory_corrected", "reading_finished")
    with db._lock:
        rows = db.conn.execute(
            "SELECT event_type, payload_json, occurred_at FROM relationship_events "
            "WHERE user_id = ? AND status = 'active' AND event_type IN "
            f"({','.join('?' * len(wanted))}) "
            "AND (expires_at IS NULL OR expires_at > ?) "
            "ORDER BY id DESC LIMIT 3",
            (user_id, *wanted, datetime.now().isoformat(timespec="seconds")),
        ).fetchall()
    lines = []
    for r in rows:
        try:
            payload = json.loads(r["payload_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        brief = str(payload.get("title") or payload.get("summary") or "").strip()
        if brief:
            lines.append(f"- {brief}（{str(r['occurred_at'] or '')[:10]}）")
    return lines


def rebuild_sections(user_id: str) -> dict:
    """从 SQLite 权威数据确定性重建六节（不含 LLM 压缩段）。"""
    return {
        "goals": _active_goals(user_id),
        "promises": _open_promises(user_id),
        "questions": _open_question_lines(user_id),
        "events_recent": _recent_events(user_id),
        "older_event_count": _older_event_count(user_id),
        "life": _life_line(user_id),
        "focus": _focus_line(user_id),
    }


# ---------------------------------------------------------------------------
# 档案存取（schema v43 situation_files）
# ---------------------------------------------------------------------------

def get_file(user_id: str) -> dict | None:
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT * FROM situation_files WHERE user_id = ?", (user_id,)
        ).fetchone()
    if row is None:
        return None
    try:
        state = json.loads(row["state_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        state = {}
    return {
        "state": state,
        "sections": state.get("sections") or {},
        "compressed_older": str(state.get("compressed_older") or ""),
        "last_event_id": int(state.get("last_event_id") or row["last_event_id"] or 0),
        "turns_since_update": int(row["turn_count"] or 0),
        "char_budget": int(row["char_budget"] or DEFAULT_CHAR_BUDGET),
        "updated_at": str(row["updated_at"] or ""),
    }


def _save_file(
    user_id: str, sections: dict, compressed: str, *, last_event_id: int,
    reset_turns: bool = True, mark_recompiled: bool = False,
) -> None:
    from .userdb import db

    now = datetime.now().isoformat(timespec="seconds")
    state = {
        "format_version": FORMAT_VERSION,
        "sections": sections,
        "compressed_older": compressed,
        "last_event_id": int(last_event_id),
    }
    with db._lock:
        db.conn.execute(
            "INSERT INTO situation_files "
            "(user_id, format_version, state_json, char_budget, last_event_id, "
            " turn_count, updated_at, recompiled_at) "
            "VALUES (:uid, :fv, :state, :budget, :last_id, 0, :now, "
            "        CASE WHEN :mark = 1 THEN :now ELSE NULL END) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "format_version = :fv, state_json = :state, last_event_id = :last_id, "
            "turn_count = CASE WHEN :reset = 1 THEN 0 ELSE situation_files.turn_count END, "
            "updated_at = :now, "
            "recompiled_at = CASE WHEN :mark = 1 THEN :now "
            "                     ELSE situation_files.recompiled_at END",
            {
                "uid": user_id,
                "fv": FORMAT_VERSION,
                "state": json.dumps(state, ensure_ascii=False, sort_keys=True),
                "budget": DEFAULT_CHAR_BUDGET,
                "last_id": int(last_event_id),
                "reset": 1 if reset_turns else 0,
                "mark": 1 if mark_recompiled else 0,
                "now": now,
            },
        )
        db.conn.commit()


def _newest_event_id(user_id: str) -> int:
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM relationship_events WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return int(row[0] if row else 0)


# ---------------------------------------------------------------------------
# 增量更新与每日重编译
# ---------------------------------------------------------------------------

_COMPRESS_PROMPT = (
    "你在为一个人的长期记忆做压缩。下面是某段关系里较早发生的一些事件"
    "（都是真实记录）。请把它们压缩成最多 3 行中文短句，每行一个主题，"
    "只保留对这段关系仍然要紧的事，不编造、不评价、不写数字编号。"
    "直接输出短句列表，一行一句，不要任何解释。"
)


async def _compress_older(user_id: str, previous: str) -> str:
    """LLM 压缩更早事件（唯一允许 LLM 碰的一节）。失败抛异常由调用方保旧。"""
    older = _recent_events(user_id, limit=OLDER_EVENT_SPAN)[RECENT_EVENT_WINDOW:]
    if not older:
        return ""
    material = "\n".join(f"- [{e['at']}] {e['text']}" for e in older)
    if previous:
        material = f"[既有压缩段，可沿用可改写]\n{previous}\n\n[更早事件原文]\n{material}"
    from .llm import chat

    resp = await chat(
        [
            {"role": "system", "content": _COMPRESS_PROMPT},
            {"role": "user", "content": material},
        ],
        task="batch_other",
        temperature=0.2,
        max_tokens=400,
    )
    lines = [
        ln.strip().lstrip("-•· ").strip()
        for ln in (resp or "").splitlines() if ln.strip()
    ][:3]
    return "\n".join(ln for ln in lines if ln)[:_MAX_COMPRESSED_CHARS]


async def update_after_turn(user_id: str) -> dict:
    """turn 副作用后台调度入口：判定是否需要更新并落新档案。

    需要 = 有新关系事件（id > last_event_id）或 ≥10 轮未更新。
    LLM 只在需要时调用且受 D10 门控；任何失败都保旧（确定性节可先刷新）。
    """
    current = get_file(user_id)
    sections = rebuild_sections(user_id)
    newest = _newest_event_id(user_id)
    last_seen = current["last_event_id"] if current else 0
    turns = current["turns_since_update"] if current else TURN_UPDATE_INTERVAL
    if current is None or newest > last_seen or turns >= TURN_UPDATE_INTERVAL:
        compressed = current["compressed_older"] if current else ""
        llm_used = False
        try:
            from .cost_guard import check as _cost_ok

            if _cost_ok("situation"):
                compressed = await _compress_older(user_id, compressed)
                llm_used = True
        except Exception as exc:
            logger.warning("[局势档案] {} 压缩段更新失败，保旧: {}", user_id, type(exc).__name__)
        _save_file(user_id, sections, compressed, last_event_id=newest)
        return {"updated": True, "llm_used": llm_used, "new_events": newest - last_seen}
    # 无需 LLM 更新：只推进轮计数（供下次判定）
    from .userdb import db

    with db._lock:
        db.conn.execute(
            "UPDATE situation_files SET turn_count = turn_count + 1 WHERE user_id = ?",
            (user_id,),
        )
        db.conn.commit()
    return {"updated": False, "llm_used": False, "new_events": 0}


def recompile(user_id: str) -> dict:
    """每日批处理尾部调用：确定性全量重建（零 LLM，无成本不过闸）。

    压缩段由 update_after_turn 的 LLM 路径负责再生；此处保持现有压缩不动，
    只保证六节与 last_event_id 和 SQLite 一致（漂移兜底）。
    """
    sections = rebuild_sections(user_id)
    current = get_file(user_id)
    compressed = current["compressed_older"] if current else ""
    _save_file(
        user_id, sections, compressed,
        last_event_id=_newest_event_id(user_id),
        reset_turns=True, mark_recompiled=True,
    )
    return {"recompiled": True}


# ---------------------------------------------------------------------------
# 注入与展示
# ---------------------------------------------------------------------------

def _render(sections: dict, compressed: str, *, char_budget: int) -> str:
    parts: list[str] = []
    recent = sections.get("events_recent") or []
    older_count = int(sections.get("older_event_count") or 0)
    lines = [
        f"- {e['text']}（{e['at']}）" for e in recent
    ]
    if compressed:
        lines.append(f"（更早的事）{compressed}")
    elif older_count > 0:
        lines.append(f"（更早还有 {older_count} 件事，没展开）")
    blocks = [
        (_SECTION_TITLES["life"], [str(sections.get("life") or "")] if sections.get("life") else []),
        (_SECTION_TITLES["goals"], sections.get("goals") or []),
        (_SECTION_TITLES["promises"], sections.get("promises") or []),
        (_SECTION_TITLES["questions"], sections.get("questions") or []),
        (_SECTION_TITLES["events"], lines),
        (_SECTION_TITLES["focus"], sections.get("focus") or []),
    ]
    for title, rows in blocks:
        rows = [r for r in rows if r]
        if rows:
            parts.append(f"{title}：" + " ".join(rows))
    text = "\n".join(parts)
    # 预算裁剪：超长时先丢压缩段重拼，再硬截断（保头保尾各半）
    if len(text) > char_budget and compressed:
        text = _render(sections, "", char_budget=char_budget)
    if len(text) > char_budget:
        keep = char_budget - 1
        text = text[: keep // 2] + "…" + text[-keep // 2:]
    return text


def situation_context(user_id: str) -> str:
    """常驻注入块（flag 门控）。档案缺失时现场确定性重建一份（零 LLM）。"""
    try:
        from .features import flag

        if not flag("situation_enabled"):
            return ""
    except Exception:
        return ""
    try:
        current = get_file(user_id)
        if current is None:
            recompile(user_id)
            current = get_file(user_id)
        if current is None:
            return ""
        body = _render(
            current["sections"], current["compressed_older"],
            char_budget=current["char_budget"],
        )
        if not body:
            return ""
        return "[局势档案] 以下是你们当下的世界快照（真实记录汇编，供你自然参考）：\n" + body
    except Exception:
        logger.exception("[局势档案] {} 注入渲染失败，本轮跳过", user_id)
        return ""


def debug_view(user_id: str) -> dict:
    """GET /api/situation：档案本体 + 注入预览（诊断用）。"""
    current = get_file(user_id)
    if current is None:
        recompile(user_id)
        current = get_file(user_id)
    return {
        "ok": True,
        "format_version": FORMAT_VERSION,
        "updated_at": current["updated_at"] if current else "",
        "sections": current["sections"] if current else {},
        "compressed_older": current["compressed_older"] if current else "",
        "prompt_preview": situation_context(user_id),
    }


__all__ = [
    "FORMAT_VERSION",
    "TURN_UPDATE_INTERVAL",
    "debug_view",
    "get_file",
    "recompile",
    "rebuild_sections",
    "situation_context",
    "update_after_turn",
]
