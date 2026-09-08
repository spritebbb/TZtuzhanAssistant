# -*- coding: utf-8 -*-
"""L04 幽默记忆：梗的授权、冷却与退役。

契约（docs/Zcode技术指导.md L04 + 调度文档批次 7）：

- 侧表 ``humor_usage(user_id, term_id, source_turn_id, reaction, last_used_at,
  blocked_until, status)``；status = candidate / approved / retired；
- 只接受**明确反馈**：positive / negative / unknown，单条「哈哈」只算 unknown
  （不把客套当长期授权）；
- 近 7 天至少 2 次明确 positive 才 approved；**负反馈优先于历史 positive**，
  明确「别再玩这个梗」立即 retired，不等负票累积；
- 单次最多一梗、最近 3 个回合不重复；初识不适用；严肃/求助/修复场景默认不插；
- 有效偏好仍由 ``user_terms`` 承载并随关系包导出；本表是 runtime 明细，
  reset 清理、不导出；
- 用户删除共同语言（del_term）时同步退役对应梗。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .log import logger

STATUSES: tuple[str, ...] = ("candidate", "approved", "retired")
REACTIONS: tuple[str, ...] = ("positive", "negative", "unknown")

APPROVE_POSITIVES = 2          # 近 7 天明确 positive 次数门槛
APPROVE_WINDOW_DAYS = 7
REPEAT_COOLDOWN_TURNS = 3      # 最近 3 个回合不重复同一梗
NEGATIVE_BLOCK_DAYS = 7        # 负反馈后的静默期

# 明确反馈词表（确定性，无 LLM）：「哈哈」单独出现只算 unknown。
_POSITIVE_RE = re.compile(
    r"这个梗(?:好|可以|行|有意思)|这梗(?:好|可以)|笑死|太好笑|太好笑了|有意思|"
    r"你赢了|好梗|绝了|哈哈哈哈哈"
)
_NEGATIVE_RE = re.compile(
    r"别(?:再)?(?:玩|说|讲|用)这个梗|不好笑|无聊|别贫|闭嘴|烦人|别闹|少来|"
    r"不(?:要|用)(?:再)?(?:玩|说|讲)这个"
)
# 严肃/求助/修复场景：默认不插梗
_SERIOUS_RE = re.compile(
    r"怎么办|帮我|求助|紧急|认真|严肃|正经|难过|生气|抱歉|对不起|别开玩笑|"
    r"心情不好|压力|焦虑|崩溃|受伤"
)
_LAUGH_ONLY_RE = re.compile(r"^[哈呵嘿嘻嗯哦额\s。，,!！?？~～]+$")


class HumorMemoryError(ValueError):
    """幽默记忆的预期业务错误。"""


def _now(moment: datetime | None = None) -> datetime:
    return moment or datetime.now()


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def classify_feedback(text: str) -> str:
    """明确反馈才归类；单条「哈哈」/纯语气词 = unknown（不刷授权）。"""
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean or len(clean) > 200:
        return "unknown"
    if _NEGATIVE_RE.search(clean):
        return "negative"
    if _POSITIVE_RE.search(clean):
        return "positive"  # 含连续大笑（5 个以上「哈」）这类明确反应
    if _LAUGH_ONLY_RE.match(clean):
        return "unknown"  # 单条/短促笑声，不算明确认可
    return "unknown"


def is_serious_context(text: str, *, tension: int = 0) -> bool:
    """严肃/求助/修复语境：默认不插梗。"""
    if tension and int(tension) > 0:
        return True
    return bool(_SERIOUS_RE.search(str(text or "")))


# ---- 明细入账 ----

def note_usage(user_id: str, term_id: int, turn_id: int | None, *,
               now: datetime | None = None) -> bool:
    """记录她在某一轮用了一个梗（幂等：同 user/term/turn 只一条）。"""
    from .userdb import db

    moment = _now(now)
    with db._lock:
        cur = db.conn.execute(
            "INSERT OR IGNORE INTO humor_usage "
            "(user_id, term_id, source_turn_id, reaction, last_used_at, status, created_at) "
            "VALUES (?, ?, ?, 'unknown', ?, ?, ?)",
            (user_id, int(term_id), None if turn_id is None else int(turn_id),
             _iso(moment), _status_of(user_id, int(term_id)), _iso(moment)),
        )
        db.conn.commit()
    return bool(cur.rowcount)


def record_feedback(user_id: str, term_id: int, turn_id: int | None, reaction: str,
                    *, now: datetime | None = None) -> dict:
    """登记一次明确反馈并重算该梗状态（幂等：同 user/term/turn 更新同一条）。"""
    if reaction not in REACTIONS:
        raise HumorMemoryError(f"未知反馈：{reaction}")
    from .userdb import db

    moment = _now(now)
    with db._lock:
        cur = db.conn.execute(
            "UPDATE humor_usage SET reaction=?, last_used_at=COALESCE(last_used_at, ?) "
            "WHERE user_id=? AND term_id=? AND source_turn_id IS ?",
            (reaction, _iso(moment), user_id, int(term_id),
             None if turn_id is None else int(turn_id)),
        )
        if not cur.rowcount:
            db.conn.execute(
                "INSERT INTO humor_usage "
                "(user_id, term_id, source_turn_id, reaction, last_used_at, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'candidate', ?)",
                (user_id, int(term_id), None if turn_id is None else int(turn_id),
                 reaction, _iso(moment), _iso(moment)),
            )
        db.conn.commit()
    # 先落反馈再算状态：门槛把本次反馈一并计入
    blocked_until = None
    if reaction == "negative":
        status = "retired"
        blocked_until = _iso(moment + timedelta(days=NEGATIVE_BLOCK_DAYS))
    elif reaction == "positive":
        status = ("approved" if _positive_count(user_id, int(term_id), now=moment)
                  >= APPROVE_POSITIVES else "candidate")
    else:
        status = _status_of(user_id, int(term_id))
    with db._lock:
        db.conn.execute(
            "UPDATE humor_usage SET status=?, blocked_until=? WHERE user_id=? AND term_id=?",
            (status, blocked_until, user_id, int(term_id)),
        )
        db.conn.commit()
    logger.info("[幽默记忆] 梗 #{} 反馈 {} → {}", term_id, reaction, status)
    return {"term_id": int(term_id), "reaction": reaction, "status": status}


def _positive_count(user_id: str, term_id: int, *, now: datetime) -> int:
    from .userdb import db

    cutoff = (now - timedelta(days=APPROVE_WINDOW_DAYS)).isoformat(timespec="seconds")
    with db._lock:
        row = db.conn.execute(
            "SELECT COUNT(*) AS n FROM humor_usage WHERE user_id=? AND term_id=? "
            "AND reaction='positive' AND created_at >= ?",
            (user_id, int(term_id), cutoff),
        ).fetchone()
    return int(row["n"]) if row else 0


def _status_of(user_id: str, term_id: int) -> str:
    """该梗当前状态（取最近一行）。"""
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT status FROM humor_usage WHERE user_id=? AND term_id=? "
            "ORDER BY id DESC LIMIT 1",
            (user_id, int(term_id)),
        ).fetchone()
    return str(row["status"]) if row else "candidate"


def status_map(user_id: str) -> dict[int, str]:
    """term_id → 最新状态（供注入层过滤 retired）。"""
    from .userdb import db

    with db._lock:
        rows = db.conn.execute(
            "SELECT term_id, status, MAX(id) AS last_id FROM humor_usage "
            "WHERE user_id=? GROUP BY term_id",
            (user_id,),
        ).fetchall()
    return {int(r["term_id"]): str(r["status"]) for r in rows}


def retire_term(user_id: str, term_id: int, *, now: datetime | None = None) -> int:
    """用户删除/禁用共同语言时同步退役（侧表与偏好一致）。"""
    from .userdb import db

    moment = _now(now)
    with db._lock:
        cur = db.conn.execute(
            "UPDATE humor_usage SET status='retired', blocked_until=? "
            "WHERE user_id=? AND term_id=?",
            (_iso(moment + timedelta(days=NEGATIVE_BLOCK_DAYS)), user_id, int(term_id)),
        )
        db.conn.commit()
    return int(cur.rowcount)


# ---- 选择（单次最多一梗） ----

def _recent_term_ids(user_id: str, *, within_turns: int = REPEAT_COOLDOWN_TURNS,
                     now: datetime) -> set[int]:
    """最近若干轮已经用过的梗（避免连着重复）。"""
    from .userdb import db

    with db._lock:
        # 只算「实际使用」行（reaction 仍为 unknown）；反馈行不代表一次使用。
        rows = db.conn.execute(
            "SELECT term_id FROM humor_usage WHERE user_id=? AND source_turn_id IS NOT NULL "
            "AND reaction='unknown' ORDER BY source_turn_id DESC LIMIT ?",
            (user_id, max(1, int(within_turns))),
        ).fetchall()
    return {int(r["term_id"]) for r in rows}


def select_humor(user_id: str, *, stage: str = "熟悉", serious: bool = False,
                 query: str = "", now: datetime | None = None) -> dict | None:
    """选一个可用的梗：approved、未退役、未封锁、最近 3 回合没用过。

    初识不适用、严肃/求助场景默认不插；一次最多一个。
    """
    from .features import flag

    if not flag("humor_memory_enabled"):
        return None
    if stage == "初识" or serious:
        return None
    moment = _now(now)
    blocked = _recent_term_ids(user_id, now=moment)
    try:
        from .userdb import db

        terms = db.get_terms(user_id, limit=30)
    except Exception:
        return None
    statuses = status_map(user_id)
    for term in terms:
        term_id = int(term["id"])
        if statuses.get(term_id) != "approved":
            continue
        if term_id in blocked:
            continue
        if not _term_still_exists(user_id, term_id):
            continue
        return {"term_id": term_id, "term": str(term["term"]),
                "meaning": str(term.get("meaning") or "")}
    return None


def _term_still_exists(user_id: str, term_id: int) -> bool:
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT 1 FROM user_terms WHERE user_id=? AND id=?",
            (user_id, int(term_id)),
        ).fetchone()
    return row is not None


def filter_injectable(user_id: str, terms: list[dict], *,
                      now: datetime | None = None) -> list[dict]:
    """注入层过滤：退役的梗不再出现（其余保持既有共同语言行为）。"""
    if not terms:
        return []
    statuses = status_map(user_id)
    return [t for t in terms if statuses.get(int(t["id"])) != "retired"]


# ---- 表达侧接线（进程内记忆最近一次用到的梗） ----

_last_used: dict[str, list[int]] = {}


def note_reply_usage(user_id: str, reply: str, turn_id: int | None, *,
                     now: datetime | None = None) -> list[int]:
    """从她的回复里检出用到的共同语言并记使用（确定性包含匹配，最多 2 个）。"""
    from .userdb import db

    text = str(reply or "")
    if not text:
        return []
    try:
        terms = db.get_terms(user_id, limit=30)
    except Exception:
        return []
    used: list[int] = []
    for term in terms:
        word = str(term.get("term") or "").strip()
        if len(word) < 2 or word not in text:
            continue
        if note_usage(user_id, int(term["id"]), turn_id, now=now):
            used.append(int(term["id"]))
        if len(used) >= 2:
            break
    if used:
        _last_used[user_id] = used
    return used


def record_feedback_from_reply(user_id: str, text: str, *, turn_id: int | None = None,
                               now: datetime | None = None) -> list[dict]:
    """把用户这句话的明确反馈记到最近一轮用过的梗上；无明确反馈则不动。"""
    reaction = classify_feedback(text)
    if reaction == "unknown":
        return []
    used = _last_used.get(user_id) or []
    out: list[dict] = []
    for term_id in used:
        try:
            out.append(record_feedback(user_id, term_id, turn_id, reaction, now=now))
        except HumorMemoryError:
            continue
    if out:
        _last_used.pop(user_id, None)  # 反馈已入账，避免重复计
    return out
