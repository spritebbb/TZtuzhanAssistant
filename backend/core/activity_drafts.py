# -*- coding: utf-8 -*-
"""F06 聊天意图自动预填：用户明确「想一起做 X」时给一张可确认的草稿。

契约（docs/Zcode技术指导.md F06 + 调度文档批次 8）：

- 用户明确说「想一起做 X」才产草稿；普通提及（「昨天读了一本书」）不产；
- 草稿是**签名的短期引用**（默认 20 分钟、进程重启即失效），服务端不建草稿表；
- 前端可改 title/payload 后 POST /api/activity-drafts/confirm；服务端重新校验
  user/epoch/expiry 与活动互斥，再调用各权威 start 函数；
- 幂等：``activity_draft_receipts`` 记录 user/draft_id → activity_id，同草稿二次
  确认返回同一活动（小型 runtime 回执，reset 清除、不导出）；
- 用户取消草稿不影响任何活动；临时轮不产生可持久确认句柄；
- 开关关闭只停草稿输出，原手动入口保留。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta

from .log import logger

KINDS: tuple[str, ...] = ("goal", "writing", "song_list", "book_list")
DRAFT_TTL_MINUTES = 20
_MAX_TITLE = 60
_MAX_TEXT = 400

# payload 白名单：只允许各既有创建接口认识的字段
_PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    "goal": ("motivation", "next_step", "support_mode"),
    "writing": ("premise", "genre"),
    "song_list": ("theme",),
    "book_list": ("theme",),
}

# 明确邀请：必须出现「一起做/一起开始/想一起」这类意向词 + 可识别活动类型
_INVITE_RE = re.compile(r"想一起|一起做|一起开始|咱们一起|要不要一起|陪我一起|和我一起")
_KIND_RE: tuple[tuple[str, re.Pattern], ...] = (
    ("goal", re.compile(r"目标|计划|坚持|打卡|一起做点什么")),
    ("writing", re.compile(r"写故事|共同创作|一起写|故事|小说")),
    ("song_list", re.compile(r"歌单|一起听|挑歌|音乐")),
    ("book_list", re.compile(r"书单|一起读|共读|读书")),
)

_SECRET = secrets.token_bytes(32)  # 进程级密钥：重启即失效（设计允许）


class ActivityDraftError(ValueError):
    """活动草稿的预期业务错误。"""


def _now(moment: datetime | None = None) -> datetime:
    return moment or datetime.now()


def detect_draft_intent(text: str) -> tuple[str, str] | None:
    """明确邀请才返回 (kind, title)；普通提及返回 None。"""
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean or len(clean) > 300:
        return None
    if not _INVITE_RE.search(clean):
        return None
    for kind, pattern in _KIND_RE:
        if pattern.search(clean):
            return kind, clean[:_MAX_TITLE]
    return None


def _sign(payload: dict) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    token = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
    mac = hmac.new(_SECRET, token.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    return f"{token}.{mac}"


def _verify(token: str) -> dict | None:
    raw = str(token or "")
    if "." not in raw:
        return None
    body, _, mac = raw.rpartition(".")
    expected = hmac.new(_SECRET, body.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, json.JSONDecodeError):
        return None


def create_draft(user_id: str, kind: str, title: str, *, payload: dict | None = None,
                 source_turn_id: int | None = None,
                 now: datetime | None = None) -> dict | None:
    """生成签名草稿引用；不落库（服务端无草稿表，重启即过期）。"""
    from .features import flag

    if not flag("activity_drafts_enabled") or kind not in KINDS:
        return None
    moment = _now(now)
    clean_payload = {
        key: str((payload or {}).get(key) or "")[:_MAX_TEXT]
        for key in _PAYLOAD_KEYS.get(kind, ())
        if (payload or {}).get(key)
    }
    body = {
        "u": user_id,
        "k": kind,
        "t": str(title or "").strip()[:_MAX_TITLE],
        "p": clean_payload,
        "n": int(moment.timestamp()),
        "e": int((moment + timedelta(minutes=DRAFT_TTL_MINUTES)).timestamp()),
        "s": source_turn_id,
        "r": secrets.token_hex(4),  # 同一轮多次生成不撞 id
    }
    if not body["t"]:
        return None
    token = _sign(body)
    return {
        "draft_id": token,
        "kind": kind,
        "title": body["t"],
        "payload": clean_payload,
        "expires_at": datetime.fromtimestamp(body["e"]).isoformat(timespec="seconds"),
    }


def _draft_id_of(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()[:32]


def confirm_draft(user_id: str, token: str, *, title: str | None = None,
                  payload: dict | None = None,
                  now: datetime | None = None) -> dict:
    """确认草稿：重校验签名/过期/身份，幂等落账后调用权威 start 函数。"""
    from .reset import reset_epoch

    moment = _now(now)
    body = _verify(token)
    if body is None:
        raise ActivityDraftError("草稿无效或已过期，请重新生成")
    if str(body.get("u")) != user_id:
        raise ActivityDraftError("草稿不属于当前人格")
    if int(body.get("e") or 0) <= int(moment.timestamp()):
        raise ActivityDraftError("草稿已过期，请重新生成")
    kind = str(body.get("k") or "")
    if kind not in KINDS:
        raise ActivityDraftError("草稿类型不受支持")
    draft_id = _draft_id_of(token)
    # 幂等：同草稿二次确认返回同一活动
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT activity_id FROM activity_draft_receipts WHERE user_id=? AND draft_id=?",
            (user_id, draft_id),
        ).fetchone()
    if row is not None:
        return {"ok": True, "idempotent": True, "activity_id": int(row["activity_id"]),
                "kind": kind, "epoch": reset_epoch()}
    final_title = (title or str(body.get("t") or "")).strip()[:_MAX_TITLE]
    final_payload = dict(body.get("p") or {})
    for key in _PAYLOAD_KEYS.get(kind, ()):
        if (payload or {}).get(key) is not None:
            final_payload[key] = str(payload[key])[:_MAX_TEXT]
    if not final_title:
        raise ActivityDraftError("标题不能为空")
    activity = _start_authoritative(user_id, kind, final_title, final_payload)
    if activity is None:
        raise ActivityDraftError("创建失败，请手动重试")
    activity_id = int(activity.get("id") or 0)
    with db._lock:
        db.conn.execute(
            "INSERT OR IGNORE INTO activity_draft_receipts "
            "(user_id, draft_id, activity_id, created_at) VALUES (?, ?, ?, ?)",
            (user_id, draft_id, activity_id,
             moment.isoformat(timespec="seconds")),
        )
        db.conn.commit()
    logger.info("[活动草稿] 确认 {} → activity={}", kind, activity_id)
    return {"ok": True, "activity_id": activity_id, "kind": kind, "activity": activity}


def _start_authoritative(user_id: str, kind: str, title: str, payload: dict) -> dict | None:
    """复用既有权威创建函数（不新造第二套创建路径）。"""
    try:
        if kind == "goal":
            from .goals import start_goal

            return start_goal(
                user_id, title,
                motivation=str(payload.get("motivation") or ""),
                next_step=str(payload.get("next_step") or ""),
                support_mode=str(payload.get("support_mode") or "companion"),
            )
        if kind == "writing":
            from .cowriting import start_writing

            return start_writing(user_id, title)
        if kind == "song_list":
            from .colists import start_list

            return start_list(user_id, title, "song")
        if kind == "book_list":
            from .colists import start_list

            return start_list(user_id, title, "book")
    except Exception as exc:
        logger.warning("[活动草稿] 权威创建失败 {}：{}", kind, exc)
    return None


def active_mutex_notice(user_id: str, kind: str) -> str:
    """活动互斥告知：同壳有进行中的活动时，确认会先暂停它。"""
    from .userdb import db

    shell = {"goal": "goal", "writing": "writing",
             "song_list": "list", "book_list": "list"}.get(kind)
    if shell is None:
        return ""
    with db._lock:
        row = db.conn.execute(
            "SELECT title FROM activities WHERE user_id=? AND kind=? "
            "AND status IN ('active','paused') ORDER BY id DESC LIMIT 1",
            (user_id, shell),
        ).fetchone()
    if row is None:
        return ""
    return f"开始新的会先把正在进行的「{row['title']}」暂停，进度不会丢。"
