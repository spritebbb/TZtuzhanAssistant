# -*- coding: utf-8 -*-
"""G04 求助与欲望：亲密与信任都到位时，她偶尔请对方帮一个小忙。

契约（docs/Zcode技术指导.md §14.9 G04 + 调度文档批次 6）：

- ``companion_requests(user_id, life_event_id, kind, status, offered_at,
  expires_at, response_message_id)``，kind 首版 song_choice / book_choice；
  status = candidate / offered / accepted / declined / expired；
- 候选门槛：intimacy ≥ 50 且 trust ≥ 50，且 7 天内至多一次；
- 候选不绕过 initiative：由主动仲裁投递（共享额度/勿扰/冷却），投递成功才置
  offered 并起 24h 倒计时；24h 无回复自动 expired，不再追问；
- 拒绝（含「随便/不想」）直接 declined，**不扣任何关系分**；
- 接受后只写角色虚构产物（artifacts, source_type='fiction'），与现实承诺账分离；
- 聊天意图与 API 走同一 respond 函数；
- LC-1：life 类别导出，life_event_id 引用 character_life_events；源删除使未完成
  请求失效；response_message_id 指向 messages（不入包，恢复时清空）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from .log import logger

KINDS: tuple[str, ...] = ("song_choice", "book_choice")
STATUSES: tuple[str, ...] = ("candidate", "offered", "accepted", "declined", "expired")

MIN_TRUST = 50
MIN_INTIMACY = 50
WEEKLY_LIMIT_DAYS = 7
OFFER_TTL_HOURS = 24

_KIND_LABEL = {"song_choice": "歌", "book_choice": "书"}
_ARTIFACT_TYPE = {"song_choice": "companion_song", "book_choice": "companion_book"}

# 聊天意图：只在有未过期 offer 时才解释，避免误判普通闲聊。
_ACCEPT_RE = re.compile(
    r"^\s*(?:好(?:的|啊|呀|吧)?|可以|行(?:啊|吧)?|没问题|听你的|你(?:来)?定|你选|要|来|试试|那就)"
)
_DECLINE_RE = re.compile(r"随便|不想|不用了|不用|算了|不要|没兴趣|下次吧|拒绝|别了")


class CompanionRequestError(ValueError):
    """求助请求的预期业务错误。"""


def _now(moment: datetime | None = None) -> datetime:
    return moment or datetime.now()


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _row_view(row) -> dict:
    return dict(row) if row is not None else {}


# ---- 候选生成（门槛 + 每周一次 + 生活源锚定） ----

def _dimensions(user_id: str) -> tuple[int, int]:
    try:
        from .affection import dimensions_of

        return dimensions_of(user_id)
    except Exception:
        return 0, 0


def _recent_request_exists(user_id: str, *, now: datetime) -> bool:
    from .userdb import db

    cutoff = (now - timedelta(days=WEEKLY_LIMIT_DAYS)).isoformat(timespec="seconds")
    with db._lock:
        row = db.conn.execute(
            "SELECT 1 FROM companion_requests WHERE user_id=? AND created_at >= ? LIMIT 1",
            (user_id, cutoff),
        ).fetchone()
    return row is not None


def _latest_life_event(user_id: str) -> int | None:
    """生活源锚：她最近的虚构日常事件（没有就不开口，不凭空要东西）。"""
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT id FROM character_life_events WHERE user_id=? "
            "ORDER BY occurred_at DESC, id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return int(row["id"]) if row is not None else None


def maybe_create_candidate(user_id: str, *, now: datetime | None = None) -> int | None:
    """门槛达标且有生活源时建候选；同生活源只出一次，7 天内至多一次。"""
    from .features import flag

    if not flag("companion_requests_enabled"):
        return None
    trust, intimacy = _dimensions(user_id)
    if trust < MIN_TRUST or intimacy < MIN_INTIMACY:
        return None
    moment = _now(now)
    if _recent_request_exists(user_id, now=moment):
        return None
    life_event_id = _latest_life_event(user_id)
    if life_event_id is None:
        return None
    from .userdb import db

    kind = KINDS[0] if int(trust + intimacy) % 2 == 0 else KINDS[1]
    with db._lock:
        exists = db.conn.execute(
            "SELECT 1 FROM companion_requests WHERE user_id=? AND life_event_id=? LIMIT 1",
            (user_id, life_event_id),
        ).fetchone()
        if exists is not None:
            return None
        cur = db.conn.execute(
            "INSERT INTO companion_requests "
            "(user_id, life_event_id, kind, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'candidate', ?, ?)",
            (user_id, life_event_id, kind, _iso(moment), _iso(moment)),
        )
        db.conn.commit()
    logger.info("[求助] 挂上候选 #{}（{}）", cur.lastrowid, kind)
    return int(cur.lastrowid)


def pending_candidate(user_id: str, *, now: datetime | None = None) -> dict | None:
    """取一个待投递候选（不改状态；投递成功由 mark_offered 落账）。"""
    from .userdb import db

    moment = _now(now)
    expire_stale(user_id, now=moment)
    with db._lock:
        row = db.conn.execute(
            "SELECT * FROM companion_requests WHERE user_id=? AND status='candidate' "
            "ORDER BY id LIMIT 1",
            (user_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def active_offer(user_id: str, *, now: datetime | None = None) -> dict | None:
    """当前有效的 offer（未回应且未过期），供聊天意图识别。"""
    from .userdb import db

    moment = _now(now)
    expire_stale(user_id, now=moment)
    with db._lock:
        row = db.conn.execute(
            "SELECT * FROM companion_requests WHERE user_id=? AND status='offered' "
            "AND expires_at > ? ORDER BY id DESC LIMIT 1",
            (user_id, _iso(moment)),
        ).fetchone()
    return dict(row) if row is not None else None


def mark_offered(user_id: str, request_id: int, *, now: datetime | None = None) -> bool:
    """投递成功后置 offered 并起 24h 倒计时。"""
    from .userdb import db

    moment = _now(now)
    with db._lock:
        cur = db.conn.execute(
            "UPDATE companion_requests SET status='offered', offered_at=?, expires_at=?, "
            "updated_at=? WHERE user_id=? AND id=? AND status='candidate'",
            (_iso(moment), _iso(moment + timedelta(hours=OFFER_TTL_HOURS)),
             _iso(moment), user_id, int(request_id)),
        )
        db.conn.commit()
    return bool(cur.rowcount)


def expire_stale(user_id: str, *, now: datetime | None = None) -> int:
    """超过 24h 未回应的 offer → expired；生活源已消失的未完成请求 → dismissed。"""
    from .userdb import db

    moment = _now(now)
    with db._lock:
        cur = db.conn.execute(
            "UPDATE companion_requests SET status='expired', updated_at=? "
            "WHERE user_id=? AND status='offered' AND expires_at <= ?",
            (_iso(moment), user_id, _iso(moment)),
        )
        db.conn.execute(
            "UPDATE companion_requests SET status='dismissed', updated_at=? "
            "WHERE user_id=? AND status IN ('candidate','offered') AND life_event_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM character_life_events e "
            "                WHERE e.id = companion_requests.life_event_id AND e.user_id = ?)",
            (_iso(moment), user_id, user_id),
        )
        db.conn.commit()
    return int(cur.rowcount)


def dismiss_for_source(user_id: str, life_event_id: int) -> int:
    """生活源被删除：未完成的请求直接失效（已接受的产物保留）。"""
    from .userdb import db

    with db._lock:
        cur = db.conn.execute(
            "UPDATE companion_requests SET status='dismissed', updated_at=? "
            "WHERE user_id=? AND life_event_id=? AND status IN ('candidate','offered')",
            (_iso(_now()), user_id, int(life_event_id)),
        )
        db.conn.commit()
    return int(cur.rowcount)


# ---- 回应（API 与聊天意图同一函数） ----

def classify_reply(text: str) -> str:
    """把用户回复归类为 accept / decline / unknown（确定性，无 LLM）。"""
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean or len(clean) > 200:
        return "unknown"
    if _DECLINE_RE.search(clean):
        return "decline"
    if _ACCEPT_RE.match(clean):
        return "accept"
    return "unknown"


def _write_artifact(user_id: str, request: dict, reply_text: str) -> dict | None:
    """接受后写角色虚构产物；与现实承诺账分离（source_type='fiction'）。"""
    from .userdb import db

    kind = str(request["kind"])
    label = _KIND_LABEL.get(kind, "东西")
    choice = re.sub(r"\s+", " ", str(reply_text or "")).strip()[:60]
    title = f"她收下你挑的{label}"
    content = (
        f"你替她挑的{label}：{choice}" if choice else f"你答应帮她挑一个{label}。"
    )
    now = _iso(_now())
    with db._lock:
        row = db.conn.execute(
            "SELECT COALESCE(MAX(source_id), 0) AS max_id FROM artifacts "
            "WHERE user_id = ? AND source_type = 'fiction'",
            (user_id,),
        ).fetchone()
        source_id = int(row["max_id"]) + 1
        cur = db.conn.execute(
            "INSERT INTO artifacts (user_id, artifact_type, source_type, source_id, "
            "title, content, version, created_at, updated_at, status) "
            "VALUES (?, ?, 'fiction', ?, ?, ?, 1, ?, ?, 'active')",
            (user_id, _ARTIFACT_TYPE.get(kind, "companion_choice"), source_id,
             title, content, now, now),
        )
        db.conn.commit()
        row = db.conn.execute(
            "SELECT id, artifact_type, title, content, created_at FROM artifacts WHERE id=?",
            (int(cur.lastrowid),),
        ).fetchone()
    return _row_view(row)


def respond(user_id: str, request_id: int, action: str, *,
            reply_text: str = "", message_id: int | None = None,
            now: datetime | None = None) -> dict:
    """接受/拒绝一次求助；幂等（已终态直接返回当前状态）。"""
    from .userdb import db

    if action not in {"accept", "decline"}:
        raise CompanionRequestError(f"未知动作：{action}")
    moment = _now(now)
    with db._lock:
        row = db.conn.execute(
            "SELECT * FROM companion_requests WHERE user_id=? AND id=?",
            (user_id, int(request_id)),
        ).fetchone()
        if row is None:
            raise CompanionRequestError("这条请求不存在")
        current = dict(row)
        if current["status"] in {"accepted", "declined", "expired", "dismissed"}:
            return {"ok": True, "status": current["status"], "idempotent": True}
        if current["status"] not in {"candidate", "offered"}:
            raise CompanionRequestError(f"状态不可回应：{current['status']}")
        status = "accepted" if action == "accept" else "declined"
        db.conn.execute(
            "UPDATE companion_requests SET status=?, response_message_id=?, updated_at=? "
            "WHERE user_id=? AND id=?",
            (status, message_id, _iso(moment), user_id, int(request_id)),
        )
        db.conn.commit()
    artifact = None
    if status == "accepted":
        try:
            artifact = _write_artifact(user_id, current, reply_text)
        except Exception as exc:  # 产物失败不影响状态与关系
            logger.warning("[求助] 产物写入失败 #{}：{}", request_id, exc)
    logger.info("[求助] #{} {}", request_id, "接受" if status == "accepted" else "拒绝")
    return {"ok": True, "status": status, "artifact": artifact}


def respond_to_reply(user_id: str, text: str, *, message_id: int | None = None,
                     now: datetime | None = None) -> dict | None:
    """聊天意图入口：有有效 offer 且回复能归类时才回应；否则 None（不打扰）。"""
    offer = active_offer(user_id, now=now)
    if not offer:
        return None
    intent = classify_reply(text)
    if intent == "unknown":
        return None
    return respond(user_id, int(offer["id"]), intent, reply_text=text,
                   message_id=message_id, now=now)


def list_requests(user_id: str, *, limit: int = 20) -> list[dict]:
    from .userdb import db

    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM companion_requests WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, max(1, min(100, int(limit)))),
        ).fetchall()
    return [dict(row) for row in rows]


def offer_prompt(user_id: str, request: dict) -> str:
    """给表达层的开口提示：只描述她想要什么，不写成任务清单。"""
    kind = str(request.get("kind") or KINDS[0])
    label = _KIND_LABEL.get(kind, "东西")
    return (
        f"你想让对方帮你挑一个{label}——就一个小请求，说完就把选择权交给他，"
        "不催、不追问、不解释为什么。一到两句，符合你此刻的人格与说话方式，"
        "别加括号动作，别列选项清单。"
    )
