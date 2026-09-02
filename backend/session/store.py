# -*- coding: utf-8 -*-
"""会话持久化：SQLite 实现。

单一会话模式：全局只有一个固定会话（id 固定为 ``current``），
不再支持新建/切换多会话。会话内容在「结束并归档」时打包存入
archives 表，之后当前会话被清空，等待下一段对话。

结构：
- sessions(id TEXT PK, title, created_at, updated_at)  —— 仅一条 id='current'
- messages(id INTEGER PK AUTOINC, session_id, role, content, image, ts)
- archives(id TEXT PK, title, created_at, message_count, messages_json)

单进程 + asyncio.Lock 串行写；SQLite 同步调用放线程池避免卡事件循环。
数据量小，WAL 模式保证并发读安全。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from pathlib import Path

from ..core.config import config

_DB: Path = config.data_dir / "sessions.db"

# 单一会话的固定 id：全局唯一，不再新建
CURRENT_SESSION_ID = "current"

_lock = asyncio.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init() -> None:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    conn = _connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions ("
            "id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '新会话',"
            "created_at REAL NOT NULL, updated_at REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "session_id TEXT NOT NULL, role TEXT NOT NULL,"
            "content TEXT NOT NULL DEFAULT '', image TEXT, ts REAL NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS archives ("
            "id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '归档',"
            "created_at REAL NOT NULL, message_count INTEGER NOT NULL DEFAULT 0,"
            "messages_json TEXT NOT NULL DEFAULT '[]')"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_archives_created ON archives(created_at)"
        )
        # 确保单一会话 'current' 始终存在
        now = time.time()
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, title, created_at, updated_at) VALUES (?,?,?,?)",
            (CURRENT_SESSION_ID, "新会话", now, now),
        )
        conn.commit()
    finally:
        conn.close()


# ---- 数据访问（内部同步实现，外部经 async 包装）----

def _get_messages_sync(session_id: str) -> list[dict] | None:
    conn = _connect()
    try:
        exists = conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone()
        if exists is None:
            return None
        rows = conn.execute(
            "SELECT role, content, image, ts FROM messages"
            " WHERE session_id=? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        return [{"role": r["role"], "content": r["content"], "image": r["image"], "ts": r["ts"]} for r in rows]
    finally:
        conn.close()


def _append_sync(session_id: str, messages: list[dict]) -> bool:
    conn = _connect()
    try:
        row = conn.execute("SELECT title FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            return False
        for m in messages:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, image, ts) VALUES (?,?,?,?,?)",
                (session_id, m.get("role", ""), m.get("content", ""), m.get("image"), m.get("ts", time.time())),
            )
        # 标题：取第一条用户消息前 20 字
        if row["title"] == "新会话":
            for m in messages:
                if m.get("role") == "user" and m.get("content", "").strip():
                    t = m["content"].strip().replace("\n", " ")
                    conn.execute("UPDATE sessions SET title=?, updated_at=? WHERE id=?", (t[:20], time.time(), session_id))
                    break
            else:
                conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), session_id))
        else:
            conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), session_id))
        conn.commit()
        return True
    finally:
        conn.close()


# ---- 归档（单一会话模式下，会话结束 → 打包存入 archives）----

def _archive_current_sync() -> dict | None:
    """把当前会话（id='current'）的所有消息打包归档，并清空当前会话。

    返回归档信息；若当前会话无消息则返回 None（不产生空归档）。
    """
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT role, content, image, ts FROM messages"
            " WHERE session_id=? ORDER BY id ASC",
            (CURRENT_SESSION_ID,),
        ).fetchall()
        if not rows:
            return None
        msgs = [
            {"role": r["role"], "content": r["content"], "image": r["image"], "ts": r["ts"]}
            for r in rows
        ]
        # 归档标题：取第一条用户消息前 20 字
        title = "归档"
        for m in msgs:
            if m.get("role") == "user" and (m.get("content") or "").strip():
                title = m["content"].strip().replace("\n", " ")[:20]
                break
        aid = uuid.uuid4().hex[:12]
        now = time.time()
        conn.execute(
            "INSERT INTO archives (id, title, created_at, message_count, messages_json) VALUES (?,?,?,?,?)",
            (aid, title, now, len(msgs), json.dumps(msgs, ensure_ascii=False)),
        )
        conn.execute("DELETE FROM messages WHERE session_id=?", (CURRENT_SESSION_ID,))
        conn.execute(
            "UPDATE sessions SET title='新会话', updated_at=? WHERE id=?",
            (now, CURRENT_SESSION_ID),
        )
        conn.commit()
        return {"id": aid, "title": title, "created_at": now, "message_count": len(msgs)}
    finally:
        conn.close()


def _list_archives_sync() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, title, created_at, message_count FROM archives ORDER BY created_at DESC"
        ).fetchall()
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "created_at": r["created_at"],
                "message_count": r["message_count"],
            }
            for r in rows
        ]
    finally:
        conn.close()


def _get_archive_sync(archive_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, title, created_at, message_count, messages_json FROM archives WHERE id=?",
            (archive_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            msgs = json.loads(row["messages_json"])
        except (json.JSONDecodeError, TypeError):
            msgs = []
        return {
            "id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "message_count": row["message_count"],
            "messages": msgs,
        }
    finally:
        conn.close()


# ---- 异步对外接口 ----

async def get_messages(session_id: str) -> list[dict] | None:
    async with _lock:
        return await asyncio.to_thread(_get_messages_sync, session_id)


async def append_messages(session_id: str, messages: list[dict]) -> bool:
    async with _lock:
        return await asyncio.to_thread(_append_sync, session_id, messages)


async def archive_current() -> dict | None:
    """归档当前会话（打包消息存入 archives，清空当前会话）。无消息返回 None。"""
    async with _lock:
        return await asyncio.to_thread(_archive_current_sync)


async def list_archives() -> list[dict]:
    async with _lock:
        return await asyncio.to_thread(_list_archives_sync)


async def get_archive(archive_id: str) -> dict | None:
    async with _lock:
        return await asyncio.to_thread(_get_archive_sync, archive_id)


def init() -> None:
    _init()


# 启动时初始化（幂等）
init()
