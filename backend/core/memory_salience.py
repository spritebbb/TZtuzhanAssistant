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
- 分层只记不改：policy 表只记录分层结果与理由，评分落库时不改写
  facts.expires_at、不改 confidence、不即时删除；short 事实的自动期限
  （30/7/1 天）由 ``memory_lifecycle_enabled`` 开关 gate，开启后经
  ``fact_decay`` 到期真删（关闭则只保留影子记录，见 G01-LIFECYCLE-ADR 第 15/17 条）。

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

import json
import re
import unicodedata
from datetime import datetime, timedelta

from .log import logger

SCORE_VERSION = 1

# 锚点/初历判定窗口：事实来源消息与真实关系事件的时间邻近度。
# 共同活动完成、约定完成、重要日子等事件发生前后 24h 内被提及的事实，
# 视为与关系进展锚定（§14.8 评分输入含「关系事件/初历标志」，无 LLM 参与）。
_ANCHOR_WINDOW_HOURS = 24

_IMPORTANCE_RE = re.compile(r"(?:很重要|非常重要|对我重要|一定要记住|请记住|别忘了)")
_NO_MEMORY_RE = re.compile(r"(?:别记住|不要记|别记下来|不保存|不留痕|临时聊)")

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


def observe_fact(user_id: str, fact_id: int, message_ids: list[int], *,
                 now: datetime | None = None, new_fact: bool = False) -> dict | None:
    """消费已落库用户消息；LLM 仅提供候选 id，归属、日期和重要性由代码裁定。

    旧事实没有 policy 时仅允许显式重要性重评。无来源的新事实可建立零分
    shadow，但不从整段 transcript 推测重复天数。重复调用不延后观察起点。
    """
    from .features import flag
    from .userdb import db

    if not flag("memory_salience_enabled"):
        return None
    moment = now or datetime.now()
    with db._lock:
        fact = db.conn.execute(
            "SELECT * FROM facts WHERE user_id=? AND id=? AND status='active'",
            (user_id, int(fact_id)),
        ).fetchone()
        if fact is None or fact["surface_policy"] == "never_surface":
            return None
        current = get_policy(user_id, fact_id)
        if current and fact_id in policy_expired_ids(user_id, now=moment):
            return None  # 到期后的旧事实不能通过迟到的评分重新获得召回资格
        requested = {int(i) for i in message_ids if isinstance(i, int) and not isinstance(i, bool)}
        rows = []
        if requested:
            placeholders = ",".join("?" for _ in requested)
            rows = db.conn.execute(
                f"SELECT id, content, ts FROM messages WHERE user_id=? AND role='user' "
                f"AND id IN ({placeholders})", (user_id, *sorted(requested)),
            ).fetchall()
        rows = [r for r in rows if not _NO_MEMORY_RE.search(r["content"])]
        explicit = any(_IMPORTANCE_RE.search(r["content"]) for r in rows)
        if current is None and not new_fact and not explicit:
            return None  # 不把旧事实静默转为 short
        previous_ids = set(json.loads(current["source_message_ids"] or "[]")) if current else set()
        ids = previous_ids | {int(r["id"]) for r in rows}
        days: set[str] = set()
        valid_ids: list[int] = []
        if ids:
            placeholders = ",".join("?" for _ in ids)
            evidence = db.conn.execute(
                f"SELECT id, ts, content FROM messages WHERE user_id=? AND role='user' "
                f"AND id IN ({placeholders})", (user_id, *sorted(ids)),
            ).fetchall()
            for row in evidence:
                if _NO_MEMORY_RE.search(row["content"]):
                    continue
                try:
                    day = datetime.fromisoformat(row["ts"]).date()
                except (TypeError, ValueError):
                    continue
                if day > moment.date():
                    continue
                days.add(day.isoformat())
                valid_ids.append(int(row["id"]))
                explicit = explicit or bool(_IMPORTANCE_RE.search(row["content"]))
        first_observed = (current or {}).get("first_observed_at") or moment.isoformat(timespec="seconds")
        evaluate_fact(user_id, fact_id, explicit=explicit,
                      distinct_days=len(days), pinned=bool(fact["pinned"]))
        # 原始正文只留 messages；policy 只保存经校验的引用和计数。
        review_at = (datetime.fromisoformat(first_observed) + timedelta(days=14)).isoformat(timespec="seconds")
        db.conn.execute(
            "UPDATE memory_policy SET source_message_ids=?, first_observed_at=?, review_at=? "
            "WHERE user_id=? AND fact_id=?",
            (json.dumps(sorted(valid_ids)), first_observed, review_at, user_id, fact_id),
        )
        db.conn.commit()
        return get_policy(user_id, fact_id)


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

def record_user_teaching_annotation(user_id: str, fact_id: int, *,
                                    event_id: int | None = None) -> int | None:
    """视角注释的生产路径：用户亲手改写/确认记忆时记一条 user_teaching。

    注释只表达「这条记忆被用户认真对待过」，不改写事实文本，不升
    confidence；同一事实只保留最近一条 user_teaching（确认动作不堆积）。
    """
    from .userdb import db

    with db._lock:
        exists = db.conn.execute(
            "SELECT 1 FROM facts WHERE user_id=? AND id=?",
            (user_id, int(fact_id)),
        ).fetchone()
        if exists is None:
            return None
        db.conn.execute(
            "DELETE FROM memory_annotations WHERE user_id=? AND fact_id=? "
            "AND role='assistant' AND origin='user_teaching'",
            (user_id, int(fact_id)),
        )
        cur = db.conn.execute(
            "INSERT INTO memory_annotations "
            "(user_id, fact_id, role, emotion, viewpoint, origin, confidence, source_event_id) "
            "VALUES (?, ?, 'assistant', '', '这条记忆用户亲手确认过', 'user_teaching', 1.0, ?)",
            (user_id, int(fact_id), event_id),
        )
        db.conn.commit()
    return int(cur.lastrowid)


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


# ---- 生产来源：关系锚点与初历（事件驱动，无 LLM 参与） ----

def fact_anchored_to_event(user_id: str, fact_id: int, *,
                           now: datetime | None = None) -> bool:
    """关系锚点判定：事实的来源消息与真实关系事件时间邻近（±24h）。

    只吃关系事件库里 active 且未过期的真实事件（共同活动完成/约定完成/
    特殊日子/记忆纠正）；观察起点之后新落库的事实才需判定，旧事实不因此
    迟到升层。跨人格由事件查询天然隔离。
    """
    from .userdb import db

    moment = now or datetime.now()
    with db._lock:
        fact = db.conn.execute(
            "SELECT source_message_ids, ts FROM facts "
            "WHERE user_id=? AND id=?",
            (user_id, int(fact_id)),
        ).fetchone()
        if fact is None:
            return False
        # 事实落库时间即观察锚（facts 无 created_at 列，ts 即创建时刻）。
        try:
            base = datetime.fromisoformat(str(fact["ts"]))
        except (TypeError, ValueError):
            base = None
        if base is None:
            try:
                ids = json.loads(fact["source_message_ids"] or "[]")
            except json.JSONDecodeError:
                ids = []
            if ids:
                row = db.conn.execute(
                    "SELECT ts FROM messages WHERE user_id=? AND id=?",
                    (user_id, int(ids[0])),
                ).fetchone()
                if row is not None:
                    try:
                        base = datetime.fromisoformat(str(row["ts"]))
                    except (TypeError, ValueError):
                        base = None
        if base is None:
            return False
        window_start = (base - timedelta(hours=_ANCHOR_WINDOW_HOURS)).isoformat(timespec="seconds")
        window_end = (base + timedelta(hours=_ANCHOR_WINDOW_HOURS)).isoformat(timespec="seconds")
        hit = db.conn.execute(
            "SELECT 1 FROM relationship_events WHERE user_id=? AND status='active' "
            "AND (expires_at IS NULL OR expires_at > ?) "
            "AND occurred_at BETWEEN ? AND ? LIMIT 1",
            (user_id, moment.isoformat(timespec="seconds"), window_start, window_end),
        ).fetchone()
    return hit is not None


def reevaluate_fact_from_sources(user_id: str, fact_id: int, *,
                                 now: datetime | None = None) -> dict | None:
    """事件驱动重评：以既有来源消息 + 锚点判定重算 policy（不改 facts）。

    关系事件落库后对近期新事实调用；锚点只影响本条事实的评分输入，
    不产生跨事实联动。旧事实（无 policy）不因迟到评分被静默激活——
    该约束由 observe_fact 承担，本函数只消费已有 policy 的事实。
    """
    from .features import flag
    from .userdb import db

    if not flag("memory_salience_enabled"):
        return None
    with db._lock:
        row = db.conn.execute(
            "SELECT 1 FROM memory_policy WHERE user_id=? AND fact_id=?",
            (user_id, int(fact_id)),
        ).fetchone()
        if row is None:
            return None
        ids = db.conn.execute(
            "SELECT source_message_ids FROM memory_policy WHERE user_id=? AND fact_id=?",
            (user_id, int(fact_id)),
        ).fetchone()
    message_ids = []
    if ids is not None:
        try:
            message_ids = [int(i) for i in json.loads(ids["source_message_ids"] or "[]")]
        except (json.JSONDecodeError, TypeError, ValueError):
            message_ids = []
    policy = observe_fact(user_id, fact_id, message_ids, now=now)
    if policy is None:
        return None
    if fact_anchored_to_event(user_id, fact_id, now=now):
        policy = evaluate_fact(
            user_id, fact_id,
            explicit=bool(policy["explicit_importance"]),
            anchor=True,
            distinct_days=int(policy["distinct_days"]),
            first_event=bool(policy["first_event"]),
            pinned=bool(policy and _fact_pinned(user_id, fact_id)),
        )
    return policy


def _fact_pinned(user_id: str, fact_id: int) -> bool:
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT pinned FROM facts WHERE user_id=? AND id=?",
            (user_id, int(fact_id)),
        ).fetchone()
    return bool(row and row["pinned"])


def reevaluate_recent_facts_after_event(user_id: str, *,
                                        occurred_at: str,
                                        now: datetime | None = None) -> int:
    """事件落库后对窗口内新建的事实做锚点重评（消费方：relationship_events.record）。

    只重评已有 policy 且创建时间落在事件邻近窗口内的事实——新事实的
    observe_fact 已在提炼链路建立 policy；这里把「与真实关系事件同时段
    产生」的锚点信号补进评分。返回重评条数；任何失败不外溢。
    """
    from .userdb import db

    moment = now or datetime.now()
    try:
        event_dt = datetime.fromisoformat(str(occurred_at))
    except (TypeError, ValueError):
        return 0
    window_start = (event_dt - timedelta(hours=_ANCHOR_WINDOW_HOURS)).isoformat(timespec="seconds")
    window_end = (event_dt + timedelta(hours=_ANCHOR_WINDOW_HOURS)).isoformat(timespec="seconds")
    with db._lock:
        rows = db.conn.execute(
            "SELECT p.fact_id FROM memory_policy p JOIN facts f "
            "ON f.user_id=p.user_id AND f.id=p.fact_id "
            "WHERE p.user_id=? AND f.status='active' AND f.pinned=0 "
            "AND p.relationship_anchor=0 "
            "AND f.ts BETWEEN ? AND ?",
            (user_id, window_start, window_end),
        ).fetchall()
    count = 0
    for row in rows:
        try:
            policy = reevaluate_fact_from_sources(user_id, int(row["fact_id"]), now=moment)
            if policy is not None:
                count += 1
        except Exception:
            continue
    return count


def note_event_for_first_occurrence(user_id: str, event_id: int,
                                    event_type: str) -> bool:
    """初历生产来源：关系事件落库时按事件主题建立首次记录。

    topic_key 取事件 object（约定内容/目标标题/日子标签/故事标题），
    做与 promise_hash 同款规范化后唯一；同一 (event_type, topic) 后续
    事件不再吃首次加成。源删除时标记随事件级联消失。
    """
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT object FROM relationship_events WHERE user_id=? AND id=?",
            (user_id, int(event_id)),
        ).fetchone()
    if row is None:
        return False
    topic = str(row["object"] or "").strip()
    if not topic:
        return False
    try:
        return mark_first_occurrence(user_id, event_type, topic, event_id)
    except SalienceError:
        return False


# ---- F07 解释层露出：只暴露非敏感生命周期元数据 ----

def lifecycle_for_facts(user_id: str, fact_ids: list[int], *,
                        now: datetime | None = None) -> dict[int, dict]:
    """本轮实际引用的事实 → 展示用生命周期元数据（F07）。

    只返回允许展示的元数据：pinned/expires_at/保留说明/分层/置信度/确认时间/
    是否被用户亲手确认过。不返回 score 或权重等算法内部值；非 active 与
    never_surface 事实直接跳过（二次授权：后端返回时再查一次权威状态）。
    """
    from .userdb import db

    clean = sorted({int(i) for i in fact_ids if int(i) > 0})
    if not clean:
        return {}
    placeholders = ",".join("?" for _ in clean)
    with db._lock:
        rows = db.conn.execute(
            f"SELECT f.id, f.content, f.pinned, f.expires_at, f.status, f.surface_policy, "
            f"f.confidence, f.verified_at, p.tier, p.legacy, p.first_observed_at "
            f"FROM facts f LEFT JOIN memory_policy p "
            f"ON p.user_id=f.user_id AND p.fact_id=f.id "
            f"WHERE f.user_id=? AND f.id IN ({placeholders})",
            (user_id, *clean),
        ).fetchall()
        teaching = db.conn.execute(
            f"SELECT DISTINCT fact_id FROM memory_annotations "
            f"WHERE user_id=? AND role='assistant' AND origin='user_teaching' "
            f"AND fact_id IN ({placeholders})",
            (user_id, *clean),
        ).fetchall()
    taught_ids = {int(r["fact_id"]) for r in teaching}
    result: dict[int, dict] = {}
    for row in rows:
        if row["status"] != "active" or row["surface_policy"] == "never_surface":
            continue  # 二次授权：不再可展示的事实不出现
        pinned = bool(row["pinned"])
        tier = str(row["tier"]) if row["tier"] else None
        expires_at = row["expires_at"]
        auto_until = _auto_retention_until(row, now=now)
        if pinned or tier == "long":
            retention = "长期保留"
        elif expires_at:
            retention = f"保留到 {str(expires_at)[:10]}"
        elif auto_until:
            retention = f"保留到 {auto_until[:10]}"
        else:
            retention = "尚待确认"
        result[int(row["id"])] = {
            "pinned": pinned,
            "expires_at": expires_at,
            "tier": tier,
            "retention": retention,
            "confidence": float(row["confidence"] or 0.0),
            "verified_at": row["verified_at"],
            "user_confirmed": int(row["id"]) in taught_ids,
            "can_edit": True,
        }
    return result


def _auto_retention_until(row, *, now: datetime | None = None) -> str | None:
    """short 事实的自动期限（与 policy_expired_ids 同条件），无则 None。"""
    if (row["pinned"] or row["expires_at"] or row["tier"] != "short"
            or row["legacy"] or not row["first_observed_at"]):
        return None
    try:
        start = datetime.fromisoformat(str(row["first_observed_at"]))
        if start.tzinfo is not None:
            start = start.astimezone().replace(tzinfo=None)
        return (start + timedelta(days=short_retention_days(str(row["content"])))
                ).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None


def expiring_soon_candidates(user_id: str, *, within_days: int = 3,
                             now: datetime | None = None) -> list[dict]:
    """可见遗忘的到期前候选（非敏感元数据，不含原文）。

    返回即将到期（既有硬期限或 policy 自动期限）且未固定的 active 事实的
    id/到期时刻；提醒与否由表达层决定（走 pending_thoughts 候选 + 既有
    主动仲裁），本函数只做确定性查询。到期后绝不靠墓碑复原原文。
    """
    from .features import flag
    from .userdb import db

    if not flag("memory_lifecycle_enabled") or not flag("memory_salience_enabled"):
        return []
    moment = now or datetime.now()
    now_iso = moment.isoformat(timespec="seconds")
    horizon = (moment + timedelta(days=within_days)).isoformat(timespec="seconds")
    with db._lock:
        rows = db.conn.execute(
            "SELECT f.id, f.content, f.pinned, f.expires_at, f.status, f.surface_policy, "
            "f.confidence, f.verified_at, p.tier, p.legacy, p.first_observed_at "
            "FROM facts f LEFT JOIN memory_policy p "
            "ON p.user_id=f.user_id AND p.fact_id=f.id "
            "WHERE f.user_id=? AND f.status='active' AND f.pinned=0 "
            "AND f.surface_policy != 'never_surface'",
            (user_id,),
        ).fetchall()
    candidates: list[dict] = []
    for row in rows:
        expires = row["expires_at"]
        if expires:
            if not (now_iso < str(expires) <= horizon):
                continue
            until = str(expires)
        else:
            until = _auto_retention_until(row, now=moment) or ""
            if not until or not (now_iso < until <= horizon):
                continue
        candidates.append({
            "fact_id": int(row["id"]),
            "expires_at": until,
            "retention": f"保留到 {until[:10]}",
        })
    return sorted(candidates, key=lambda c: c["fact_id"])


# ---- 观察期查询（shadow 评估用） ----

def tier_distribution(user_id: str) -> dict:
    from .userdb import db

    with db._lock:
        rows = db.conn.execute(
            "SELECT tier, COUNT(*) AS n FROM memory_policy WHERE user_id=? GROUP BY tier",
            (user_id,),
        ).fetchall()
    return {str(r["tier"]): int(r["n"]) for r in rows}


def short_retention_days(content: str) -> int:
    """确定性的类别候选：临时事件一天、当前状态七天、其余短期三十天。"""
    if re.search(r"今天|今晚|此刻|临时", content):
        return 1
    if re.search(r"最近|当前|这周|正在|这几天", content):
        return 7
    return 30


def policy_expired_ids(user_id: str, *, now: datetime | None = None,
                       database=None) -> set[int]:
    """policy 仅补充无既有期限的事实；facts.expires_at 始终优先。

    无观察起点的旧 policy、legacy、long 和 pinned 不受此自动期限影响。
    关闭生命周期开关即保持 shadow；不复制或改写事实正文与原始期限。
    """
    from .features import flag
    from .userdb import db

    if not flag("memory_lifecycle_enabled") or not flag("memory_salience_enabled"):
        return set()
    database = database or db
    moment = now or datetime.now()
    expired: set[int] = set()
    with database._lock:
        rows = database.conn.execute(
            "SELECT f.id, f.content, p.first_observed_at FROM facts f "
            "JOIN memory_policy p ON p.user_id=f.user_id AND p.fact_id=f.id "
            "WHERE f.user_id=? AND f.status='active' AND f.pinned=0 "
            "AND f.expires_at IS NULL "
            "AND p.tier='short' AND p.legacy=0 AND p.first_observed_at IS NOT NULL",
            (user_id,),
        ).fetchall()
    for row in rows:
        try:
            start = datetime.fromisoformat(row["first_observed_at"])
            if start.tzinfo is not None:
                start = start.astimezone().replace(tzinfo=None)
            local_now = moment.astimezone().replace(tzinfo=None) if moment.tzinfo else moment
            if start + timedelta(days=short_retention_days(row["content"])) <= local_now:
                expired.add(int(row["id"]))
        except (ValueError, TypeError):
            continue  # 无法解释旧时间时保守保留，绝不批量删除
    return expired
