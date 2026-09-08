# -*- coding: utf-8 -*-
"""L05 领域信任：五个领域各自的可依赖程度，不替代二维关系。

契约（docs/Zcode技术指导.md L05 + 调度文档批次 11）：

- 表 ``domain_trust_events(user_id, event_id, domain, delta, rule_version)``
  唯一 user/event/domain；``domain_trust_snapshot(user_id, domain, value, version)``
  为派生快照；
- **同一事件由唯一 relationship reducer 同时算全局与领域增量，禁止双入口叠加**；
- 首次领域值 = 当时的全局 trust（不是 0）；日每域 ±4、0–100；
- 领域只改变相应披露/求助/玩笑强度，不能因 task 低拒绝基本聊天、
  不能因 privacy 高绕过隐私开关；双维阶段仍取 min(global)；
- GET 返回高层可依赖程度与最多 2 条来源；DELETE 按当前全局 trust 重建。
"""
from __future__ import annotations

from datetime import datetime

from .log import logger

DOMAINS: tuple[str, ...] = ("emotional", "task", "privacy", "promise", "humor")

_DOMAIN_LABELS = {
    "emotional": "情绪陪伴",
    "task": "事情托付",
    "privacy": "隐私边界",
    "promise": "约定兑现",
    "humor": "玩笑分寸",
}

# 关系事件规则 → 领域增量（与 affection.DIMENSION_RULES 同一事件，只算一次）
EVENT_DOMAIN_RULES: dict[str, tuple[str, int]] = {
    "promise_confirmed": ("promise", 2),
    "boundary_respected": ("privacy", 1),
    "user_self_disclosure": ("emotional", 1),
    "persona_disclosure_accepted": ("emotional", 2),
    "confirmed_offense": ("emotional", -2),
    "task_delivered": ("task", 2),
    "promise_broken": ("promise", -2),
    "humor_crossed": ("humor", -2),
}

_DAILY_CAP = 4
RULE_VERSION = 1


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _global_trust(user_id: str) -> int:
    try:
        from .affection import dimensions_of

        return int(dimensions_of(user_id)[0])
    except Exception:
        return 0


def _ensure_snapshot(user_id: str, domain: str) -> int:
    """首次领域值 = 当前全局 trust（不是 0）。"""
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT value FROM domain_trust_snapshot WHERE user_id=? AND domain=?",
            (user_id, domain),
        ).fetchone()
        if row is not None:
            return int(row["value"])
        value = max(0, min(100, _global_trust(user_id)))
        db.conn.execute(
            "INSERT OR IGNORE INTO domain_trust_snapshot "
            "(user_id, domain, value, version, updated_at) VALUES (?, ?, ?, 0, ?)",
            (user_id, domain, value, _now()),
        )
        db.conn.commit()
    return value


def _daily_total(user_id: str, domain: str) -> int:
    from .userdb import db

    day = datetime.now().date().isoformat()
    with db._lock:
        row = db.conn.execute(
            "SELECT COALESCE(SUM(delta), 0) AS total FROM domain_trust_events "
            "WHERE user_id=? AND domain=? AND reverted_at IS NULL AND occurred_at >= ?",
            (user_id, domain, f"{day}T00:00:00"),
        ).fetchone()
    return int(row["total"]) if row else 0


def apply_domain_event(user_id: str, event_id: int, rule_id: str) -> dict | None:
    """唯一入口：按规则给对应领域记一次增量（幂等 user/event/domain）。"""
    rule = EVENT_DOMAIN_RULES.get(rule_id)
    if rule is None:
        return None
    domain, delta = rule
    from .userdb import db

    with db._lock:
        cur = db.conn.execute(
            "INSERT OR IGNORE INTO domain_trust_events "
            "(user_id, event_id, domain, delta, rule_version, occurred_at) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            (user_id, int(event_id), domain, RULE_VERSION, _now()),
        )
        if cur.rowcount == 0:
            return {"domain": domain, "value": _ensure_snapshot(user_id, domain),
                    "idempotent": True}
        # 日每域 ±4 截断（正负分别限幅）
        used = _daily_total(user_id, domain)
        room = _DAILY_CAP - used if delta > 0 else -_DAILY_CAP - used
        applied = delta if delta > 0 else -abs(delta)
        if delta > 0:
            applied = max(0, min(delta, room))
        else:
            applied = min(0, max(delta, -_DAILY_CAP - used))
        db.conn.execute(
            "UPDATE domain_trust_events SET delta=? WHERE id=?",
            (applied, int(cur.lastrowid)),
        )
        current = _ensure_snapshot(user_id, domain)
        value = max(0, min(100, current + applied))
        db.conn.execute(
            "UPDATE domain_trust_snapshot SET value=?, version=version+1, updated_at=? "
            "WHERE user_id=? AND domain=?",
            (value, _now(), user_id, domain),
        )
        db.conn.commit()
    logger.info("[领域信任] {} {} {} → {}", user_id, domain, applied, value)
    return {"domain": domain, "delta": applied, "value": value, "idempotent": False}


def get_snapshot(user_id: str) -> dict:
    """高层可依赖程度 + 每域最多 2 条来源（不暴露内部权重）。

    **只读**：缺失的域按当前全局 trust 现算返回，不落行——读路径有副作用会让
    临时轮/纯展示路径污染用户数据（2026-09-09 实测：行为帧调用本函数导致
    domain_trust_snapshot 多出 5 行）。落行只发生在 apply_domain_event。
    """
    from .userdb import db

    with db._lock:
        rows = db.conn.execute(
            "SELECT domain, value FROM domain_trust_snapshot WHERE user_id=?",
            (user_id,),
        ).fetchall()
        source_rows = db.conn.execute(
            "SELECT domain, event_id, delta, occurred_at FROM domain_trust_events "
            "WHERE user_id=? AND reverted_at IS NULL ORDER BY id DESC",
            (user_id,),
        ).fetchall()
    values = {str(r["domain"]): int(r["value"]) for r in rows}
    default_value = max(0, min(100, _global_trust(user_id)))
    for domain in DOMAINS:
        values.setdefault(domain, default_value)
    reasons: dict[str, list[dict]] = {}
    for row in source_rows:
        domain = str(row["domain"])
        bucket = reasons.setdefault(domain, [])
        if len(bucket) < 2:
            bucket.append({"event_id": int(row["event_id"]),
                           "delta": int(row["delta"]),
                           "occurred_at": str(row["occurred_at"])})
    return {
        "domains": [
            {"domain": d, "label": _DOMAIN_LABELS[d], "value": values.get(d, 0),
             "reasons": reasons.get(d, [])}
            for d in DOMAINS
        ],
    }


def reset_domain(user_id: str, domain: str) -> bool:
    """按当前全局 trust 重建某域（撤销事件后的一致性兜底）。"""
    if domain not in DOMAINS:
        raise ValueError(f"未知领域：{domain}")
    from .userdb import db

    value = max(0, min(100, _global_trust(user_id)))
    with db._lock:
        db.conn.execute(
            "UPDATE domain_trust_snapshot SET value=?, version=version+1, updated_at=? "
            "WHERE user_id=? AND domain=?",
            (value, _now(), user_id, domain),
        )
        db.conn.commit()
    return True


def behavior_hint(user_id: str) -> dict[str, float]:
    """领域只调制披露/求助/玩笑强度（不碰阶段边界与隐私开关）。"""
    snapshot = {item["domain"]: item["value"] for item in get_snapshot(user_id)["domains"]}
    hint: dict[str, float] = {}
    # 情绪陪伴低 → 少主动深挖；玩笑分寸低 → 少玩梗；事情托付低 → 少揽活
    if snapshot.get("emotional", 100) < 40:
        hint["probing"] = -0.05
    if snapshot.get("humor", 100) < 40:
        hint["humor"] = -0.05
    if snapshot.get("task", 100) < 40:
        hint["initiative"] = -0.05
    return hint
