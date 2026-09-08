# -*- coding: utf-8 -*-
"""监控 Agent：网页变化监视（只存哈希，不存正文）。

设计约束（沿用 web_fetch 的安全口径）：
- 只接受公网 http(s) 地址（``resolve_public_url`` 校验 + DNS pinning），
  本机/内网一律拒绝——避免被用来探测内网；
- 不自动跟随重定向、限大小（512KB）与超时（15s）；
- **不保存网页正文**，只存内容规范化后的哈希：变化时能告知「有更新」，
  需要细节再由用户触发抓取（隐私与存储都省）；
- 每个监视有独立检查间隔（默认 6 小时），到期才抓，避免频繁打对方站点。
"""
from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from .log import logger

_FETCH_TIMEOUT = 15
_MAX_BYTES = 512 * 1024
_MIN_INTERVAL = 30          # 最短检查间隔（分钟）
_WS_RE = re.compile(r"\s+")


class WatchError(ValueError):
    """监视项的预期业务错误。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normalize(text: str) -> str:
    """内容规范化：折叠空白 + 去掉常见时间戳噪声，降低假变化。"""
    return _WS_RE.sub(" ", str(text or "")).strip()


def _hash(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8", errors="replace")).hexdigest()[:32]


def _fetch_text(url: str) -> str:
    """抓取网页文本（公网校验 + 不自动跟重定向 + 大小/超时限制）。"""
    from ..tools.safety import build_pinned_opener, resolve_public_url

    ok, err, resolved_ip = resolve_public_url(url)
    if not ok:
        raise WatchError(f"拒绝监视不安全的地址：{err}")

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = build_pinned_opener(resolved_ip, _NoRedirect())
    req = urllib.request.Request(url, headers={"User-Agent": "TuzhanWatch/1.0"})
    try:
        with opener.open(req, timeout=_FETCH_TIMEOUT) as resp:
            raw = resp.read(_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            raise WatchError("页面跳转了，请把监视地址换成最终地址") from exc
        raise WatchError(f"抓取失败：HTTP {exc.code}") from exc
    except Exception as exc:
        raise WatchError(f"抓取失败：{type(exc).__name__}") from exc
    if len(raw) > _MAX_BYTES:
        raw = raw[:_MAX_BYTES]
    return raw.decode("utf-8", errors="replace")


def add_watch(user_id: str, url: str, label: str = "",
              interval_minutes: int = 360) -> dict:
    """新增一个网页监视（同用户同 URL 幂等，重复添加只更新间隔/标签）。"""
    from ..tools.safety import resolve_public_url
    from .userdb import db

    url = str(url or "").strip()
    ok, err, _ = resolve_public_url(url)
    if not ok:
        raise WatchError(f"这个地址不能监视：{err}")
    interval = max(_MIN_INTERVAL, int(interval_minutes or 360))
    now = _now()
    with db._lock:
        db.conn.execute(
            "INSERT INTO watches (user_id, url, label, interval_minutes, status,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?) "
            "ON CONFLICT(user_id, url) DO UPDATE SET "
            "label = excluded.label, interval_minutes = excluded.interval_minutes,"
            " status = 'active', updated_at = excluded.updated_at",
            (user_id, url, str(label or "")[:60], interval, now, now),
        )
        db.conn.commit()
        row = db.conn.execute(
            "SELECT * FROM watches WHERE user_id=? AND url=?", (user_id, url)
        ).fetchone()
    logger.info("[监视] {} 新增监视 {}（每 {} 分钟）", user_id, url, interval)
    return dict(row)


def list_watches(user_id: str) -> list[dict]:
    from .userdb import db

    with db._lock:
        rows = db.conn.execute(
            "SELECT id, url, label, interval_minutes, last_checked_at, last_changed_at,"
            " status FROM watches WHERE user_id=? ORDER BY id DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def remove_watch(user_id: str, watch_id: int) -> bool:
    from .userdb import db

    with db._lock:
        cur = db.conn.execute(
            "DELETE FROM watches WHERE user_id=? AND id=?", (user_id, int(watch_id))
        )
        db.conn.commit()
    return bool(cur.rowcount)


def check_due_watches(user_id: str | None = None, *, now: datetime | None = None,
                      force: bool = False) -> list[dict]:
    """检查到期的监视项，返回**发生变化**的条目（同步，调用方放线程池）。

    - 到期判定：last_checked_at 为空或已超过 interval_minutes；
    - 每次检查都会更新 last_checked_at（即使内容没变），避免打对方站点；
    - 首次检查只记录基线哈希，不算「变化」。
    """
    from .userdb import db

    moment = now or datetime.now()
    sql = "SELECT * FROM watches WHERE status='active'"
    params: list = []
    if user_id:
        sql += " AND user_id=?"
        params.append(user_id)
    with db._lock:
        rows = db.conn.execute(sql, params).fetchall()

    changes: list[dict] = []
    for row in rows:
        watch_id = int(row["id"])
        owner = str(row["user_id"])
        last_checked = row["last_checked_at"]
        if not force and last_checked:
            try:
                due_at = datetime.fromisoformat(str(last_checked)) + timedelta(
                    minutes=int(row["interval_minutes"])
                )
            except ValueError:
                due_at = moment
            if moment < due_at:
                continue
        url = str(row["url"])
        try:
            text = _fetch_text(url)
        except WatchError as exc:
            logger.info("[监视] 抓取失败 {}：{}", url, exc)
            with db._lock:
                db.conn.execute(
                    "UPDATE watches SET last_checked_at=?, updated_at=? WHERE id=?",
                    (_now(), _now(), watch_id),
                )
                db.conn.commit()
            continue
        digest = _hash(text)
        old_hash = str(row["last_hash"] or "")
        with db._lock:
            db.conn.execute(
                "UPDATE watches SET last_checked_at=?, last_hash=?, updated_at=?"
                " WHERE id=?",
                (_now(), digest, _now(), watch_id),
            )
            if old_hash and old_hash != digest:
                db.conn.execute(
                    "UPDATE watches SET last_changed_at=? WHERE id=?", (_now(), watch_id)
                )
            db.conn.commit()
        if old_hash and old_hash != digest:
            changes.append({
                "watch_id": watch_id,
                "user_id": owner,
                "label": str(row["label"] or ""),
                "url": url,
            })
    return changes
