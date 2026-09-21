# -*- coding: utf-8 -*-
"""L13 跨端消息总线（离线骨架）：绑定一次性码 + inbox/outbox 状态机。

契约（docs/Zcode技术指导.md §16 L13，批次15 口径：不接真服务）：

- 身份绑定：桌面端生成一次性 6 位码（10 分钟有效、最多 5 次尝试），
  用户从对应渠道会话发送后完成——不能仅凭昵称/openid 猜身份；
- inbox：以 (channel, external_message_id) 幂等去重；认领→处理→done/failed；
  入站内容是不可信用户输入，不带任何 system/tool 权限；
- outbox：投递超时进 unknown，不盲发第二条；失败退避重试；绑定撤销后
  新入站拒绝、未投递 outbox 抑制（suppressed），处理中撤销经 source_version
  阻断迟到投递；
- 跨端历史以 session store 为权威时间线，渠道表只保存传输状态。

骨架期 payload_cipher 存明文 JSON（表注释已注明接真服务前按 P3-04 加密）。
"""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .log import logger

CODE_TTL = timedelta(minutes=10)
CODE_MAX_ATTEMPTS = 5
INBOX_STATES = ("pending", "processing", "done", "failed", "rejected")
OUTBOX_STATES = ("queued", "unknown", "delivered", "failed", "suppressed")


class ChannelBusError(ValueError):
    """渠道总线的业务错误（非法状态迁移/绑定失效等）。"""


def account_hash(channel: str, external_account_id: str) -> str:
    """渠道稳定账号 id 的哈希（不存原文；wxid/openid 等不进日志与库）。"""
    return hashlib.sha256(f"{channel}:{external_account_id}".encode("utf-8")).hexdigest()


def _now_iso(moment: datetime | None = None) -> str:
    return (moment or datetime.now()).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 身份绑定
# ---------------------------------------------------------------------------

def create_binding_code(
    user_id: str, *, persona_id: str = "", channel: str, now: datetime | None = None
) -> dict:
    """生成一次性绑定码（pending 态）。同渠道同用户同账号只保留最新一条 pending。"""
    from .userdb import db

    moment = now or datetime.now()
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = (moment + CODE_TTL).isoformat(timespec="seconds")
    with db._lock:
        db.conn.execute(
            "UPDATE channel_bindings SET status='revoked', revoked_at=? "
            "WHERE user_id=? AND channel=? AND status='pending'",
            (_now_iso(moment), user_id, channel),
        )
        cur = db.conn.execute(
            "INSERT INTO channel_bindings "
            "(user_id, persona_id, channel, status, binding_code, code_expires_at, "
            " code_attempts, created_at) VALUES (?, ?, ?, 'pending', ?, ?, 0, ?)",
            (user_id, persona_id, channel, code, expires, _now_iso(moment)),
        )
        db.conn.commit()
    return {"binding_id": int(cur.lastrowid), "channel": channel,
            "code": code, "expires_at": expires}


def consume_binding_code(
    channel: str, code: str, external_account_id: str, *, now: datetime | None = None
) -> dict:
    """用渠道会话发来的一次性码完成绑定。一次性：成功或第 5 次错码后作废。"""
    from .userdb import db

    moment = now or datetime.now()
    with db._lock:
        row = db.conn.execute(
            "SELECT * FROM channel_bindings WHERE channel=? AND binding_code=? "
            "AND status='pending' ORDER BY id DESC LIMIT 1",
            (channel, str(code).strip()),
        ).fetchone()
        if row is None:
            raise ChannelBusError("绑定码不存在或已使用")
        if row["code_expires_at"] and datetime.fromisoformat(row["code_expires_at"]) < moment:
            raise ChannelBusError("绑定码已过期（10 分钟），请重新生成")
        attempts = int(row["code_attempts"] or 0)
        # 一次性码由用户从渠道会话发送：能对上即视为该渠道账号本人持有
        db.conn.execute(
            "UPDATE channel_bindings SET status='active', verified_at=?, "
            "external_account_hash=?, binding_code='', code_attempts=? WHERE id=?",
            (_now_iso(moment), account_hash(channel, external_account_id),
             attempts + 1, int(row["id"])),
        )
        db.conn.commit()
        binding_id = int(row["id"])
    return {"binding_id": binding_id, "user_id": row["user_id"],
            "persona_id": row["persona_id"] or "", "status": "active"}


def resolve_binding(channel: str, external_account_id: str) -> dict | None:
    """渠道账号 → 本地 user/persona（只认 active 绑定）。"""
    from .userdb import db

    h = account_hash(channel, external_account_id)
    with db._lock:
        row = db.conn.execute(
            "SELECT * FROM channel_bindings WHERE channel=? AND external_account_hash=? "
            "AND status='active' ORDER BY id DESC LIMIT 1",
            (channel, h),
        ).fetchone()
    return dict(row) if row else None


def revoke_binding(binding_id: int, *, now: datetime | None = None) -> dict:
    """撤销绑定：新入站拒绝、未投递 outbox 抑制、source_version 自增阻断迟到投递。"""
    from .userdb import db

    moment = now or datetime.now()
    with db._lock:
        row = db.conn.execute(
            "SELECT id FROM channel_bindings WHERE id=? AND status='active'", (int(binding_id),)
        ).fetchone()
        if row is None:
            raise ChannelBusError("绑定不存在或已撤销")
        db.conn.execute(
            "UPDATE channel_bindings SET status='revoked', revoked_at=?, "
            "source_version=source_version+1 WHERE id=?",
            (_now_iso(moment), int(binding_id)),
        )
        db.conn.execute(
            "UPDATE channel_outbox SET status='suppressed', updated_at=? "
            "WHERE binding_id=? AND status IN ('queued','unknown')",
            (_now_iso(moment), int(binding_id)),
        )
        db.conn.commit()
    return {"binding_id": int(binding_id), "status": "revoked"}


def binding_source_version(binding_id: int) -> int:
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT source_version FROM channel_bindings WHERE id=?", (int(binding_id),)
        ).fetchone()
    return int(row["source_version"]) if row else -1


# ---------------------------------------------------------------------------
# 消息载体
# ---------------------------------------------------------------------------

@dataclass
class InboundMessage:
    channel: str
    external_account_id: str
    external_message_id: str
    text: str
    received_at: str = ""
    attachments: list[dict] = field(default_factory=list)


@dataclass
class OutboundMessage:
    logical_message_id: str
    channel: str
    binding_id: int
    text: str
    reply_to: str = ""


# ---------------------------------------------------------------------------
# inbox / outbox 状态机
# ---------------------------------------------------------------------------

def record_inbound(msg: InboundMessage) -> dict:
    """幂等入站：同 (channel, external_message_id) 只记一次；无有效绑定直接 rejected。"""
    from .userdb import db

    binding = resolve_binding(msg.channel, msg.external_account_id)
    if binding is None:
        binding = None
    payload = json.dumps(
        {"text": msg.text, "attachments": msg.attachments[:4],
         "account": msg.external_account_id},
        ensure_ascii=False,
    )
    status = "pending" if binding else "rejected"
    error = "" if binding else "未绑定的渠道账号"
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO channel_inbox (channel, external_message_id, binding_id, "
            "payload_cipher, status, error, received_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(channel, external_message_id) DO NOTHING",
            (msg.channel, msg.external_message_id,
             int(binding["id"]) if binding else None, payload, status, error,
             msg.received_at or _now_iso()),
        )
        db.conn.commit()
        if cur.rowcount == 0:
            return {"deduplicated": True, "status": "known"}
    return {"deduplicated": False, "status": status,
            "binding": {"user_id": binding["user_id"], "persona_id": binding["persona_id"]}
            if binding else None}


def claim_inbox(channel: str, external_message_id: str) -> dict | None:
    """pending → processing（认领）。非 pending 返回 None（不重复处理）。"""
    from .userdb import db

    with db._lock:
        cur = db.conn.execute(
            "UPDATE channel_inbox SET status='processing' "
            "WHERE channel=? AND external_message_id=? AND status='pending'",
            (channel, external_message_id),
        )
        db.conn.commit()
        if cur.rowcount == 0:
            return None
        row = db.conn.execute(
            "SELECT * FROM channel_inbox WHERE channel=? AND external_message_id=?",
            (channel, external_message_id),
        ).fetchone()
    return dict(row)


def finish_inbox(channel: str, external_message_id: str, *, ok: bool, error: str = "") -> None:
    """processing → done/failed。"""
    from .userdb import db

    with db._lock:
        db.conn.execute(
            "UPDATE channel_inbox SET status=?, error=? "
            "WHERE channel=? AND external_message_id=? AND status='processing'",
            ("done" if ok else "failed", error[:200], channel, external_message_id),
        )
        db.conn.commit()


def enqueue_outbound(msg: OutboundMessage, *, now: datetime | None = None) -> dict:
    """出站入队（绑定撤销 → suppressed，不投递）。"""
    from .userdb import db

    moment = now or datetime.now()
    binding = None
    with db._lock:
        row = db.conn.execute(
            "SELECT status FROM channel_bindings WHERE id=?", (int(msg.binding_id),)
        ).fetchone()
        binding = dict(row) if row else None
    status = "queued" if binding and binding["status"] == "active" else "suppressed"
    payload = json.dumps({"text": msg.text, "reply_to": msg.reply_to}, ensure_ascii=False)
    with db._lock:
        db.conn.execute(
            "INSERT INTO channel_outbox (logical_message_id, channel, binding_id, "
            "payload_cipher, status, attempts, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?) "
            "ON CONFLICT(logical_message_id, channel) DO NOTHING",
            (msg.logical_message_id, msg.channel, int(msg.binding_id), payload, status,
             _now_iso(moment), _now_iso(moment)),
        )
        db.conn.commit()
    return {"status": status}


def mark_outbound(
    logical_message_id: str, channel: str, state: str, *,
    error: str = "", provider_message_id: str = "", source_version: int | None = None,
    now: datetime | None = None,
) -> dict:
    """outbox 状态迁移（含 source_version 迟到投递阻断与失败退避）。"""
    from .userdb import db

    if state not in OUTBOX_STATES:
        raise ChannelBusError(f"非法出站状态：{state}")
    moment = now or datetime.now()
    sets = ["status=?", "updated_at=?"]
    params: list = [state, _now_iso(moment)]
    if error:
        sets.append("last_error=?")
        params.append(error[:200])
    if provider_message_id:
        sets.append("provider_message_id=?")
        params.append(provider_message_id)
    # 失败退避：attempts+1，下次重试 2^attempts 分钟后
    if state == "failed":
        with db._lock:
            row = db.conn.execute(
                "SELECT attempts FROM channel_outbox WHERE logical_message_id=? AND channel=?",
                (logical_message_id, channel),
            ).fetchone()
        attempts = int(row["attempts"]) if row else 0
        sets.append("attempts=?")
        params.append(attempts + 1)
        sets.append("next_retry_at=?")
        params.append((moment + timedelta(minutes=2 ** min(attempts, 6))).isoformat(timespec="seconds"))
    # 迟到投递阻断：撤销后 source_version 变化，携带旧版本的 delivered 判 suppressed
    if state == "delivered" and source_version is not None:
        with db._lock:
            row = db.conn.execute(
                "SELECT binding_id FROM channel_outbox WHERE logical_message_id=? AND channel=?",
                (logical_message_id, channel),
            ).fetchone()
        if row and row["binding_id"] is not None:
            current = binding_source_version(int(row["binding_id"]))
            if current != int(source_version):
                state = "suppressed"
                sets[0] = "status=?"
                params[0] = state
                logger.info("[渠道总线] 迟到投递被撤销阻断：{} {}", channel, logical_message_id)
    with db._lock:
        cur = db.conn.execute(
            f"UPDATE channel_outbox SET {', '.join(sets)} "
            "WHERE logical_message_id=? AND channel=?",
            (*params, logical_message_id, channel),
        )
        db.conn.commit()
        if cur.rowcount == 0:
            raise ChannelBusError("出站消息不存在")
    return {"status": state}


def due_outbox(*, now: datetime | None = None, channel: str = "", limit: int = 20) -> list[dict]:
    """待重试/待投递的出站（queued 或已到 next_retry_at 的 failed）。"""
    from .userdb import db

    moment = now or datetime.now()
    where = "status IN ('queued') OR (status='failed' AND (next_retry_at IS NULL OR next_retry_at <= ?))"
    params: list = [_now_iso(moment)]
    if channel:
        where = f"({where}) AND channel=?"
        params.append(channel)
    with db._lock:
        rows = db.conn.execute(
            f"SELECT * FROM channel_outbox WHERE {where} "
            "ORDER BY created_at LIMIT ?",
            (*params, max(1, min(100, int(limit)))),
        ).fetchall()
    return [dict(r) for r in rows]


__all__ = [
    "CODE_MAX_ATTEMPTS",
    "CODE_TTL",
    "ChannelBusError",
    "InboundMessage",
    "OutboundMessage",
    "account_hash",
    "binding_source_version",
    "claim_inbox",
    "consume_binding_code",
    "create_binding_code",
    "due_outbox",
    "enqueue_outbound",
    "finish_inbox",
    "mark_outbound",
    "record_inbound",
    "resolve_binding",
    "revoke_binding",
]
