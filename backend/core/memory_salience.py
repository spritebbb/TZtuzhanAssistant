# -*- coding: utf-8 -*-
"""G01 记忆显著度：分层评分、生命周期接线、视角注释与初历标记。

契约（docs/Zcode技术指导.md §14.8 G01 + 调度文档批次3）：

**评分（shadow 起步）**
- ``memory_policy(fact_id, user_id, tier, score, score_version, review_at)``
  一条事实一条；facts 表仍是权威，原到期硬边界先执行；
- 评分 ``S = 40*explicit_importance + 20*relationship_anchor
  + 10*min(distinct_days_mentioned, 3) + 10*first_event``（布尔 0/1，
  clamp 100）；模型 confidence 不因 score 升高改变；
- S≥60 → long；S≤30 且未 pinned → short；中间保持原 tier（滞回）；
  新条目默认 short；**pinned 不通过分数表示**，score=0 也不降级 pinned；
- shadow 语义：policy 表只记录分层结果与理由，不改写 facts.expires_at，
  不触发任何删除——评分先跑观察期，由 review 消费。

**分层生命周期**
- short 默认 30 天、当前状态类 7 天、临时事件 1 天（建议值进 policy 注记，
  不覆盖 facts 既有 expires_at——已有更早 expires_at 不自动延长）；
- 敏感/never_surface 无论 S 多高无主动召回资格；
- 旧条目迁移 tier=legacy，避免上线瞬间大规模删除。

**视角与初历**
- ``memory_annotations``：她的事实视角侧表（owner=assistant、origin/
  confidence、不改写事实文本）；
- ``first_occurrences``：初历标记（user_id+event_type+topic_key 唯一，
  topic_key 按 promise_hash 同款规范化）；源删除即删标记，旧历史不重复
  吃首次加成。

三张表全部进入 LC-1 记忆类别导出与 reset 清单。
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta

from .log import logger

SCORE_VERSION = 1

# 分层阈值（§14.8 原文）：滞回区间 31..59 保持原 tier
_LONG_THRESHOLD = 60
_SHORT_THRESHOLD = 30
_LONG_TIER_TTL_DAYS = 30      # long tier 的 policy 复核周期
_SHADOW_REVIEW_DAYS = 14      # 首次 shadow 逻辑回放窗口

_WEIGHTS = {"explicit": 40, "anchor": 20, "days": 10, "first": 10}


class SalienceError(ValueError):
    """显著度计算的预期业务错误。"""


# ---- 评分 ----

def compute_score(*, explicit_importance: bool, relationship_anchor: bool,
                  distinct_days_mentioned: int, first_event: bool) -> int:
    """S = 40*explicit + 20*anchor + 10*min(days,3) + 10*first（clamp 100）。"""
    s = (_WEIGHTS["explicit"] * (1 if explicit_importance else 0)
         + _WEIGHTS["anchor"] * (1 if relationship_anchor else 0)
         + _WEIGHTS["days"] * min(max(0, int(distinct_days_mentioned)), 3)
         + _WEIGHTS["first"] * (1 if first_event else 0))
    return max(0, min(100, int(s)))


def decide_tier(score: int, *, current_tier: str = "short", pinned: bool) -> str:
    """滞回分层：pinned 永不通过分数降级；中间带保持原 tier。"""
    if pinned:
        return current_tier if current_tier in ("long", "short") else "short"
    if score >= _LONG_THRESHOLD:
        return "long"
    if score <= _SHORT_THRESHOLD:
        return "short"
    return current_tier if current_tier in ("long", "short") else "short"


def _normalize_topic_key(topic: str) -> str:
    """canonical 主题短语规范化（与 promise_hash 同款：NFKC+去空白+lower）。"""
    text = unicodedata.normalize("NFKC", topic or "").strip().lower()
    return re.sub(r"\s+", " ", text)


# ---- policy 读写（shadow：只记录，不改 facts） ----

def _require_source(conn, user_id: str, table: str, source_id: int) -> None:
    if conn.execute(
        f"SELECT 1 FROM {table} WHERE user_id=? AND id=?",
        (user_id, int(source_id)),
    ).fetchone() is None:
        raise SalienceError("来源不存在或不属于当前人格")

def upsert_policy(user_id: str, fact_id: int, *, score: int, tier: str,
                  explicit: bool = False, anchor: bool = False,
                  distinct_days: int = 0, first_event: bool = False,
                  legacy: bool = False) -> dict:
    """一条事实一条 policy；重复调用更新评分与 tier（保留首次 first_seen 语义）。"""
    from .userdb import db

    now = datetime.now()
    review_at = (now + timedelta(days=_LONG_TIER_TTL_DAYS if tier == "long"
                                 else _SHADOW_REVIEW_DAYS)).isoformat(timespec="seconds")
    with db._lock:
        _require_source(db.conn, user_id, "facts", fact_id)
        db.conn.execute(
            "INSERT INTO memory_policy (user_id, fact_id, tier, score, score_version, "
            "explicit_importance, relationship_anchor, distinct_days, first_event, "
            "legacy, review_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id, fact_id) DO UPDATE SET tier=excluded.tier, "
            "score=excluded.score, score_version=excluded.score_version, "
            "explicit_importance=excluded.explicit_importance, "
            "relationship_anchor=excluded.relationship_anchor, "
            "distinct_days=excluded.distinct_days, first_event=excluded.first_event, "
            "legacy=excluded.legacy, review_at=excluded.review_at, updated_at=excluded.updated_at",
            (user_id, int(fact_id), tier, int(score), SCORE_VERSION,
             1 if explicit else 0, 1 if anchor else 0,
             int(distinct_days), 1 if first_event else 0,
             1 if legacy else 0, review_at, now.isoformat(timespec="seconds")),
        )
        db.conn.commit()
        row = db.conn.execute(
            "SELECT * FROM memory_policy WHERE user_id=? AND fact_id=?",
            (user_id, int(fact_id)),
        ).fetchone()
    return dict(row) if row is not None else {}


def get_policy(user_id: str, fact_id: int) -> dict | None:
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT * FROM memory_policy WHERE user_id=? AND fact_id=?",
            (user_id, int(fact_id)),
        ).fetchone()
    return dict(row) if row is not None else None


def evaluate_fact(user_id: str, fact_id: int, *, explicit: bool = False,
                  anchor: bool = False, distinct_days: int = 0,
                  first_event: bool = False, pinned: bool = False,
                  legacy: bool = False) -> dict:
    """评分 + 分层 + policy 落库的统一入口（shadow：不触碰 facts.expires_at）。"""
    score = compute_score(explicit_importance=explicit,
                          relationship_anchor=anchor,
                          distinct_days_mentioned=distinct_days,
                          first_event=first_event)
    current = get_policy(user_id, fact_id)
    current_tier = str(current["tier"]) if current else "short"
    tier = decide_tier(score, current_tier=current_tier, pinned=pinned)
    if legacy and not current:
        tier = "legacy"
    return upsert_policy(user_id, fact_id, score=score, tier=tier,
                         explicit=explicit, anchor=anchor,
                         distinct_days=distinct_days,
                         first_event=first_event, legacy=legacy)


# ---- 视角注释（她的事实视角；不改写事实文本） ----

def add_annotation(user_id: str, fact_id: int, *, emotion: str = "",
                   viewpoint: str = "", origin: str = "observed",
                   confidence: float = 0.7,
                   source_event_id: int | None = None) -> int:
    from .userdb import db

    if origin not in {"observed", "user_teaching", "inference"}:
        raise SalienceError(f"origin 非法: {origin}")
    confidence = max(0.0, min(1.0, float(confidence)))
    with db._lock:
        _require_source(db.conn, user_id, "facts", fact_id)
        if source_event_id is not None:
            _require_source(db.conn, user_id, "relationship_events", source_event_id)
        cur = db.conn.execute(
            "INSERT INTO memory_annotations "
            "(user_id, fact_id, role, emotion, viewpoint, origin, confidence, source_event_id) "
            "VALUES (?, ?, 'assistant', ?, ?, ?, ?, ?)",
            (user_id, int(fact_id), emotion, viewpoint, origin,
             confidence, source_event_id),
        )
        db.conn.commit()
    return int(cur.lastrowid)


def annotations_for(user_id: str, fact_id: int) -> list[dict]:
    from .userdb import db

    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM memory_annotations WHERE user_id=? AND fact_id=? "
            "ORDER BY id DESC LIMIT 5",
            (user_id, int(fact_id)),
        ).fetchall()
    return [dict(r) for r in rows]


# ---- 初历（first occurrence） ----

def mark_first_occurrence(user_id: str, event_type: str, topic_key: str,
                          source_event_id: int | None = None) -> bool:
    """唯一 (user, event_type, topic_key)：第一次 True，重复 False。

    源删除后标记随之消失（forget_for_source），此前的旧历史不会重新吃
    首次加成——只有未来的新事件可以重新建立首条记录。
    """
    from .userdb import db

    key = _normalize_topic_key(topic_key)
    if not key:
        return False
    with db._lock:
        if source_event_id is not None:
            _require_source(db.conn, user_id, "relationship_events", source_event_id)
        cur = db.conn.execute(
            "INSERT OR IGNORE INTO first_occurrences "
            "(user_id, event_type, topic_key, source_event_id) VALUES (?, ?, ?, ?)",
            (user_id, event_type, key, source_event_id),
        )
        db.conn.commit()
    return cur.rowcount == 1


def is_first_occurrence(user_id: str, event_type: str, topic_key: str) -> bool:
    from .userdb import db

    key = _normalize_topic_key(topic_key)
    if not key:
        return False
    with db._lock:
        row = db.conn.execute(
            "SELECT 1 FROM first_occurrences WHERE user_id=? AND event_type=? AND topic_key=?",
            (user_id, event_type, key),
        ).fetchone()
    return row is None


def forget_for_source(user_id: str, *, fact_id: int | None = None,
                      source_event_id: int | None = None) -> dict:
    """源删除级联：policy/annotations 按 fact_id 删，初历按 source_event_id 删。

    即时重算语义：derive/评分每次现算，删除后下一次评估不再引用已删源。
    """
    from .userdb import db

    removed = {"policy": 0, "annotations": 0, "first_occurrences": 0}
    with db._lock:
        if fact_id is not None:
            for table in ("memory_policy", "memory_annotations"):
                cur = db.conn.execute(
                    f"DELETE FROM {table} WHERE user_id=? AND fact_id=?",
                    (user_id, int(fact_id)),
                )
                removed["policy" if table == "memory_policy" else "annotations"] += cur.rowcount
        if source_event_id is not None:
            cur = db.conn.execute(
                "DELETE FROM memory_annotations WHERE user_id=? AND source_event_id=?",
                (user_id, int(source_event_id)),
            )
            removed["annotations"] += cur.rowcount
            cur = db.conn.execute(
                "DELETE FROM first_occurrences WHERE user_id=? AND source_event_id=?",
                (user_id, int(source_event_id)),
            )
            removed["first_occurrences"] = cur.rowcount
        db.conn.commit()
    if any(removed.values()):
        logger.info("[记忆显著度] 源删除级联 user={} removed={}", user_id, removed)
    return removed


# ---- 观察期查询（shadow 评估用） ----

def tier_distribution(user_id: str) -> dict:
    from .userdb import db

    with db._lock:
        rows = db.conn.execute(
            "SELECT tier, COUNT(*) AS n FROM memory_policy WHERE user_id=? GROUP BY tier",
            (user_id,),
        ).fetchall()
    return {str(r["tier"]): int(r["n"]) for r in rows}
