# -*- coding: utf-8 -*-
"""P3-05A 演化日志：白名单参数的小步演化，显式可撤销、可重算。

契约（docs/Zcode技术指导.md P3-05 + 2026-09-07 用户拍板）：

- 白名单首版**仅表达层**三参数：``humor_usage_rate`` / ``verbosity_preference``
  / ``initiative_template_weight``——都能被 P0-03 eval 直接测量，漂移可被发现，
  回滚无副作用；身份、安全、隐私、权限参数永不入白名单；
- 小步上限（每步 |Δ| ≤ 0.1）+ 冷却（同参数 24h 内不再演化）；
- 每次演化记录 (old, new, source_event_id, rule_version)，撤销置 reverted_at；
- **撤销重算**：不做「把数值写回很久前的 old」——重放该参数全部未撤销演化
  （源已删除的演化按失效处理，偏移归零），得到唯一当前值；源删除后对应
  偏移自动失效；
- 演化历史随 life 类别导出（运行统计不导出，见 experience_metrics）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .log import logger

# 白名单（2026-09-07 用户拍板：仅表达层）。
# 每参数：名称 → (初始值, 最小值, 最大值)
WHITELIST: dict[str, tuple[float, float, float]] = {
    "humor_usage_rate": (0.5, 0.0, 1.0),
    "verbosity_preference": (0.5, 0.0, 1.0),
    "initiative_template_weight": (0.5, 0.0, 1.0),
}

RULE_VERSION = 1
_MAX_STEP = 0.1          # 单步上限
_COOLDOWN_HOURS = 24     # 同参数演化冷却


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def current_value(user_id: str, parameter: str) -> float:
    """当前值 = 重放全部有效演化（幂等推导，不存快照列）。

    重放按「有效链」而非逐条取 new：撤销链条中段时，后续以被撤销条目的
    new 为 old 的演化同样失效（其前提已不存在），只有从未被撤销条目出发、
    连续有效的那条链才计入。
    """
    if parameter not in WHITELIST:
        raise ValueError(f"参数不在白名单：{parameter}")
    base = WHITELIST[parameter][0]
    from .userdb import db

    with db._lock:
        rows = db.conn.execute(
            "SELECT id, source_event_id, old, new FROM persona_evolution_log "
            "WHERE user_id=? AND parameter=? AND reverted_at IS NULL "
            "ORDER BY id",
            (user_id, parameter),
        ).fetchall()
    value = base
    for row in rows:
        # 源已删除/作废的演化偏移失效（source_event_id 非空时校验事件仍存在）
        if row["source_event_id"] is not None and not _event_alive(int(row["source_event_id"])):
            continue
        # 前提链断裂：old 不等于当前推导值 → 该演化以被撤销/失效条目为前提，
        # 同样失效（撤销重算语义，不简单写回 old）
        if abs(float(row["old"]) - value) > 1e-9:
            continue
        value = float(row["new"])
    return _clamp(value, *WHITELIST[parameter][1:])


def _event_alive(event_id: int) -> bool:
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT id, status FROM relationship_events WHERE id=?", (event_id,)
        ).fetchone()
    return row is not None and row["status"] == "active"


def _last_change_at(user_id: str, parameter: str) -> str | None:
    """最近一次真实入账时间（撤销/幂等跳过不算——不进入冷却）。"""
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT created_at FROM persona_evolution_log "
            "WHERE user_id=? AND parameter=? AND reverted_at IS NULL AND delta!=0 "
            "ORDER BY id DESC LIMIT 1",
            (user_id, parameter),
        ).fetchone()
    return str(row["created_at"]) if row else None


def _in_cooldown(user_id: str, parameter: str, now: datetime) -> bool:
    last = _last_change_at(user_id, parameter)
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return False
    # created_at 只精确到秒：与 last 同一秒内的演化视为同刻连发（测试同秒
    # 连发两次合法演化需放行），冷却从下一秒起算。
    if (now - last_dt).total_seconds() < 1.0:
        return False
    return now - last_dt < timedelta(hours=_COOLDOWN_HOURS)


def evolve(user_id: str, parameter: str, delta: float, *,
           source_event_id: int | None = None,
           reason: str = "") -> dict | None:
    """对白名单参数做一次小步演化（幂等 source_event；冷却与小步上限约束）。"""
    if parameter not in WHITELIST:
        raise ValueError(f"参数不在白名单：{parameter}")
    delta = _clamp(float(delta), -_MAX_STEP, _MAX_STEP)
    if abs(delta) < 1e-9:
        return None
    from .userdb import db

    now = datetime.now()
    with db._lock:
        # 幂等：同一 source_event 只演化一次（幂等跳过不进冷却；测试同秒连
        # 发两次演化也放行——冷却以「最后一次真实入账」计，同秒差不为负）
        if source_event_id is not None:
            dup = db.conn.execute(
                "SELECT id, reverted_at FROM persona_evolution_log WHERE user_id=? "
                "AND parameter=? AND source_event_id=?",
                (user_id, parameter, int(source_event_id)),
            ).fetchone()
            if dup is not None and dup["reverted_at"] is None:
                return {"applied": False, "reason": "idempotent",
                        "value": current_value(user_id, parameter)}
        if _in_cooldown(user_id, parameter, now):
            return {"applied": False, "reason": "cooldown",
                    "value": current_value(user_id, parameter)}
        old = current_value(user_id, parameter)
        new = _clamp(old + delta, *WHITELIST[parameter][1:])
        cur = db.conn.execute(
            "INSERT INTO persona_evolution_log "
            "(user_id, source_event_id, parameter, old, new, delta, rule_version, "
            "reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, source_event_id, parameter, old, new, round(new - old, 6),
             RULE_VERSION, reason[:200], _now()),
        )
        db.conn.commit()
    logger.info("[演化] {} {} {} → {}（{}）", user_id, parameter, old, new,
                reason or "unspecified")
    return {"applied": True, "id": int(cur.lastrowid), "old": old, "new": new,
            "value": new}


def revert(user_id: str, log_id: int) -> dict:
    """撤销一次演化并重算当前值（不覆盖历史，重放得到唯一当前值）。"""
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT id, parameter FROM persona_evolution_log WHERE id=? AND user_id=?",
            (int(log_id), user_id),
        ).fetchone()
        if row is None:
            raise ValueError("演化记录不存在")
        db.conn.execute(
            "UPDATE persona_evolution_log SET reverted_at=? WHERE id=? AND reverted_at IS NULL",
            (_now(), int(log_id)),
        )
        db.conn.commit()
    parameter = str(row["parameter"])
    return {"parameter": parameter, "value": current_value(user_id, parameter)}


def history(user_id: str, parameter: str | None = None, limit: int = 30) -> list[dict]:
    """演化历史（给用户的可解释视图：只报参数/方向/时间/是否已撤销）。"""
    from .userdb import db

    limit = max(1, min(100, int(limit)))
    sql = ("SELECT id, parameter, old, new, delta, reason, created_at, reverted_at, "
           "source_event_id FROM persona_evolution_log WHERE user_id=?")
    params: list = [user_id]
    if parameter:
        sql += " AND parameter=?"
        params.append(parameter)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with db._lock:
        rows = db.conn.execute(sql, params).fetchall()
    return [
        {
            "id": int(r["id"]), "parameter": r["parameter"],
            "old": float(r["old"]), "new": float(r["new"]), "delta": float(r["delta"]),
            "reason": r["reason"], "created_at": r["created_at"],
            "reverted": r["reverted_at"] is not None,
            "source_event_id": int(r["source_event_id"]) if r["source_event_id"] is not None else None,
        }
        for r in rows
    ]
