# -*- coding: utf-8 -*-
"""L12 设备注册与会话（离线骨架）。remote_gateway_enabled 默认关：
表结构与会话签发/校验先行落地，接真网关时由 gateway 进程调用。"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

from .log import logger

SESSION_TTL = timedelta(days=30)


def _now_iso(moment: datetime | None = None) -> str:
    return (moment or datetime.now()).isoformat(timespec="seconds")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def register_device(user_id: str, *, display_name: str = "",
                    platform: str = "") -> dict:
    """注册设备，返回一次性会话 token（token_hash 入库，原文只在响应出现）。"""
    from .userdb import db

    device_id = uuid.uuid4().hex[:12]
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + SESSION_TTL).isoformat(timespec="seconds")
    now = _now_iso()
    with db._lock:
        db.conn.execute(
            "INSERT INTO devices (id, user_id, display_name, platform, created_at, "
            "last_seen_at) VALUES (?, ?, ?, ?, ?, '')",
            (device_id, user_id, display_name, platform, now),
        )
        db.conn.execute(
            "INSERT INTO auth_sessions (id, device_id, token_hash, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().hex[:12], device_id, _sha256(token), expires, now),
        )
        db.conn.commit()
    return {"device_id": device_id, "session_token": token, "expires_at": expires}


def verify_session(token: str) -> dict | None:
    """token → 设备与用户（撤销/过期 → None；命中即刷新 last_seen）。"""
    from .userdb import db

    now_iso = _now_iso()
    with db._lock:
        row = db.conn.execute(
            "SELECT s.id AS session_id, s.expires_at, s.revoked_at, d.id AS device_id, "
            "d.user_id, d.revoked_at AS device_revoked "
            "FROM auth_sessions s JOIN devices d ON d.id = s.device_id "
            "WHERE s.token_hash=? LIMIT 1",
            (_sha256(token),),
        ).fetchone()
        if row is None:
            return None
        if row["revoked_at"] or row["device_revoked"]:
            return None
        if row["expires_at"] and row["expires_at"] < now_iso:
            return None
        db.conn.execute(
            "UPDATE devices SET last_seen_at=? WHERE id=?", (now_iso, row["device_id"])
        )
        db.conn.commit()
    return {"device_id": row["device_id"], "user_id": row["user_id"],
            "session_id": row["session_id"]}


def list_devices(user_id: str) -> list[dict]:
    from .userdb import db

    with db._lock:
        rows = db.conn.execute(
            "SELECT id, display_name, platform, created_at, last_seen_at, revoked_at "
            "FROM devices WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def revoke_device(device_id: str, user_id: str) -> dict:
    """撤销设备：设备与其全部会话/推送一并作废（幂等）。"""
    from .userdb import db

    now = _now_iso()
    with db._lock:
        row = db.conn.execute(
            "SELECT id FROM devices WHERE id=? AND user_id=?", (device_id, user_id)
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "设备不存在"}
        db.conn.execute("UPDATE devices SET revoked_at=? WHERE id=?", (now, device_id))
        db.conn.execute(
            "UPDATE auth_sessions SET revoked_at=? WHERE device_id=? AND revoked_at IS NULL",
            (now, device_id),
        )
        db.conn.execute(
            "UPDATE push_subscriptions SET revoked_at=? WHERE device_id=? AND revoked_at IS NULL",
            (now, device_id),
        )
        db.conn.commit()
    logger.info("[设备] {} 已撤销", device_id)
    return {"ok": True}


def add_push_subscription(device_id: str, endpoint: str) -> dict:
    """推送订阅（骨架：只存 endpoint 哈希；密钥材料接真服务前入密钥仓）。"""
    from .userdb import db

    sub_id = uuid.uuid4().hex[:12]
    with db._lock:
        db.conn.execute(
            "INSERT INTO push_subscriptions (id, device_id, endpoint_hash, created_at) "
            "VALUES (?, ?, ?, ?)",
            (sub_id, device_id, _sha256(endpoint), _now_iso()),
        )
        db.conn.commit()
    return {"subscription_id": sub_id}


def retract_push(endpoint: str) -> dict:
    """404/410 时撤销订阅（幂等）。"""
    from .userdb import db

    with db._lock:
        db.conn.execute(
            "UPDATE push_subscriptions SET revoked_at=? WHERE endpoint_hash=? "
            "AND revoked_at IS NULL",
            (_now_iso(), _sha256(endpoint)),
        )
        db.conn.commit()
    return {"ok": True}


__all__ = [
    "add_push_subscription",
    "list_devices",
    "register_device",
    "retract_push",
    "revoke_device",
    "verify_session",
]
