# -*- coding: utf-8 -*-
"""M5 未完成心事（pending_thoughts）：想问但时机不对的事，等一个自然的时机。

设计约束（docs/TECH-PLAN.md M2/M5）：
- 只携带叙事素材（她惦记的事），不携带可执行指令。
- 每条心事可追溯来源（activity / fact），来源消失即作废。
- Narrative Planner 决定"现在表达 / 延迟 / 放弃"：earliest_at 未到不表达、
  尝试次数用尽放弃、过期作废；表达永远走既有主动队列（额度/冷却/勿扰不变）。
- 克制：同一来源的心事一生只挂一次，不反复盘问用户。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .log import logger
from .userdb import db

_THOUGHT_KINDS = ("resume_reading", "confirm_memory", "goal_checkin", "chain_aftermath", "memory_fading", "open_question_result")  # chain_aftermath=P2-04 链式回望（event_chains 产出）；memory_fading=G01 可见遗忘到期前候选；open_question_result=G03 待查有结果
_PAUSED_READING_DAYS = 3
_CONFIRM_MEMORY_HOURS = 2
_THOUGHT_TTL_DAYS = 7
_FADING_WINDOW_DAYS = 3


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _expire_at() -> str:
    return (datetime.now() + timedelta(days=_THOUGHT_TTL_DAYS)).isoformat(timespec="seconds")


def _add(
    user_id: str,
    kind: str,
    source_type: str,
    source_id: int,
    content: str,
    *,
    earliest_at: str | None = None,
    priority: int = 5,
    commit: bool = True,
) -> int | None:
    """幂等写入：同一来源一生只挂一次心事。返回新挂上的 id，已存在返回 None。"""
    if kind not in _THOUGHT_KINDS:
        raise ValueError(f"未注册的心事类型：{kind}")
    with db._lock:
        cur = db.conn.execute(
            "INSERT OR IGNORE INTO pending_thoughts "
            "(user_id, kind, source_type, source_id, content, earliest_at, expires_at, "
            "priority, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, kind, source_type, int(source_id), content,
             earliest_at or _now(), _expire_at(), int(priority), _now()),
        )
        if commit:
            db.conn.commit()
    return int(cur.lastrowid) if cur.rowcount else None


def sync_pending_thoughts(user_id: str) -> int:
    """从真实信号同步心事（确定性生产者，幂等），返回新增条数。

    - resume_reading：有共读搁置超过 3 天 → 她惦记那本书。
    - confirm_memory：一天内纠偏过记忆 → 她想找时机确认现在记对了没。
    - memory_fading：有短期记忆即将到期 → 她想趁还记得再聊一次（G01 可见遗忘，
      只取非敏感元数据，不含事实原文；到期后不靠墓碑复原）。
    """
    added = 0
    now = _now()
    fading: list[dict] = []
    try:
        from .memory_salience import expiring_soon_candidates

        fading = expiring_soon_candidates(user_id, within_days=_FADING_WINDOW_DAYS)[:2]
    except Exception:
        fading = []  # 候选查询失败不影响其余心事同步
    with db._lock:
        paused = db.conn.execute(
            "SELECT a.id, a.updated_at, d.filename FROM activities a "
            "JOIN kb_documents d ON d.id = a.document_id AND d.user_id = a.user_id "
            "WHERE a.user_id = ? AND a.kind = 'reading' AND a.status = 'paused' "
            "AND a.updated_at <= ?",
            (user_id, (datetime.now() - timedelta(days=_PAUSED_READING_DAYS)).isoformat(timespec="seconds")),
        ).fetchall()
        corrected = db.conn.execute(
            "SELECT source_id, payload_json, occurred_at FROM relationship_events "
            "WHERE user_id = ? AND event_type = 'memory_corrected' AND status = 'active' "
            "AND occurred_at >= ?",
            (user_id, (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")),
        ).fetchall()
        due_goals = db.conn.execute(
            "SELECT a.id, a.title, g.next_step, g.reminder_at FROM activities a "
            "JOIN activity_goals g ON g.activity_id = a.id AND g.user_id = a.user_id "
            "WHERE a.user_id = ? AND a.kind = 'goal' AND a.status IN ('active', 'paused') "
            "AND g.support_mode = 'reminder' AND g.reminder_at IS NOT NULL AND g.reminder_at <= ?",
            (user_id, now),
        ).fetchall()
    for row in paused:
        earliest = (
            datetime.fromisoformat(row["updated_at"]) + timedelta(days=_PAUSED_READING_DAYS)
        ).isoformat(timespec="seconds")
        if _add(
            user_id, "resume_reading", "activity", int(row["id"]),
            f"你们有一本读到一半搁下的《{row['filename']}》，她有点想知道后来读到哪儿了",
            earliest_at=earliest, priority=4, commit=False,
        ):
            added += 1
    for row in corrected:
        earliest = (
            datetime.fromisoformat(row["occurred_at"]) + timedelta(hours=_CONFIRM_MEMORY_HOURS)
        ).isoformat(timespec="seconds")
        if _add(
            user_id, "confirm_memory", "fact", int(row["source_id"]),
            "她刚改过一条记你的事，想找个自然的时机确认这次记对了没",
            earliest_at=earliest, priority=3, commit=False,
        ):
            added += 1
    for row in due_goals:
        if _add(
            user_id, "goal_checkin", "activity", int(row["id"]),
            f"对方请你在合适时轻轻问一次共同目标「{row['title']}」；下一小步是「{row['next_step']}」",
            earliest_at=row["reminder_at"], priority=5, commit=False,
        ):
            added += 1
    for item in fading:
        if _add(
            user_id, "memory_fading", "fact", int(item["fact_id"]),
            f"有一条关于你的事快到保留期限了（{item['retention']}），"
            "她想趁还记得的时候再聊一次",
            earliest_at=now, priority=6, commit=False,
        ):
            added += 1
    if added:
        db.conn.commit()
        logger.info("[心事] 挂上 {} 条未完成心事：{}", added, user_id)
    return added


def due_thoughts(user_id: str, limit: int = 3) -> list[dict]:
    """到点、未过期、还有尝试余地的 pending 心事，按优先级排序。"""
    now = _now()
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM pending_thoughts WHERE user_id = ? AND status = 'pending' "
            "AND earliest_at <= ? AND (expires_at IS NULL OR expires_at > ?) "
            "AND attempts < max_attempts "
            "ORDER BY priority, earliest_at, id LIMIT ?",
            (user_id, now, now, max(1, min(10, int(limit)))),
        ).fetchall()
    return [dict(row) for row in rows]


def next_thought_for_stage(user_id: str, stage: str) -> dict | None:
    """Narrative Planner 的阶段门控：初识只允许读书跟进这类非私人化心事。"""
    sync_pending_thoughts(user_id)
    allowed = {"resume_reading"} if stage == "初识" else set(_THOUGHT_KINDS)
    for thought in due_thoughts(user_id):
        if thought["kind"] in allowed:
            return thought
    return None


def record_attempt(thought_id: int, *, commit: bool = True) -> None:
    with db._lock:
        db.conn.execute(
            "UPDATE pending_thoughts SET attempts = attempts + 1, last_attempt_at = ? "
            "WHERE id = ?",
            (_now(), int(thought_id)),
        )
        if commit:
            db.conn.commit()


def mark_expressed(thought_id: int, *, commit: bool = True) -> None:
    with db._lock:
        db.conn.execute(
            "UPDATE pending_thoughts SET status = 'expressed' WHERE id = ?",
            (int(thought_id),),
        )
        if commit:
            db.conn.commit()


def dismiss_thought(user_id: str, thought_id: int) -> bool:
    """用户主权：不想让她惦记这件事，直接放下。"""
    with db._lock:
        cur = db.conn.execute(
            "UPDATE pending_thoughts SET status = 'dismissed' "
            "WHERE user_id = ? AND id = ? AND status = 'pending'",
            (user_id, int(thought_id)),
        )
        db.conn.commit()
    return bool(cur.rowcount)


# ---- F03 心事语境门控（注入到用户发起的一轮，不占后台主动额度） ----

THOUGHT_CONTEXT_MAX_CHARS = 200
_RELEVANCE_MIN = 1          # 关键词二元组命中阈值（与 colists/事实检索同法）
_STAGE_GATE_EARLY = "初识"   # 初识只允许非私人化心事（与 planner 一致）


def _bigrams(text: str) -> set[str]:
    clean = "".join(ch for ch in str(text or "") if ch.isalnum() or "一" <= ch <= "鿿")
    return {clean[i:i + 2] for i in range(len(clean) - 1)} if len(clean) >= 2 else set()


def _source_alive(user_id: str, thought: dict) -> bool:
    """来源必须仍存在（活动/事实/开放问题）；来源消失的候选直接失效。"""
    source_type = str(thought.get("source_type") or "")
    source_id = thought.get("source_id")
    if source_id is None:
        return False
    table = {"activity": "activities", "fact": "facts", "open_question": "open_questions"}.get(source_type)
    if table is None:
        return False
    with db._lock:
        row = db.conn.execute(
            f"SELECT 1 FROM {table} WHERE user_id = ? AND id = ?",
            (user_id, int(source_id)),
        ).fetchone()
    return row is not None


def context_candidates(user_id: str, query: str, turn_id: int, *,
                       ephemeral: bool = False, limit: int = 1) -> list[dict]:
    """本轮可注入的心事候选：到点 + 来源合法 + 阶段允许 + 与话题相关。

    最多 limit 条、单条 200 字；临时轮只读不写回执（不改变心事状态）。
    选中即记 selected 回执（不等于「已表达」——模型无法可靠声明使用时保守处理，
    重复抑制交给 registry 的 4 回合冷却）。
    """
    from .features import flag

    if not flag("context_registry_enabled"):
        return []
    clean_query = str(query or "").strip()
    query_bigrams = _bigrams(clean_query)
    out: list[dict] = []
    for thought in due_thoughts(user_id, limit=5):
        if not _source_alive(user_id, thought):
            continue
        content = str(thought.get("content") or "").strip()
        if not content:
            continue
        if query_bigrams:
            overlap = len(query_bigrams & _bigrams(content))
            if overlap < _RELEVANCE_MIN:
                continue  # 话题不相关就不塞心事
        out.append(thought)
        if len(out) >= max(1, int(limit)):
            break
    if not out:
        return []
    if not ephemeral and turn_id:
        for thought in out:
            try:
                record_receipt(user_id, int(thought["id"]), int(turn_id), status="selected")
            except Exception:
                logger.warning("[心事] 注入回执写入失败 thought={}", thought.get("id"))
    return out


def thought_context_text(thought: dict) -> str:
    """渲染注入文本（≤200 字），保留来源属性与「只在自然时提起」的约束。"""
    content = " ".join(str(thought.get("content") or "").split())
    if len(content) > THOUGHT_CONTEXT_MAX_CHARS:
        content = content[: THOUGHT_CONTEXT_MAX_CHARS - 1] + "…"
    return (
        "你心里一直惦记一件小事：" + content
        + "。只有当对方当下的话确实和这件事相关时，才自然提一句；"
        "不相关就照常回应，别硬扯、别追问、别像提醒事项。"
    )


def record_receipt(user_id: str, thought_id: int, turn_id: int, *,
                   status: str = "selected") -> bool:
    """写一条注入回执（唯一 user/thought/turn；重复调用幂等）。"""
    if status not in {"selected", "committed"}:
        raise ValueError(f"未知回执状态：{status}")
    with db._lock:
        cur = db.conn.execute(
            "INSERT OR IGNORE INTO thought_context_receipts "
            "(user_id, thought_id, turn_id, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, int(thought_id), int(turn_id), status, _now()),
        )
        db.conn.commit()
    return bool(cur.rowcount)


def commit_receipt(user_id: str, thought_id: int, turn_id: int) -> bool:
    """确认该候选真的被采用（selected → committed）。

    首版保守：模型无法可靠声明「用了哪条」，生产路径不调用它；保留接口给
    未来能给出确定性信号的表达层，测试覆盖。
    """
    with db._lock:
        cur = db.conn.execute(
            "UPDATE thought_context_receipts SET status = 'committed' "
            "WHERE user_id = ? AND thought_id = ? AND turn_id = ? AND status = 'selected'",
            (user_id, int(thought_id), int(turn_id)),
        )
        db.conn.commit()
    return bool(cur.rowcount)


def receipts_for_turn(user_id: str, turn_id: int) -> list[dict]:
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM thought_context_receipts WHERE user_id = ? AND turn_id = ? "
            "ORDER BY thought_id",
            (user_id, int(turn_id)),
        ).fetchall()
    return [dict(row) for row in rows]


def stats(user_id: str) -> dict:
    """可观测性：心事池的状态分布（M5 退出标准：可观测）。"""
    with db._lock:
        rows = db.conn.execute(
            "SELECT status, COUNT(*) AS n FROM pending_thoughts WHERE user_id = ? "
            "GROUP BY status",
            (user_id,),
        ).fetchall()
        total = db.conn.execute(
            "SELECT COUNT(*) FROM pending_thoughts WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
    counts = {row["status"]: int(row["n"]) for row in rows}
    return {
        "total": int(total),
        "pending": counts.get("pending", 0),
        "expressed": counts.get("expressed", 0),
        "dismissed": counts.get("dismissed", 0),
    }


def forget_thoughts_for_source(user_id: str, source_type: str, source_id: int) -> None:
    """来源消失 → 心事作废（不留幽灵惦记）。"""
    with db._lock:
        db.conn.execute(
            "UPDATE pending_thoughts SET status = 'dismissed' "
            "WHERE user_id = ? AND source_type = ? AND source_id = ? AND status = 'pending'",
            (user_id, source_type, int(source_id)),
        )
        db.conn.commit()
