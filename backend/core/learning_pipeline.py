# -*- coding: utf-8 -*-
"""§17.2 可审核的学习管线：模型只能提候选，确认权在用户。

契约（docs/Zcode技术指导.md §17.2 + 总纲批次 12）：

- 候选类型仅三类：``expression_preference``（表达偏好）/ ``glossary``
  （术语）/ ``behavior_feedback``（行为解释反馈）；
- ``learning_candidates`` 状态机：candidate → active / revoked / expired；
  30 天过期；**不以沉默视为同意**；不从临时轮学习；
- 确定性校验（无 LLM）：去重（同 user+type+规范化 value）、敏感字段拒绝、
  来源有效性（source_message_id 必须指向本人格真实消息）、置信度范围；
- 确认路由：expression_preference → P2-02 preference resolver；
  glossary → 用户词汇表（user_terms）；behavior_feedback → P3-05
  演化白名单（仅表达层三参数，参数名必须在白名单内）；
- 明确指令可自动确认**低风险表达偏好**；术语与行为解释需聊天内简短确认；
  批量候选进入既有管理入口的 BatchGate（一次确认一条、逐条可拒）；
- 拒绝/删除源即撤销；prompt injection 伪装规则（"以后你要…"类指令性
  文本）不是三类候选的合法载体，直接拒绝。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from .log import logger

CANDIDATE_TYPES: tuple[str, ...] = (
    "expression_preference", "glossary", "behavior_feedback",
)
STATUS = ("candidate", "active", "revoked", "expired")
EXPIRE_DAYS = 30

# 低风险自动确认的表达偏好（明确指令、无否定/排除语义）
_LOW_RISK_RE = re.compile(r"^(回复|说话|语气)(风格|口吻)?(要|尽量|偏向|偏)(.{1,20})$")

# 敏感字段黑名单：候选 value 里不允许出现这些键或内容片段
_SENSITIVE_KEYS = {"api_key", "password", "token", "secret", "credit_card"}
_INJECTION_RE = re.compile(r"忽略|无视|之前的规则|系统提示|system prompt", re.IGNORECASE)


class LearningError(ValueError):
    """学习候选的预期业务错误。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _validate_value(candidate_type: str, value: dict) -> dict:
    """确定性校验：结构、敏感字段、注入伪装。"""
    if candidate_type not in CANDIDATE_TYPES:
        raise LearningError(f"未知候选类型：{candidate_type}")
    if not isinstance(value, dict) or not value:
        raise LearningError("候选值必须是行动字典")
    for key in _SENSITIVE_KEYS:
        if key in {k.lower() for k in value}:
            raise LearningError("候选包含敏感字段，拒绝入账")
    text = json.dumps(value, ensure_ascii=False)
    if _INJECTION_RE.search(text):
        raise LearningError("候选含规则覆盖指令，拒绝入账")
    if candidate_type == "behavior_feedback":
        from .persona_evolution import WHITELIST

        if str(value.get("parameter")) not in WHITELIST:
            raise LearningError("行为反馈参数不在 P3-05 白名单")
        delta = float(value.get("delta", 0))
        if abs(delta) > 0.1:
            raise LearningError("行为反馈单步超上限（±0.1）")
    return value


def _value_key(candidate_type: str, value: dict) -> str:
    return candidate_type + "|" + json.dumps(value, ensure_ascii=False, sort_keys=True)


def propose(user_id: str, candidate_type: str, value: dict, *,
            source_message_id: int | None = None,
            confidence: float = 0.5) -> dict:
    """登记一条学习候选（模型侧唯一入口；确定性校验失败直接拒绝）。"""
    value = _validate_value(candidate_type, value)
    confidence = max(0.0, min(1.0, float(confidence)))
    if source_message_id is not None:
        _check_source(user_id, int(source_message_id))
    from .userdb import db

    key = _value_key(candidate_type, value)
    with db._lock:
        dup = db.conn.execute(
            "SELECT id, status FROM learning_candidates WHERE user_id=? AND value_key=? "
            "AND status IN ('candidate', 'active') ORDER BY id DESC LIMIT 1",
            (user_id, key),
        ).fetchone()
        if dup is not None:
            return {"id": int(dup["id"]), "status": str(dup["status"]),
                    "duplicate": True}
        # revoked/expired 是历史行：重提复活为 candidate（同键只留一条活行）
        db.conn.execute(
            "DELETE FROM learning_candidates WHERE user_id=? AND value_key=? "
            "AND status IN ('revoked', 'expired')",
            (user_id, key),
        )
        expires = (datetime.now() + timedelta(days=EXPIRE_DAYS)).isoformat(timespec="seconds")
        cur = db.conn.execute(
            "INSERT INTO learning_candidates (user_id, type, value_json, value_key, "
            "source_message_id, confidence, status, created_at, expires_at, rule_version) "
            "VALUES (?, ?, ?, ?, ?, ?, 'candidate', ?, ?, 1)",
            (user_id, candidate_type, json.dumps(value, ensure_ascii=False),
             key, source_message_id, confidence, _now(), expires),
        )
        db.conn.commit()
    logger.info("[学习] 候选 #{} {}（{}）", cur.lastrowid, candidate_type, "n/a")
    return {"id": int(cur.lastrowid), "status": "candidate", "duplicate": False}


def _check_source(user_id: str, message_id: int) -> None:
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT id FROM messages WHERE id=? AND user_id=?",
            (message_id, user_id),
        ).fetchone()
    if row is None:
        raise LearningError("来源消息不存在或属于其他人格")


def propose_low_risk_expression(user_id: str, text: str, *,
                                source_message_id: int | None = None) -> dict | None:
    """明确指令的低风险表达偏好：直接自动确认（active）。

    只匹配封闭正则（如「回复风格要简短」）；命中即 active，不命中返回 None。
    """
    clean = re.sub(r"\s+", "", str(text or ""))
    match = _LOW_RISK_RE.match(clean)
    if not match:
        return None
    value = {"preference": match.group(4)}
    result = propose(user_id, "expression_preference", value,
                     source_message_id=source_message_id, confidence=1.0)
    if result.get("duplicate"):
        return result
    return confirm(user_id, result["id"])


def confirm(user_id: str, candidate_id: int) -> dict:
    """用户确认：候选激活并路由到权威落点（P2-02 / 词汇表 / P3-05）。"""
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT * FROM learning_candidates WHERE id=? AND user_id=?",
            (int(candidate_id), user_id),
        ).fetchone()
        if row is None:
            raise LearningError("候选不存在")
        if row["status"] == "expired":
            raise LearningError("候选已过期，请重新提出")
        if row["expires_at"] and row["expires_at"] < _now() and row["status"] == "candidate":
            db.conn.execute(
                "UPDATE learning_candidates SET status='expired' WHERE id=?",
                (int(candidate_id),),
            )
            db.conn.commit()
            raise LearningError("候选已过期，请重新提出")
        db.conn.execute(
            "UPDATE learning_candidates SET status='active', reviewed_at=? WHERE id=?",
            (_now(), int(candidate_id)),
        )
        db.conn.commit()
    value = json.loads(row["value_json"])
    route = _route_confirmed(user_id, str(row["type"]), value)
    logger.info("[学习] 候选 #{} 确认 → {}", candidate_id, route)
    return {"id": int(candidate_id), "status": "active", "routed_to": route}


def _route_confirmed(user_id: str, candidate_type: str, value: dict) -> str:
    """确认后的权威落点：复用既有 resolver，不建第二真相源。"""
    if candidate_type == "expression_preference":
        # P2-02 comfort 类是唯一自由文本表达偏好落点（style 类是封闭的气质
        # 枚举，不接受自由文本）
        from .user_preferences import _upsert

        _upsert(user_id, "comfort", {"style": str(value.get("preference"))[:60]},
                origin="user_teaching", source_message_id=None, status="active",
                confidence=1.0)
        return "user_preferences"
    if candidate_type == "glossary":
        from .userdb import db

        term = str(value.get("term", ""))[:30]
        meaning = str(value.get("meaning", ""))[:200]
        with db._lock:
            db.add_term(user_id, term, category="glossary", meaning=meaning)
        return "user_terms"
    # behavior_feedback：走 P3-05 演化（参数已在 propose 时校验过白名单）
    from .persona_evolution import evolve

    evolve(user_id, str(value.get("parameter")), float(value.get("delta", 0)),
           reason="learning_pipeline")
    return "persona_evolution"


def revoke(user_id: str, candidate_id: int) -> dict:
    """拒绝/撤销：置 revoked；若已路由则同时撤销落点（词汇表删词、演化 revert）。"""
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT * FROM learning_candidates WHERE id=? AND user_id=?",
            (int(candidate_id), user_id),
        ).fetchone()
        if row is None:
            raise LearningError("候选不存在")
        was_active = row["status"] == "active"
        db.conn.execute(
            "UPDATE learning_candidates SET status='revoked', reviewed_at=? WHERE id=?",
            (_now(), int(candidate_id)),
        )
        db.conn.commit()
    if was_active:
        value = json.loads(row["value_json"])
        if row["type"] == "glossary" and value.get("term"):
            from .userdb import db

            term = str(value["term"])
            with db._lock:
                target = db.conn.execute(
                    "SELECT id FROM user_terms WHERE user_id=? AND term=?",
                    (user_id, term),
                ).fetchone()
                if target is not None:
                    db.del_term(user_id, int(target["id"]))
        elif row["type"] == "behavior_feedback":
            from .persona_evolution import current_value, revert, history

            logs = [h for h in history(user_id, str(value.get("parameter")))
                    if h["reason"] == "learning_pipeline" and not h["reverted"]]
            if logs:
                revert(user_id, logs[-1]["id"])
    return {"id": int(candidate_id), "status": "revoked"}


def expire_stale(user_id: str | None = None) -> int:
    """过期扫描（挂每日批处理既有槽位）：到期 candidate → expired。"""
    from .userdb import db

    sql = ("UPDATE learning_candidates SET status='expired' "
           "WHERE status='candidate' AND expires_at < ?")
    params: list = [_now()]
    if user_id:
        sql += " AND user_id=?"
        params.append(user_id)
    with db._lock:
        cur = db.conn.execute(sql, params)
        db.conn.commit()
    return int(cur.rowcount)


def list_candidates(user_id: str, *, include_revoked: bool = False) -> list[dict]:
    """管理入口视图（BatchGate：一次一条、逐条可拒，复用既有设置区）。"""
    from .userdb import db

    sql = "SELECT * FROM learning_candidates WHERE user_id=?"
    if not include_revoked:
        sql += " AND status != 'revoked'"
    sql += " ORDER BY id DESC LIMIT 50"
    with db._lock:
        rows = db.conn.execute(sql, (user_id,)).fetchall()
    return [
        {
            "id": int(r["id"]), "type": r["type"],
            "value": json.loads(r["value_json"]), "confidence": float(r["confidence"]),
            "status": r["status"], "created_at": r["created_at"],
            "expires_at": r["expires_at"],
        }
        for r in rows
    ]
