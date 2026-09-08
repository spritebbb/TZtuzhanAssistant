# -*- coding: utf-8 -*-
"""G03 悬念/开放问题：用户明确要「有结果告诉我」的事，她记着并有限度地复查。

契约（docs/Zcode技术指导.md §14.9 G03 + 调度文档批次 5）：

- ``open_questions`` 全状态机：open / researching / resolved / dismissed / expired；
- **首次只在用户明确要求追踪时保存**（「以后有结果告诉我 / 帮我继续查」）；
  一般的「我不知道」正常回复即可，不偷偷长期追踪；
- 7 天到期、最多 2 次复查、最小间隔 24 小时；
- 复查走 JOB-1 认领（job_runs 租约，防跨进程双跑）；
- ``evidence_hash`` = 排序后证据条目（canonical_url + title + 摘录）的规范化
  哈希，**不含模型叙述**；证据散列未变不发「假进展」；
- 有新证据才置 resolved，并经 pending_thoughts 候选 + 共享额度表达；
- 源消息消失立即 dismissed；
- LC-1 导出进「关系待办」类别（tasks）。

自行补充的决策：触发只接「用户明确要求追踪」这一条真实路径——§14.9 明确
「一般『我不知道』正常回复但不偷偷长期追踪」，故不做「证据不足自动建单」。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta

from .log import logger

STATUSES: tuple[str, ...] = ("open", "researching", "resolved", "dismissed", "expired")

EXPIRE_DAYS = 7
MAX_ATTEMPTS = 2
MIN_INTERVAL_HOURS = 24
_TOPIC_MAX = 120
JOB_KEY = "open_question_research"

# 只在用户明确要求「以后告诉我 / 继续查」时建单；普通提问与「我不知道」不追踪。
_TRACK_RE = re.compile(
    r"(?:有|查到|找到|查出来)[^，。！？]{0,8}(?:告诉|通知|说一声|跟我)"
    r"|帮我(?:继续)?查|继续(?:帮我)?查|再帮我查|查到了?(?:告诉|跟)我"
    r"|有结果(?:了)?(?:告诉|通知|跟)我|帮我留意"
)


class OpenQuestionError(ValueError):
    """开放问题的预期业务错误。"""


def _now_iso(moment: datetime | None = None) -> str:
    return (moment or datetime.now()).isoformat(timespec="seconds")


def _normalize_topic(topic: str) -> str:
    return re.sub(r"\s+", " ", str(topic or "")).strip().lower()


def detect_tracking_request(text: str) -> str | None:
    """用户明确要求追踪才返回 topic（原始表述，截断 120 字）；否则 None。"""
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean or len(clean) > 300:
        return None
    if not _TRACK_RE.search(clean):
        return None
    return clean[:_TOPIC_MAX]


def track_question(user_id: str, topic: str, *, source_message_id: int | None = None,
                   now: datetime | None = None) -> int | None:
    """建单；同用户同主题仍有未结单时幂等返回 None（不重复追问）。"""
    key = _normalize_topic(topic)
    if not key:
        return None
    from .userdb import db

    moment = now or datetime.now()
    expires_at = (moment + timedelta(days=EXPIRE_DAYS)).isoformat(timespec="seconds")
    next_check = (moment + timedelta(hours=MIN_INTERVAL_HOURS)).isoformat(timespec="seconds")
    with db._lock:
        exists = db.conn.execute(
            "SELECT 1 FROM open_questions WHERE user_id=? AND topic_key=? "
            "AND status IN ('open','researching')",
            (user_id, key),
        ).fetchone()
        if exists is not None:
            return None
        cur = db.conn.execute(
            "INSERT INTO open_questions "
            "(user_id, source_message_id, topic, topic_key, status, next_check_at, "
            "expires_at, attempts, last_evidence_hash, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'open', ?, ?, 0, NULL, ?, ?)",
            (user_id, source_message_id, str(topic)[:_TOPIC_MAX], key,
             next_check, expires_at, _now_iso(moment), _now_iso(moment)),
        )
        db.conn.commit()
    logger.info("[开放问题] 记下待查：{}", str(topic)[:40])
    return int(cur.lastrowid)


def list_questions(user_id: str, *, status: str | None = None,
                   limit: int = 20) -> list[dict]:
    from .userdb import db

    sql = "SELECT * FROM open_questions WHERE user_id=?"
    params: list[object] = [user_id]
    if status is not None:
        if status not in STATUSES:
            raise OpenQuestionError(f"未知状态：{status}")
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(100, int(limit))))
    with db._lock:
        rows = db.conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def get_question(user_id: str, question_id: int) -> dict | None:
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT * FROM open_questions WHERE user_id=? AND id=?",
            (user_id, int(question_id)),
        ).fetchone()
    return dict(row) if row is not None else None


def _source_alive(user_id: str, source_message_id: int | None) -> bool:
    if not source_message_id:
        return True  # 没有来源锚（用户口头要求）不算源消失
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT 1 FROM messages WHERE user_id=? AND id=?",
            (user_id, int(source_message_id)),
        ).fetchone()
    return row is not None


def due_questions(user_id: str, *, now: datetime | None = None,
                  limit: int = 5) -> list[dict]:
    """到点复查且仍在有效期内的单；源消息消失的先作废（返回空）。"""
    from .userdb import db

    moment = now or datetime.now()
    now_iso = moment.isoformat(timespec="seconds")
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM open_questions WHERE user_id=? AND status IN ('open','researching') "
            "AND next_check_at IS NOT NULL AND next_check_at <= ? AND expires_at > ? "
            "AND attempts < ? ORDER BY next_check_at, id LIMIT ?",
            (user_id, now_iso, now_iso, MAX_ATTEMPTS, max(1, min(20, int(limit)))),
        ).fetchall()
    out: list[dict] = []
    for row in rows:
        item = dict(row)
        if not _source_alive(user_id, item.get("source_message_id")):
            dismiss_question(user_id, int(item["id"]), reason="source_gone", now=moment)
            continue
        out.append(item)
    return out


def dismiss_question(user_id: str, question_id: int, *, reason: str = "",
                     now: datetime | None = None) -> bool:
    """作废（用户放弃 / 源消息消失 / 到期）。"""
    from .userdb import db

    with db._lock:
        cur = db.conn.execute(
            "UPDATE open_questions SET status='dismissed', updated_at=? "
            "WHERE user_id=? AND id=? AND status IN ('open','researching')",
            (_now_iso(now), user_id, int(question_id)),
        )
        db.conn.commit()
    if cur.rowcount:
        logger.info("[开放问题] 作废 #{}（{}）", question_id, reason or "user")
    return bool(cur.rowcount)


def expire_stale(user_id: str, *, now: datetime | None = None) -> int:
    """超过 7 天有效期或复查次数用尽的单 → expired（静默，不补发）。"""
    from .userdb import db

    moment = now or datetime.now()
    now_iso = moment.isoformat(timespec="seconds")
    with db._lock:
        cur = db.conn.execute(
            "UPDATE open_questions SET status='expired', updated_at=? "
            "WHERE user_id=? AND status IN ('open','researching') "
            "AND (expires_at <= ? OR attempts >= ?)",
            (now_iso, user_id, now_iso, MAX_ATTEMPTS),
        )
        db.conn.commit()
    return int(cur.rowcount)


# ---- JOB-1 认领（跨进程防双跑；租约过期可恢复） ----

LEASE_SECONDS = 120


def _claim(conn, scope_key: str, period: str, owner: str, now: datetime) -> bool:
    conn.execute(
        "INSERT OR IGNORE INTO job_runs (scope_key, job_key, period_start, status, "
        "attempt, created_at, updated_at) VALUES (?, ?, ?, 'pending', 0, ?, ?)",
        (scope_key, JOB_KEY, period, _now_iso(now), _now_iso(now)),
    )
    cur = conn.execute(
        "UPDATE job_runs SET status='running', lease_owner=?, lease_until=?, "
        "attempt=attempt+1, updated_at=? "
        "WHERE scope_key=? AND job_key=? AND period_start=? "
        "AND ((status IN ('pending','failed') "
        "      AND (next_retry IS NULL OR next_retry<=? OR next_retry='')) "
        "  OR (status='running' AND lease_until IS NOT NULL AND lease_until<?))",
        (owner, _now_iso(now + timedelta(seconds=LEASE_SECONDS)), _now_iso(now),
         scope_key, JOB_KEY, period, _now_iso(now), _now_iso(now)),
    )
    conn.commit()
    return cur.rowcount == 1


def _finish(conn, scope_key: str, period: str, ok: bool, now: datetime) -> None:
    conn.execute(
        "UPDATE job_runs SET status=?, lease_owner=NULL, lease_until=NULL, "
        "finished_at=?, updated_at=? WHERE scope_key=? AND job_key=? AND period_start=?",
        ("succeeded" if ok else "failed", _now_iso(now), _now_iso(now),
         scope_key, JOB_KEY, period),
    )
    conn.commit()


def _scope_key() -> str:
    try:
        from .persona_profiles import active_id

        return f"persona::{active_id()}"
    except Exception:
        return "persona::default"


# ---- 证据哈希与复查 ----

def evidence_hash(evidence: list[dict]) -> str:
    """排序后证据条目（canonical_url + title + 摘录）的规范化哈希。

    不含模型叙述，也不含顺序：同一批来源无论先后都得到同一哈希。
    """
    from .search import normalize_query
    from .source_verification import canonical_url

    parts: list[str] = []
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        url = canonical_url(str(item.get("url") or ""))
        title = normalize_query(str(item.get("title") or ""))
        snippet = normalize_query(str(item.get("snippet") or ""))[:200]
        if not (url or title or snippet):
            continue
        parts.append(f"{url}|{title}|{snippet}")
    if not parts:
        return ""
    joined = "\n".join(sorted(parts))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _result_line(evidence: list[dict]) -> str:
    """给表达层的来源摘要：只列来源标题与域名，不写模型结论。"""
    lines = []
    for item in (evidence or [])[:2]:
        title = str(item.get("title") or "").strip()[:40]
        domain = str(item.get("domain") or "").strip()
        if title:
            lines.append(f"《{title}》" + (f"（{domain}）" if domain else ""))
    return "、".join(lines)


def research_question(user_id: str, question_id: int, *, search_fn=None, judge=None,
                      now: datetime | None = None) -> dict:
    """复查一单：JOB-1 认领 → 联网求证 → 证据哈希比对。

    证据散列未变或没有证据：只记 attempts / 推迟下次复查，**不发假进展**；
    有新证据：置 resolved 并经 pending_thoughts 候选交给主动仲裁表达。
    """
    from .userdb import db

    moment = now or datetime.now()
    question = get_question(user_id, question_id)
    if question is None:
        return {"status": "missing"}
    if question["status"] not in ("open", "researching"):
        return {"status": "skipped", "reason": question["status"]}
    if str(question["expires_at"]) <= _now_iso(moment) or int(question["attempts"]) >= MAX_ATTEMPTS:
        expire_stale(user_id, now=moment)
        return {"status": "expired"}

    period = f"q{int(question_id)}:a{int(question['attempts']) + 1}"
    owner = f"research-{os.getpid()}-{int(moment.timestamp())}"
    scope = _scope_key()
    with db._lock:
        claimed = _claim(db.conn, scope, period, owner, moment)
    if not claimed:
        return {"status": "busy"}  # 另一进程/另一轮正在复查

    try:
        from .source_verification import verify_search

        report = verify_search(
            str(question["topic"]), search_fn=search_fn or _default_search, judge=judge
        )
    except Exception as exc:
        logger.warning("[开放问题] 复查失败 #{}：{}", question_id, exc)
        report = {"status": "failed", "evidence": []}

    evidence = list(report.get("evidence") or [])
    new_hash = evidence_hash(evidence)
    previous = str(question.get("last_evidence_hash") or "")
    attempts = int(question["attempts"]) + 1
    ok = True
    try:
        if evidence and new_hash and new_hash != previous:
            with db._lock:
                db.conn.execute(
                    "UPDATE open_questions SET status='resolved', attempts=?, "
                    "last_evidence_hash=?, updated_at=? WHERE user_id=? AND id=?",
                    (attempts, new_hash, _now_iso(moment), user_id, int(question_id)),
                )
                db.conn.commit()
            _queue_result_thought(user_id, question, evidence, now=moment)
            result = {"status": "resolved", "evidence_count": len(evidence),
                      "sources": [str(item.get("domain") or "") for item in evidence[:2]]}
        else:
            # 无新证据：不表达、不编进展，只推进复查预算与下次时间
            next_check = (moment + timedelta(hours=MIN_INTERVAL_HOURS)).isoformat(timespec="seconds")
            expired = attempts >= MAX_ATTEMPTS
            with db._lock:
                db.conn.execute(
                    "UPDATE open_questions SET status=?, attempts=?, next_check_at=?, "
                    "updated_at=? WHERE user_id=? AND id=?",
                    ("expired" if expired else "open", attempts, next_check,
                     _now_iso(moment), user_id, int(question_id)),
                )
                db.conn.commit()
            result = {"status": "no_new_evidence", "attempts": attempts}
    except Exception:
        ok = False
        raise
    finally:
        with db._lock:
            _finish(db.conn, scope, period, ok, moment)
    return result


def _default_search(query: str, **kwargs):
    from .search import web_search

    return web_search(query, **kwargs)


def _queue_result_thought(user_id: str, question: dict, evidence: list[dict], *,
                          now: datetime | None = None) -> int | None:
    """有新证据才挂候选：内容只含来源摘要，由主动仲裁决定何时表达。"""
    summary = _result_line(evidence)
    if not summary:
        return None
    from .pending_thoughts import _add

    return _add(
        user_id, "open_question_result", "open_question", int(question["id"]),
        f"你之前让我留意的事「{str(question['topic'])[:40]}」有结果了：{summary}",
        earliest_at=_now_iso(now), priority=3,
    )


def research_due_questions(user_id: str, *, now: datetime | None = None,
                           search_fn=None, judge=None, limit: int = 2) -> dict:
    """每日批处理入口：复查到点的单（最多 limit 单，含到期清理）。"""
    moment = now or datetime.now()
    expire_stale(user_id, now=moment)
    results: list[dict] = []
    for question in due_questions(user_id, now=moment, limit=limit):
        try:
            results.append(research_question(
                user_id, int(question["id"]), search_fn=search_fn, judge=judge, now=moment))
        except Exception as exc:
            logger.warning("[开放问题] 复查异常 #{}：{}", question["id"], exc)
            results.append({"status": "error"})
    return {"checked": len(results), "results": results}
