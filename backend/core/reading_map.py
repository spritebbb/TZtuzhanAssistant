# -*- coding: utf-8 -*-
"""F05 共读方案 C：阅读地图与书签解锁。

契约（docs/Zcode技术指导.md F05 + 调度文档批次 8）：

- ``reading_segments``：开书事务一次按文档文本稳定切分建全图，只建空框架，
  不由 LLM 通读生成「已经讨论过」的内容；status = locked / current / read /
  legacy_position（旧活动按当前位置以前的段标 legacy_position，可导航但不
  捏造确认书签）；
- ``reading_bookmarks``：status = empty / draft / confirmed；**finish 是唯一
  解锁事件**，跳到末页不算全部已读；摘录只取 source_range 与真实已确认观点；
- 草稿由确定性汇编产出（OUT-1 风格约束），用户确认后才落 confirmed；
- 全书读完时确定性汇总 confirmed 书签，未填部分显示省略计数，不替用户补观点；
- 源文档改版 hash 不符返回 409 要求新建阅读版本；文档删除走既有 forget 路径。
"""
from __future__ import annotations

import hashlib
from datetime import datetime

from .log import logger

SEGMENT_STATUSES: tuple[str, ...] = ("locked", "current", "read", "legacy_position")
BOOKMARK_STATUSES: tuple[str, ...] = ("empty", "draft", "confirmed")
_MAX_VIEW_CHARS = 600


class ReadingMapError(ValueError):
    """阅读地图的预期业务错误。"""


class ReadingMapConflict(ReadingMapError):
    """源文档版本不一致（409）。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:16]


# ---- 建图（幂等，开书一次） ----

def ensure_map(user_id: str, activity_id: int) -> dict:
    """按文档文本稳定切分建全图；已建则原样返回（重开同书不重复建图）。"""
    from .userdb import db

    with db._lock:
        existing = db.conn.execute(
            "SELECT COUNT(*) AS n FROM reading_segments WHERE user_id=? AND activity_id=?",
            (user_id, int(activity_id)),
        ).fetchone()
        if existing and int(existing["n"]) > 0:
            return get_map(user_id, int(activity_id))
        row = db.conn.execute(
            "SELECT a.document_id, a.position, d.chunk_count FROM activities a "
            "JOIN kb_documents d ON d.id = a.document_id AND d.user_id = a.user_id "
            "WHERE a.user_id=? AND a.id=? AND a.kind='reading'",
            (user_id, int(activity_id)),
        ).fetchone()
        if row is None:
            raise ReadingMapError("这场共读不存在")
        doc_id = int(row["document_id"])
        position = max(0, int(row["position"] or 0))
        chunks = db.conn.execute(
            "SELECT seq, text FROM kb_chunks WHERE user_id=? AND doc_id=? ORDER BY seq",
            (user_id, doc_id),
        ).fetchall()
        if not chunks:
            raise ReadingMapError("这份文档还没有可阅读的段落")
        offset = 0
        now = _now()
        for chunk in chunks:
            seq = int(chunk["seq"])
            text = str(chunk["text"] or "")
            status = ("legacy_position" if seq < position
                      else "current" if seq == position else "locked")
            db.conn.execute(
                "INSERT OR IGNORE INTO reading_segments "
                "(user_id, activity_id, segment_index, source_start, source_end, "
                "source_hash, title, status, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, int(activity_id), seq, offset, offset + len(text),
                 _hash(text), f"第 {seq + 1} 段", status,
                 now if status in ("read", "legacy_position") else None),
            )
            offset += len(text)
        db.conn.commit()
    logger.info("[阅读地图] 建图：activity={} 段数={}", activity_id, len(chunks))
    return get_map(user_id, int(activity_id))


def _segment_view(row) -> dict:
    return {
        "id": int(row["id"]),
        "segment_index": int(row["segment_index"]),
        "title": str(row["title"] or ""),
        "status": str(row["status"]),
        "source_start": int(row["source_start"]),
        "source_end": int(row["source_end"]),
        "source_hash": str(row["source_hash"] or ""),
        "completed_at": row["completed_at"],
    }


def _bookmark_view(row) -> dict | None:
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "segment_id": int(row["segment_id"]),
        "origin": str(row["origin"]),
        "user_view": str(row["user_view"] or ""),
        "tuzhan_view": str(row["tuzhan_view"] or ""),
        "summary": str(row["summary"] or ""),
        "status": str(row["status"]),
    }


def get_map(user_id: str, activity_id: int) -> dict:
    """地图 + 每段书签状态；只读，不触发建图。"""
    from .userdb import db

    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM reading_segments WHERE user_id=? AND activity_id=? "
            "ORDER BY segment_index",
            (user_id, int(activity_id)),
        ).fetchall()
        bookmark_rows = db.conn.execute(
            "SELECT b.* FROM reading_bookmarks b "
            "JOIN reading_segments s ON s.id = b.segment_id AND s.user_id = b.user_id "
            "WHERE b.user_id=? AND s.activity_id=?",
            (user_id, int(activity_id)),
        ).fetchall()
    bookmarks = {int(r["segment_id"]): _bookmark_view(r) for r in bookmark_rows}
    segments = []
    for row in rows:
        view = _segment_view(row)
        view["bookmark"] = bookmarks.get(view["id"])
        segments.append(view)
    confirmed = sum(1 for b in bookmarks.values() if b and b["status"] == "confirmed")
    return {
        "activity_id": int(activity_id),
        "segments": segments,
        "total": len(segments),
        "read_count": sum(1 for s in segments if s["status"] in ("read", "legacy_position")),
        "confirmed_bookmarks": confirmed,
    }


# ---- 解锁（finish 是唯一解锁事件） ----

def finish_segment(user_id: str, activity_id: int, segment_index: int, *,
                   expected_version: str | None = None) -> dict:
    """完成一段：标 read、开下一段；跳页不算已读（只有本接口解锁）。"""
    from .userdb import db

    now = _now()
    with db._lock:
        row = db.conn.execute(
            "SELECT * FROM reading_segments WHERE user_id=? AND activity_id=? "
            "AND segment_index=?",
            (user_id, int(activity_id), int(segment_index)),
        ).fetchone()
        if row is None:
            raise ReadingMapError("这一段不在阅读地图里")
        if expected_version is not None and str(expected_version) != str(row["source_hash"]):
            raise ReadingMapConflict("文档内容已改版，请新建阅读版本")
        if row["status"] == "locked":
            # 只允许完成当前段（或 legacy 段），不能跳过
            current = db.conn.execute(
                "SELECT MIN(segment_index) AS idx FROM reading_segments "
                "WHERE user_id=? AND activity_id=? AND status='current'",
                (user_id, int(activity_id)),
            ).fetchone()
            if current is None or int(current["idx"]) != int(segment_index):
                raise ReadingMapError("请先完成当前这一段")
        if row["status"] not in ("read", "legacy_position"):
            db.conn.execute(
                "UPDATE reading_segments SET status='read', completed_at=? WHERE id=?",
                (now, int(row["id"])),
            )
        db.conn.execute(
            "UPDATE reading_segments SET status='current' "
            "WHERE user_id=? AND activity_id=? AND segment_index=? AND status='locked'",
            (user_id, int(activity_id), int(segment_index) + 1),
        )
        db.conn.commit()
    return get_map(user_id, int(activity_id))


# ---- 书签（草稿 → 用户确认） ----

def bookmark_draft(user_id: str, segment_id: int) -> dict:
    """确定性草稿：只取该段原文节选与用户已留的书签，不替用户写观点。"""
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT s.*, a.document_id, a.id AS activity_id FROM reading_segments s "
            "JOIN activities a ON a.id = s.activity_id AND a.user_id = s.user_id "
            "WHERE s.user_id=? AND s.id=?",
            (user_id, int(segment_id)),
        ).fetchone()
        if row is None:
            raise ReadingMapError("这一段不在阅读地图里")
        chunk = db.conn.execute(
            "SELECT text FROM kb_chunks WHERE user_id=? AND doc_id=? AND seq=?",
            (user_id, int(row["document_id"]), int(row["segment_index"])),
        ).fetchone()
        note = db.conn.execute(
            "SELECT content FROM activity_notes WHERE user_id=? AND activity_id=? AND position=?",
            (user_id, int(row["activity_id"]), int(row["segment_index"])),
        ).fetchone()
        bookmark = db.conn.execute(
            "SELECT * FROM reading_bookmarks WHERE user_id=? AND segment_id=?",
            (user_id, int(segment_id)),
        ).fetchone()
    excerpt = str(chunk["text"] if chunk else "")[:200]
    user_note = str(note["content"] if note else "").strip()
    draft = {
        "segment_id": int(segment_id),
        "excerpt": excerpt,
        "user_view": user_note or (bookmark["user_view"] if bookmark else ""),
        "tuzhan_view": str(bookmark["tuzhan_view"] if bookmark else ""),
        "status": "draft",
    }
    with db._lock:
        db.conn.execute(
            "INSERT INTO reading_bookmarks "
            "(user_id, segment_id, origin, user_view, tuzhan_view, summary, status, "
            "source_version, created_at, updated_at) "
            "VALUES (?, ?, 'tuzhan_draft', ?, ?, '', 'draft', ?, ?, ?) "
            "ON CONFLICT(user_id, segment_id) DO UPDATE SET "
            "user_view=excluded.user_view, status='draft', updated_at=excluded.updated_at "
            "WHERE reading_bookmarks.status != 'confirmed'",
            (user_id, int(segment_id), draft["user_view"], draft["tuzhan_view"],
             str(row["source_hash"] or ""), _now(), _now()),
        )
        db.conn.commit()
    return draft


def save_bookmark(user_id: str, segment_id: int, *, user_view: str = "",
                  tuzhan_view: str = "", summary: str = "") -> dict:
    """用户确认书签（唯一 confirmed 来源；模型观点只能进 tuzhan_view）。"""
    from .userdb import db

    user_view = str(user_view or "").strip()[:_MAX_VIEW_CHARS]
    tuzhan_view = str(tuzhan_view or "").strip()[:_MAX_VIEW_CHARS]
    summary = str(summary or "").strip()[:_MAX_VIEW_CHARS]
    if not (user_view or tuzhan_view or summary):
        raise ReadingMapError("书签内容不能为空")
    with db._lock:
        row = db.conn.execute(
            "SELECT id FROM reading_segments WHERE user_id=? AND id=?",
            (user_id, int(segment_id)),
        ).fetchone()
        if row is None:
            raise ReadingMapError("这一段不在阅读地图里")
        db.conn.execute(
            "INSERT INTO reading_bookmarks "
            "(user_id, segment_id, origin, user_view, tuzhan_view, summary, status, "
            "source_version, created_at, updated_at) "
            "VALUES (?, ?, 'user', ?, ?, ?, 'confirmed', '', ?, ?) "
            "ON CONFLICT(user_id, segment_id) DO UPDATE SET "
            "origin='user', user_view=excluded.user_view, tuzhan_view=excluded.tuzhan_view, "
            "summary=excluded.summary, status='confirmed', updated_at=excluded.updated_at",
            (user_id, int(segment_id), user_view, tuzhan_view, summary, _now(), _now()),
        )
        db.conn.commit()
        row = db.conn.execute(
            "SELECT * FROM reading_bookmarks WHERE user_id=? AND segment_id=?",
            (user_id, int(segment_id)),
        ).fetchone()
    return _bookmark_view(row) or {}


def compile_summary(user_id: str, activity_id: int) -> str:
    """全书确定性汇总：只含 confirmed 书签，未填部分显示省略计数。"""
    data = get_map(user_id, activity_id)
    lines: list[str] = []
    omitted = 0
    for segment in data["segments"]:
        bookmark = segment.get("bookmark")
        if not bookmark or bookmark["status"] != "confirmed":
            omitted += 1
            continue
        piece = bookmark["user_view"] or bookmark["summary"]
        if piece:
            lines.append(f"- {segment['title']}：{piece}")
    if not lines:
        return "（这一遍没有留下确认过的书签）"
    if omitted:
        lines.append(f"（另有 {omitted} 段没有留下确认书签）")
    return "\n".join(lines)


def forget_for_activity(user_id: str, activity_id: int) -> int:
    """活动/文档删除时清空地图与书签（源删不保留摘录）。"""
    from .userdb import db

    with db._lock:
        db.conn.execute(
            "DELETE FROM reading_bookmarks WHERE user_id=? AND segment_id IN "
            "(SELECT id FROM reading_segments WHERE user_id=? AND activity_id=?)",
            (user_id, user_id, int(activity_id)),
        )
        cur = db.conn.execute(
            "DELETE FROM reading_segments WHERE user_id=? AND activity_id=?",
            (user_id, int(activity_id)),
        )
        db.conn.commit()
    return int(cur.rowcount)
