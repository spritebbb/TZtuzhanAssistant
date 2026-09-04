"""SQLite 数据层：每用户独立数据。

表：
- users         用户状态（好感度、称呼偏好、恋人确认、首次对话、日期标记）
- messages      会话历史（短期上下文的来源）
- long_memory   长期记忆原文片段（关键词检索）
- facts         LLM 提炼的长期事实（喜好/约定等，带去重）
- user_meta     事实提炼游标等元数据
- affection_log 好感度变动流水
"""
import re
import sqlite3
import threading
import time
from datetime import date, datetime

from .config import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id         TEXT PRIMARY KEY,
    affection       INTEGER NOT NULL DEFAULT 0,
    nickname_pref   TEXT,
    lover_confirm   INTEGER NOT NULL DEFAULT 0,
    first_chat_done INTEGER NOT NULL DEFAULT 0,
    last_chat_date  TEXT,
    last_batch_date TEXT,
    style_profile   TEXT,
    mood_value      INTEGER NOT NULL DEFAULT 60,
    mood_updated_at TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    role    TEXT NOT NULL,
    content TEXT NOT NULL,
    ts      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS long_memory (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    ts      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS affection_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    delta   INTEGER NOT NULL,
    reason  TEXT NOT NULL,
    ts      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    ts      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_meta (
    user_id          TEXT PRIMARY KEY,
    last_fact_msg_id INTEGER NOT NULL DEFAULT 0,
    last_profile_msg_id INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kv_store (
    user_id TEXT NOT NULL,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS important_dates (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    date    TEXT NOT NULL,     -- 'MM-DD'（如 '12-25'；无年份的每年一次）
    label   TEXT NOT NULL,     -- 事件名，如 '你的生日' / '我们认识的日子'
    kind    TEXT NOT NULL DEFAULT 'other',  -- birthday / anniversary / other
    year    INTEGER,           -- 有年份则存具体年份；无年份 NULL = 每年
    ts      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stickers (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  TEXT NOT NULL,    -- 收藏者（哪个用户发的）
    file     TEXT NOT NULL,    -- 本地缓存文件路径
    url      TEXT NOT NULL,    -- 原始图片 URL
    desc     TEXT NOT NULL DEFAULT '',  -- 视觉模型描述（用于话题匹配回发）
    emotion  TEXT NOT NULL DEFAULT '',  -- 情绪标签（逗号分隔，如"开心,可爱"）
    count    INTEGER NOT NULL DEFAULT 1, -- 该表情被看到/收藏的次数
    ts       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_profile (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  TEXT NOT NULL,
    category TEXT NOT NULL,   -- basic / likes / dislikes / habits / personality / other
    content  TEXT NOT NULL,   -- 画像条目（如「喜欢下雨天」）
    source   TEXT NOT NULL DEFAULT 'llm',  -- 来源（llm / manual / date）
    ts       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_terms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    term       TEXT NOT NULL,   -- 口头禅/黑话词
    category   TEXT NOT NULL DEFAULT 'catchphrase',  -- catchphrase(口头禅) / slang(黑话)
    meaning    TEXT NOT NULL DEFAULT '',  -- 含义（黑话解释）
    count      INTEGER NOT NULL DEFAULT 1,  -- 出现次数
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_style_map (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  TEXT NOT NULL,
    situation TEXT NOT NULL,  -- 场景（如「对方倾诉烦恼时」「对方开玩笑时」）
    style    TEXT NOT NULL,   -- 该场景下对方的表达方式（如「喜欢用短句+省略号」）
    count    INTEGER NOT NULL DEFAULT 1,
    ts       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS diary (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    date    TEXT NOT NULL,       -- 日期 YYYY-MM-DD
    content TEXT NOT NULL,       -- 日记正文（菟菚视角）
    mood    TEXT NOT NULL DEFAULT '',  -- 当天心情标签
    ts      TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_diary_user_date ON diary(user_id, date);
CREATE TABLE IF NOT EXISTS research_reports (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    period  TEXT NOT NULL,       -- 覆盖区间，如 2026-08-29~2026-09-04
    title   TEXT NOT NULL,
    content TEXT NOT NULL,
    ts      TEXT NOT NULL,
    UNIQUE(user_id, period)
);
-- 约定/承诺（C6 约定与跟进）：双方明确许下的事 + 该跟进的日子
CREATE TABLE IF NOT EXISTS promises (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    content    TEXT NOT NULL,               -- 约定内容（一句短话）
    follow_up  TEXT NOT NULL DEFAULT '',    -- 该跟进的日子 YYYY-MM-DD，空=未明确时间
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending / done / cancelled
    source     TEXT NOT NULL DEFAULT '',    -- 来源对话片段（溯源）
    created_at TEXT NOT NULL,
    done_at    TEXT
);
-- 结构化事实记忆（五元组：主体-谓词-客体-类型），方向 C
CREATE TABLE IF NOT EXISTS triples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    subject     TEXT NOT NULL,
    subject_type TEXT NOT NULL DEFAULT 'person',
    predicate   TEXT NOT NULL,
    object      TEXT NOT NULL,
    object_type TEXT NOT NULL DEFAULT 'item',
    source_msg  TEXT NOT NULL DEFAULT '',   -- 来源消息片段（溯源用）
    created_at  TEXT NOT NULL
);
-- 任务/目标追踪（对标 Harness 的 goal + todo 系统）
CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    content      TEXT NOT NULL,               -- 任务描述（目标）
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending / in_progress / completed / blocked
    phase        TEXT NOT NULL DEFAULT '',    -- 分组/阶段标签（如 v3）
    priority     TEXT NOT NULL DEFAULT 'P1',  -- P0 / P1 / P2 / P3
    progress     TEXT NOT NULL DEFAULT '',    -- 进度说明/备注
    blocked_reason TEXT NOT NULL DEFAULT '',  -- 受阻原因（status=blocked 时填写）
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id, status);
CREATE INDEX IF NOT EXISTS idx_triples_user ON triples(user_id);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, id);
CREATE INDEX IF NOT EXISTS idx_long_memory_user ON long_memory(user_id, id);
CREATE INDEX IF NOT EXISTS idx_facts_user ON facts(user_id, id);
CREATE INDEX IF NOT EXISTS idx_dates_user ON important_dates(user_id);
CREATE INDEX IF NOT EXISTS idx_promises_user ON promises(user_id, status);
CREATE INDEX IF NOT EXISTS idx_stickers_user ON stickers(user_id);
CREATE INDEX IF NOT EXISTS idx_profile_user ON user_profile(user_id);
CREATE INDEX IF NOT EXISTS idx_terms_user ON user_terms(user_id);
CREATE INDEX IF NOT EXISTS idx_style_map_user ON user_style_map(user_id);
"""


def _locked(method):
    """写方法装饰器：串行化对共享连接的写访问（RLock 可重入）。"""

    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


class UserDB:
    def __init__(self) -> None:
        # 写锁：pipeline 按用户串行，但 daily/profile/mood/greeting/agent 等
        # 模块也直接写同一连接，统一加锁避免并发写竞态
        self._lock = threading.RLock()
        config.data_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(config.data_dir / "bot.db", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.executescript(_SCHEMA)
        # 兼容旧库：补上 style_profile 列
        try:
            self.conn.execute("ALTER TABLE users ADD COLUMN style_profile TEXT")
        except sqlite3.OperationalError:
            pass
        # 心情系统字段（旧库迁移）
        try:
            self.conn.execute("ALTER TABLE users ADD COLUMN mood_value INTEGER NOT NULL DEFAULT 60")
        except sqlite3.OperationalError:
            pass
        try:
            self.conn.execute("ALTER TABLE users ADD COLUMN mood_updated_at TEXT")
        except sqlite3.OperationalError:
            pass
        # 旧库迁移：user_meta 补 last_profile_msg_id 列
        try:
            self.conn.execute("ALTER TABLE user_meta ADD COLUMN last_profile_msg_id INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        # 旧库迁移：stickers 补 emotion 列
        try:
            self.conn.execute("ALTER TABLE stickers ADD COLUMN emotion TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        # 旧库迁移：tasks 补 blocked_reason 列
        try:
            self.conn.execute("ALTER TABLE tasks ADD COLUMN blocked_reason TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        # 用户身份统一迁移：历史版本聊天链路用 f"session_{session_id}"（单一会话
        # 下即 "session_current"），现统一为 "assistant-main"，与 agent 任务代理、
        # contextvar 默认值对齐。此处把旧的 session_current 数据合并进 assistant-main，
        # 避免菟菚「失忆」（好感度/心情/记忆/待办全部保留）。幂等：无旧数据时无副作用。
        self._migrate_legacy_user_identity()
        self.conn.commit()

    def _migrate_legacy_user_identity(self, legacy: str = "session_current",
                                      target: str = "assistant-main") -> None:
        """把旧身份（legacy）名下所有数据合并到统一身份（target）。幂等。

        - users：好感度取两者较大值，昵称/恋人/日期取 target 缺失时回填 legacy；
        - 其余表：把 legacy 的行改挂到 target（user_id 列 UPDATE）；有唯一约束的
          表（user_meta / kv_store / diary）用 INSERT OR IGNORE 兜底避免主键冲突。
        """
        legacy_row = self.conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (legacy,)
        ).fetchone()
        if legacy_row is None:
            return
        target_row = self.conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (target,)
        ).fetchone()
        if target_row is None:
            # target 不存在：直接把 legacy 的 users 行改名即可（其余表仍走 UPDATE）
            self.conn.execute(
                "UPDATE users SET user_id = ? WHERE user_id = ?", (target, legacy)
            )
        else:
            # 双方都存在：好感度取较大，昵称/恋人确认/日期取 target 缺失时回填 legacy
            merged_affection = max(target_row["affection"], legacy_row["affection"])
            nickname = target_row["nickname_pref"] or legacy_row["nickname_pref"]
            lover = max(target_row["lover_confirm"], legacy_row["lover_confirm"])
            first_chat = max(target_row["first_chat_done"], legacy_row["first_chat_done"])
            last_chat = target_row["last_chat_date"] or legacy_row["last_chat_date"]
            last_batch = target_row["last_batch_date"] or legacy_row["last_batch_date"]
            mood = max(target_row["mood_value"], legacy_row["mood_value"])
            mood_updated = target_row["mood_updated_at"] or legacy_row["mood_updated_at"]
            style = target_row["style_profile"] or legacy_row["style_profile"]
            self.conn.execute(
                "UPDATE users SET affection=?, nickname_pref=?, lover_confirm=?, "
                "first_chat_done=?, last_chat_date=?, last_batch_date=?, "
                "mood_value=?, mood_updated_at=?, style_profile=? WHERE user_id=?",
                (merged_affection, nickname, lover, first_chat, last_chat, last_batch,
                 mood, mood_updated, style, target),
            )
            self.conn.execute("DELETE FROM users WHERE user_id = ?", (legacy,))

        # 有唯一约束、直接 UPDATE 可能主键冲突的表：先删 target 侧可能冲突的行再改，
        # 或改用 INSERT OR IGNORE。这里统一策略：把 legacy 行改挂 target 时，
        # 若 target 已有同名 key，保留 target 原值（legacy 行删除）。
        for table in ("user_meta",):
            self.conn.execute(
                f"INSERT OR IGNORE INTO {table} (user_id, last_fact_msg_id, last_profile_msg_id) "
                f"SELECT ?, last_fact_msg_id, last_profile_msg_id FROM {table} WHERE user_id = ?",
                (target, legacy),
            )
            self.conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (legacy,))

        # kv_store：复合主键 (user_id, key)，逐 key 迁移，target 已有则跳过
        legacy_kvs = self.conn.execute(
            "SELECT key, value FROM kv_store WHERE user_id = ?", (legacy,)
        ).fetchall()
        for kv in legacy_kvs:
            self.conn.execute(
                "INSERT OR IGNORE INTO kv_store (user_id, key, value) VALUES (?, ?, ?)",
                (target, kv["key"], kv["value"]),
            )
        self.conn.execute("DELETE FROM kv_store WHERE user_id = ?", (legacy,))

        # diary：唯一约束 (user_id, date)，同样逐行 INSERT OR IGNORE
        legacy_diaries = self.conn.execute(
            "SELECT date, content, mood, ts FROM diary WHERE user_id = ?", (legacy,)
        ).fetchall()
        for d in legacy_diaries:
            self.conn.execute(
                "INSERT OR IGNORE INTO diary (user_id, date, content, mood, ts) VALUES (?, ?, ?, ?, ?)",
                (target, d["date"], d["content"], d["mood"], d["ts"]),
            )
        self.conn.execute("DELETE FROM diary WHERE user_id = ?", (legacy,))

        # research_reports：period 在同一用户内唯一，目标侧已有则保留目标。
        legacy_reports = self.conn.execute(
            "SELECT period, title, content, ts FROM research_reports WHERE user_id = ?", (legacy,)
        ).fetchall()
        for report in legacy_reports:
            self.conn.execute(
                "INSERT OR IGNORE INTO research_reports (user_id, period, title, content, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (target, report["period"], report["title"], report["content"], report["ts"]),
            )
        self.conn.execute("DELETE FROM research_reports WHERE user_id = ?", (legacy,))

        # 其余「纯 append」表：直接把 user_id 改挂 target（无唯一约束冲突风险）
        for table in (
            "messages", "long_memory", "facts", "affection_log", "important_dates",
            "stickers", "user_profile", "user_terms", "user_style_map", "triples", "tasks",
        ):
            self.conn.execute(
                f"UPDATE {table} SET user_id = ? WHERE user_id = ?", (target, legacy)
            )

    # ---- users ----
    @_locked
    def ensure_user(self, user_id: str):
        self.conn.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
        )
        self.conn.commit()
        return self.get_user(user_id)

    @_locked
    def get_user(self, user_id: str):
        row = self.conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row

    @_locked
    def update_affection(self, user_id: str, delta: int, reason: str) -> None:
        self.conn.execute(
            "UPDATE users SET affection = MAX(0, MIN(100, affection + ?)) WHERE user_id = ?",
            (delta, user_id),
        )
        self.conn.execute(
            "INSERT INTO affection_log (user_id, delta, reason, ts) VALUES (?, ?, ?, ?)",
            (user_id, delta, reason, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    @_locked
    def set_nickname(self, user_id: str, name: str) -> None:
        self.conn.execute(
            "UPDATE users SET nickname_pref = ? WHERE user_id = ?", (name, user_id)
        )
        self.conn.commit()

    # ---- tasks（任务/目标追踪，对标 Harness goal + todo）----
    @_locked
    def create_task(self, user_id: str, content: str, priority: str = "P1",
                    phase: str = "") -> int:
        """创建任务，返回 id。"""
        now = datetime.now().isoformat()
        cur = self.conn.execute(
            "INSERT INTO tasks (user_id, content, status, phase, priority, created_at, updated_at) "
            "VALUES (?, ?, 'pending', ?, ?, ?, ?)",
            (user_id, content, phase, priority, now, now),
        )
        self.conn.commit()
        return cur.lastrowid or 0

    @_locked
    def list_tasks(self, user_id: str, status: str | None = None) -> list[dict]:
        """列出任务。status 可选过滤（pending / in_progress / completed / blocked）。"""
        if status:
            rows = self.conn.execute(
                "SELECT * FROM tasks WHERE user_id=? AND status=? ORDER BY "
                "CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END, "
                "created_at DESC", (user_id, status)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM tasks WHERE user_id=? ORDER BY "
                "CASE status WHEN 'in_progress' THEN 0 WHEN 'pending' THEN 1 WHEN 'blocked' THEN 2 ELSE 3 END, "
                "CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END, "
                "created_at DESC", (user_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    @_locked
    def get_task(self, user_id: str, task_id: int) -> dict | None:
        """获取单个任务详情。"""
        r = self.conn.execute(
            "SELECT * FROM tasks WHERE user_id=? AND id=?", (user_id, task_id)
        ).fetchone()
        return dict(r) if r else None

    @_locked
    def update_task(self, user_id: str, task_id: int, **kwargs: str) -> bool:
        """更新任务字段。支持的字段：content, status, phase, priority, progress, blocked_reason。"""
        allowed = {"content", "status", "phase", "priority", "progress", "blocked_reason"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return False
        now = datetime.now().isoformat()
        updates["updated_at"] = now
        if updates.get("status") == "completed":
            updates["completed_at"] = now
        elif "status" in updates and updates["status"] != "completed":
            # 从已完成回退到其它状态：清理完成时间，避免残留误导
            updates["completed_at"] = None
        # 字段名已由白名单约束，值全部走参数绑定（含 None 也能正确写入 SQL NULL）
        set_clause = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [user_id, task_id]
        self.conn.execute(
            f"UPDATE tasks SET {set_clause} WHERE user_id=? AND id=?", vals
        )
        self.conn.commit()
        return True

    @_locked
    def delete_task(self, user_id: str, task_id: int) -> bool:
        """删除任务。"""
        cur = self.conn.execute(
            "DELETE FROM tasks WHERE user_id=? AND id=?", (user_id, task_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    @_locked
    def get_mood(self, user_id: str) -> tuple[int, str | None]:
        """读取心情值与上次更新时间 (mood, updated_at)。"""
        row = self.conn.execute(
            "SELECT mood_value, mood_updated_at FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return 60, None
        return (60 if row["mood_value"] is None else row["mood_value"]), row["mood_updated_at"]

    @_locked
    def set_mood(self, user_id: str, mood: int) -> None:
        """写入心情值（0-100）并更新时间戳。"""
        mood = max(0, min(100, round(mood)))
        self.conn.execute(
            "UPDATE users SET mood_value = ?, mood_updated_at = ? WHERE user_id = ?",
            (mood, datetime.now().isoformat(timespec="seconds"), user_id),
        )
        self.conn.commit()

    @_locked
    def set_style(self, user_id: str, style: str) -> None:
        """记录 LLM 提炼的对方说话风格（随聊天逐渐更新）。"""
        self.conn.execute(
            "UPDATE users SET style_profile = ? WHERE user_id = ?", (style, user_id)
        )
        self.conn.commit()

    @_locked
    def get_style(self, user_id: str) -> str:
        row = self.conn.execute(
            "SELECT style_profile FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return (row["style_profile"] or "") if row else ""

    @_locked
    def set_affection_absolute(self, user_id: str, value: int) -> None:
        """直接把好感度设为指定值（0-100），用于手动调节/调试。"""
        value = max(0, min(100, int(value)))
        self.ensure_user(user_id)
        cur = self.get_user(user_id)["affection"]
        self.conn.execute(
            "UPDATE users SET affection = ? WHERE user_id = ?", (value, user_id)
        )
        self.conn.execute(
            "INSERT INTO affection_log (user_id, delta, reason, ts) VALUES (?, ?, ?, ?)",
            (user_id, value - cur, "手动设置", datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()
        u = self.get_user(user_id)
        if u["affection"] >= 75 and not u["lover_confirm"]:
            self.set_lover_confirm(user_id)

    @_locked
    def set_lover_confirm(self, user_id: str) -> None:
        self.conn.execute(
            "UPDATE users SET lover_confirm = 1 WHERE user_id = ?", (user_id,)
        )
        self.conn.commit()

    @_locked
    def clear_lover_confirm(self, user_id: str) -> None:
        """二次确认称呼完成 → 清除标志，停止 persona 的"记得确认"注入。"""
        self.conn.execute(
            "UPDATE users SET lover_confirm = 0 WHERE user_id = ?", (user_id,)
        )
        self.conn.commit()

    @_locked
    def set_first_chat_done(self, user_id: str) -> None:
        self.conn.execute(
            "UPDATE users SET first_chat_done = 1 WHERE user_id = ?", (user_id,)
        )
        self.conn.commit()

    @_locked
    def set_chat_date(self, user_id: str, day: str, batch_day: str | None = None) -> None:
        if batch_day is not None:
            self.conn.execute(
                "UPDATE users SET last_chat_date = ?, last_batch_date = ? WHERE user_id = ?",
                (day, batch_day, user_id),
            )
        else:
            self.conn.execute(
                "UPDATE users SET last_chat_date = ? WHERE user_id = ?", (day, user_id)
            )
        self.conn.commit()

    @_locked
    def set_batch_date(self, user_id: str, day: str) -> None:
        """单独推进「每日总结已执行」日期（batch 实际完成后再标记，避免提前标记丢任务）。"""
        self.conn.execute(
            "UPDATE users SET last_batch_date = ? WHERE user_id = ?", (day, user_id)
        )
        self.conn.commit()

    # ---- messages ----
    @_locked
    def add_message(self, user_id: str, role: str, content: str) -> None:
        self.conn.execute(
            "INSERT INTO messages (user_id, role, content, ts) VALUES (?, ?, ?, ?)",
            (user_id, role, content, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    @_locked
    def recent_messages(self, user_id: str, limit: int):
        return self.conn.execute(
            "SELECT role, content FROM messages WHERE user_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()[::-1]

    @_locked
    def recent_messages_with_ids(self, user_id: str, limit: int):
        """最近 limit 条消息（含 id，按时间升序）。供需要推进游标的场景。"""
        return self.conn.execute(
            "SELECT id, role, content, ts FROM messages WHERE user_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()[::-1]

    @_locked
    def messages_between(self, user_id: str, start: date, end: date):
        return self.conn.execute(
            "SELECT id, role, content, ts FROM messages WHERE user_id = ? "
            "AND date(ts) BETWEEN ? AND ? ORDER BY id",
            (user_id, start.isoformat(), end.isoformat()),
        ).fetchall()

    # ---- long memory ----
    @_locked
    def add_long_memory(self, user_id: str, content: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO long_memory (user_id, content, ts) VALUES (?, ?, ?)",
            (user_id, content, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()
        return cur.lastrowid

    @_locked
    def prune_long_memory(self, user_id: str, keep: int = 800) -> list[int]:
        """长期记忆超过上限时删除最旧的记录，返回被删除的 id 列表。

        调用方拿到 id 后应同步清理对应的向量索引，避免 Chroma 里残留孤儿向量。
        长期记忆表按用户无限增长，每轮对话还会双写（用户说/菟菚说），
        这里把每用户记录数限制在 keep 条以内。
        """
        keep_rows = self.conn.execute(
            "SELECT id FROM long_memory WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, keep),
        ).fetchall()
        keep_ids = {r["id"] for r in keep_rows}
        all_rows = self.conn.execute(
            "SELECT id FROM long_memory WHERE user_id=?", (user_id,)
        ).fetchall()
        removed = [r["id"] for r in all_rows if r["id"] not in keep_ids]
        if removed:
            self.conn.executemany(
                "DELETE FROM long_memory WHERE user_id=? AND id=?",
                [(user_id, rid) for rid in removed],
            )
            self.conn.commit()
        return removed

    @_locked
    def search_long_memory(self, user_id: str, query: str, top_k: int):
        """v1 关键词检索：按中文字符二元组重叠打分，取 top_k。

        重叠阈值与 search_long_memory_multi 一致：短查询（≤2 二元组）要求 2 个命中，
        长查询放宽到 1 个（口语措辞差异容忍），噪声由调用方重排过滤。
        """
        q_bigrams = _bigrams(query)
        if not q_bigrams:
            return []
        rows = self.conn.execute(
            "SELECT id, content, ts FROM long_memory WHERE user_id = ? "
            "ORDER BY id DESC LIMIT 500",
            (user_id,),
        ).fetchall()
        scored = []
        min_overlap = 1 if len(q_bigrams) != 2 else 2
        for r in rows:
            content_bigrams = _bigrams(r["content"])
            overlap = len(q_bigrams & content_bigrams)
            if overlap >= min_overlap:
                scored.append((overlap, r["content"]))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"content": c} for _, c in scored[:top_k]]

    @_locked
    def search_long_memory_multi(self, user_id: str, queries: list[str], top_k: int):
        """多查询词合并检索：每个查询独立打分后按最高分汇总，取 top_k。

        语义检索的落地方式：LLM 把用户问题扩展成若干关键词/短语，逐一检索，
        比单条原文命中更稳（原句里的口语词常常和存档时的措辞对不上）。

        重叠阈值：短查询（≤2 个二元组，如"下雨天"）要求 2 个二元组全命中；
        长查询（整句/长短语）放宽到 1 个，避免因口语措辞差异漏检——放宽带来的
        噪声由调用方后续的 TF-IDF 重排过滤。
        """
        scored: dict[int, tuple[int, str]] = {}
        for query in queries:
            q_bigrams = _bigrams(query)
            if not q_bigrams:
                continue
            # len==1（2字查询）只能要求1个重叠；len==2（3字）要求2个；
            # len>=3（整句/长短语）放宽到1个（口语措辞差异容忍，噪声由重排过滤）
            min_overlap = 1 if len(q_bigrams) != 2 else 2
            rows = self.conn.execute(
                "SELECT id, content, ts FROM long_memory WHERE user_id = ? "
                "ORDER BY id DESC LIMIT 500",
                (user_id,),
            ).fetchall()
            for r in rows:
                content_bigrams = _bigrams(r["content"])
                overlap = len(q_bigrams & content_bigrams)
                if overlap >= min_overlap and overlap > scored.get(r["id"], (0, ""))[0]:
                    scored[r["id"]] = (overlap, r["content"])
        ranked = sorted(scored.values(), key=lambda x: x[0], reverse=True)
        return [{"content": c} for _, c in ranked[:top_k]]

    # ---- facts（LLM 提炼的长期事实）----
    @_locked
    def add_fact(self, user_id: str, content: str) -> int | None:
        """存一条事实；与已有事实二元组重叠≥50% 视为重复则跳过。

        返回新记录 id；重复/跳过返回 None。
        """
        content = content.strip()
        if not content:
            return None
        q = _bigrams(content)
        rows = self.conn.execute(
            "SELECT content FROM facts WHERE user_id = ? ORDER BY id DESC LIMIT 200",
            (user_id,),
        ).fetchall()
        for r in rows:
            existing = _bigrams(r["content"])
            if q and existing:
                overlap = len(q & existing) / min(len(q), len(existing))
                if overlap >= 0.5:
                    return None
        cur = self.conn.execute(
            "INSERT INTO facts (user_id, content, ts) VALUES (?, ?, ?)",
            (user_id, content, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()
        return cur.lastrowid

    @_locked
    def search_facts(self, user_id: str, query: str, top_k: int):
        """按关键词（二元组）检索事实，取 top_k。

        重叠阈值与 long_memory 检索一致：短查询（≤2 二元组）要求 2 个命中，
        长查询放宽到 1 个（口语措辞差异容忍）。
        """
        q_bigrams = _bigrams(query)
        if not q_bigrams:
            return []
        rows = self.conn.execute(
            "SELECT content FROM facts WHERE user_id = ? ORDER BY id DESC LIMIT 500",
            (user_id,),
        ).fetchall()
        min_overlap = 1 if len(q_bigrams) != 2 else 2
        scored = []
        for r in rows:
            content_bigrams = _bigrams(r["content"])
            overlap = len(q_bigrams & content_bigrams)
            if overlap >= min_overlap:
                scored.append((overlap, r["content"]))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"content": c} for _, c in scored[:top_k]]

    # ---- 用户画像（user_profile）----

    @_locked
    def add_profile(self, user_id: str, category: str, content: str, source: str = "llm") -> int | None:
        """存一条画像条目；同分类下与已有条目重叠≥50% 视为重复则跳过。

        category：basic / likes / dislikes / habits / personality / other。
        返回新记录 id；重复/跳过返回 None。
        """
        content = content.strip()
        if not content or not category:
            return None
        q = _bigrams(content)
        rows = self.conn.execute(
            "SELECT content FROM user_profile WHERE user_id = ? AND category = ? ORDER BY id DESC LIMIT 200",
            (user_id, category),
        ).fetchall()
        for r in rows:
            existing = _bigrams(r["content"])
            if q and existing:
                overlap = len(q & existing) / min(len(q), len(existing))
                if overlap >= 0.5:
                    return None
        cur = self.conn.execute(
            "INSERT INTO user_profile (user_id, category, content, source, ts) VALUES (?, ?, ?, ?, ?)",
            (user_id, category, content, source, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()
        return cur.lastrowid

    @_locked
    def get_profile(self, user_id: str, category: str | None = None) -> list[dict]:
        """读取画像条目；category 为空返回全部（按分类分组排序）。"""
        if category:
            rows = self.conn.execute(
                "SELECT id, category, content, source, ts FROM user_profile WHERE user_id = ? AND category = ? ORDER BY id",
                (user_id, category),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id, category, content, source, ts FROM user_profile WHERE user_id = ? ORDER BY id",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    @_locked
    def del_profile(self, user_id: str, profile_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM user_profile WHERE user_id = ? AND id = ?", (user_id, profile_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    @_locked
    def clear_profile(self, user_id: str, category: str | None = None) -> int:
        if category:
            cur = self.conn.execute(
                "DELETE FROM user_profile WHERE user_id = ? AND category = ?", (user_id, category)
            )
        else:
            cur = self.conn.execute("DELETE FROM user_profile WHERE user_id = ?", (user_id,))
        self.conn.commit()
        return cur.rowcount

    # ---- 用户口头禅/黑话（user_terms）----

    @_locked
    def add_term(self, user_id: str, term: str, category: str = "catchphrase", meaning: str = "") -> bool:
        """记录用户口头禅/黑话；已存在则次数 +1 并刷新 last_seen。

        返回是否新增（False=已存在只累加）。
        """
        term = term.strip()[:20]
        if not term or len(term) < 1:
            return False
        now = datetime.now().isoformat(timespec="seconds")
        row = self.conn.execute(
            "SELECT id FROM user_terms WHERE user_id = ? AND term = ?", (user_id, term)
        ).fetchone()
        if row:
            self.conn.execute(
                "UPDATE user_terms SET count = count + 1, last_seen = ? WHERE id = ?",
                (now, row["id"]),
            )
            self.conn.commit()
            return False
        self.conn.execute(
            "INSERT INTO user_terms (user_id, term, category, meaning, count, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)",
            (user_id, term, category, meaning, now, now),
        )
        self.conn.commit()
        return True

    @_locked
    def get_terms(self, user_id: str, limit: int = 30) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, term, category, meaning, count FROM user_terms "
            "WHERE user_id = ? ORDER BY count DESC, id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    @_locked
    def del_term(self, user_id: str, term_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM user_terms WHERE user_id = ? AND id = ?", (user_id, term_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ---- 场景化表达风格（user_style_map）----

    @_locked
    def add_style_map(self, user_id: str, situation: str, style: str) -> bool:
        """记录「场景→表达方式」；同场景视为重复：累加次数并更新最新 style（去重键按场景而非场景+风格，避免近似表述重复堆积）。"""
        situation = situation.strip()[:40]
        style = style.strip()[:60]
        if not situation or not style:
            return False
        now = datetime.now().isoformat(timespec="seconds")
        row = self.conn.execute(
            "SELECT id FROM user_style_map WHERE user_id = ? AND situation = ?",
            (user_id, situation),
        ).fetchone()
        if row:
            self.conn.execute(
                "UPDATE user_style_map SET count = count + 1, style = ? WHERE id = ?",
                (style, row["id"]),
            )
            self.conn.commit()
            return False
        self.conn.execute(
            "INSERT INTO user_style_map (user_id, situation, style, count, ts) VALUES (?, ?, ?, 1, ?)",
            (user_id, situation, style, now),
        )
        self.conn.commit()
        return True

    @_locked
    def get_style_map(self, user_id: str, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, situation, style, count FROM user_style_map "
            "WHERE user_id = ? ORDER BY count DESC, id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    @_locked
    def del_style_map(self, user_id: str, style_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM user_style_map WHERE user_id = ? AND id = ?", (user_id, style_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ---- 事实提炼游标 ----
    @_locked
    def get_last_fact_msg_id(self, user_id: str) -> int:
        row = self.conn.execute(
            "SELECT last_fact_msg_id FROM user_meta WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["last_fact_msg_id"] if row else 0

    @_locked
    def set_last_fact_msg_id(self, user_id: str, msg_id: int) -> None:
        self.conn.execute(
            "INSERT INTO user_meta (user_id, last_fact_msg_id) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_fact_msg_id = excluded.last_fact_msg_id",
            (user_id, msg_id),
        )
        self.conn.commit()

    @_locked
    def get_last_profile_msg_id(self, user_id: str) -> int:
        row = self.conn.execute(
            "SELECT last_profile_msg_id FROM user_meta WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["last_profile_msg_id"] if row else 0

    @_locked
    def set_last_profile_msg_id(self, user_id: str, msg_id: int) -> None:
        self.conn.execute(
            "INSERT INTO user_meta (user_id, last_profile_msg_id) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_profile_msg_id = excluded.last_profile_msg_id",
            (user_id, msg_id),
        )
        self.conn.commit()

    @_locked
    def messages_after(self, user_id: str, after_id: int, limit: int):
        return self.conn.execute(
            "SELECT id, role, content FROM messages WHERE user_id = ? AND id > ? ORDER BY id LIMIT ?",
            (user_id, after_id, limit),
        ).fetchall()

    @_locked
    def max_message_id(self, user_id: str) -> int:
        row = self.conn.execute(
            "SELECT MAX(id) AS m FROM messages WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["m"] or 0

    @_locked
    def last_message_ts(self, user_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT ts FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
        ).fetchone()
        return row["ts"] if row else None

    @_locked
    def last_assistant_message(self, user_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT content FROM messages WHERE user_id = ? AND role = 'assistant' "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return row["content"] if row else None

    @_locked
    def reset(self) -> None:
        """清空所有数据（用于重复测试）。

        优先删除数据库文件重建；若文件被其他进程占用（WinError 32），
        自动退化为用 SQL 清空全部表，保证功能可用。
        """
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.commit()
        self.conn.close()

        path = config.data_dir / "bot.db"
        deleted = False
        for _ in range(3):
            try:
                path.unlink()
                deleted = True
                break
            except PermissionError:
                time.sleep(0.3)

        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        if deleted:
            self.conn.executescript(_SCHEMA)
        else:
            # 文件删除失败（被占用）时退化的清空路径：覆盖全部业务表
            for table in (
                "affection_log", "long_memory", "facts", "user_meta", "messages",
                "users", "kv_store", "important_dates", "stickers",
                "user_profile", "user_terms", "user_style_map", "diary", "research_reports", "triples",
                "tasks", "promises",
            ):
                self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()


def _bigrams(text: str) -> set[str]:
    text = text.strip()
    if len(text) < 2:
        return set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


# ---- important_dates（情感记忆：生日/纪念日/特殊日子）----


def save_important_date(user_id: str, date_str: str, label: str, kind: str = "other", year: int | None = None) -> bool:
    """保存一个特殊日子。date_str 格式为 'MM-DD'（如 '12-25'）。

    去重：同用户、同日、同标签 已存在时不重复插入（返回 False），
    仅当新信息（kind/year）更全时更新。返回是否新增。
    """
    with db._lock:
        row = db.conn.execute(
            "SELECT id, kind, year FROM important_dates WHERE user_id = ? AND date = ? AND label = ?",
            (user_id, date_str, label),
        ).fetchone()
        if row:
            # 已存在：补全缺失的 kind/year（如首次识别没年份、复盘补上了）
            # 注意：birthday/anniversary 的 year 不补全（保持每年过），
            # 只有 kind='other' 的一次性日子才补 year。
            if (not row["kind"] or row["kind"] == "other") and kind != "other":
                db.conn.execute(
                    "UPDATE important_dates SET kind = ? WHERE id = ?", (kind, row["id"])
                )
            if row["year"] is None and year is not None and kind != "birthday" and kind != "anniversary":
                db.conn.execute(
                    "UPDATE important_dates SET year = ? WHERE id = ?", (year, row["id"])
                )
            db.conn.commit()
            return False
        db.conn.execute(
            "INSERT INTO important_dates (user_id, date, label, kind, year, ts) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, date_str, label, kind, year, datetime.now().isoformat(timespec="seconds")),
        )
        db.conn.commit()
        return True


def get_today_important_dates(user_id: str) -> list[dict]:
    """查询今天有哪些特殊日子（MM-DD 匹配）。见 get_dates_for。"""
    return get_dates_for(user_id, date.today())


def get_dates_for(user_id: str, day: date) -> list[dict]:
    """查询指定日期有哪些特殊日子（MM-DD 匹配）。

    - birthday / anniversary：每年都过（忽略 year，带出生年份也照常触发）
    - other（一次性纪念日）：只在 year 匹配该日年份（或未标年份）时触发
    """
    md = day.strftime("%m-%d")
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM important_dates WHERE user_id = ? AND date = ? "
            "AND (kind IN ('birthday', 'anniversary') OR year IS NULL OR year = ?) ORDER BY kind",
            (user_id, md, day.year),
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_important_dates(user_id: str) -> list[dict]:
    """查询该用户所有特殊日子。"""
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM important_dates WHERE user_id = ? ORDER BY date", (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---- promises（约定与跟进：双方许下的事，到点菟菚主动问起）----


def save_promise(user_id: str, content: str, follow_up: str = "", source: str = "") -> int | None:
    """保存一条约定。同用户存在相同内容的 pending 约定时不重复插入（返回 None）。"""
    content = content.strip()[:100]
    if not content:
        return None
    follow_up = follow_up.strip()[:10]
    with db._lock:
        row = db.conn.execute(
            "SELECT id, follow_up FROM promises WHERE user_id = ? AND content = ? AND status = 'pending'",
            (user_id, content),
        ).fetchone()
        if row:
            # 已存在：补全此前缺失的跟进日期
            if not row["follow_up"] and follow_up:
                db.conn.execute(
                    "UPDATE promises SET follow_up = ? WHERE id = ?", (follow_up, row["id"])
                )
                db.conn.commit()
            return None
        cur = db.conn.execute(
            "INSERT INTO promises (user_id, content, follow_up, source, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, content, follow_up, source[:200], datetime.now().isoformat(timespec="seconds")),
        )
        db.conn.commit()
        return int(cur.lastrowid)


def get_due_promises(user_id: str, day: date) -> list[dict]:
    """到点该跟进的约定：pending 且 follow_up 已到期（≤ day）。"""
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM promises WHERE user_id = ? AND status = 'pending' "
            "AND follow_up != '' AND follow_up <= ? ORDER BY follow_up",
            (user_id, day.isoformat()),
        ).fetchall()
    return [dict(r) for r in rows]


def get_open_promises(user_id: str, limit: int = 5) -> list[dict]:
    """所有未完成的约定（不限日期，供对话内自然提起）。"""
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM promises WHERE user_id = ? AND status = 'pending' "
            "ORDER BY CASE WHEN follow_up = '' THEN 1 ELSE 0 END, follow_up LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_promise_done(promise_id: int) -> None:
    with db._lock:
        db.conn.execute(
            "UPDATE promises SET status = 'done', done_at = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), promise_id),
        )
        db.conn.commit()


def cancel_promise(promise_id: int) -> None:
    with db._lock:
        db.conn.execute(
            "UPDATE promises SET status = 'cancelled', done_at = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), promise_id),
        )
        db.conn.commit()


# ---- facts 删改（C7 记忆纠偏：她记错的事可以真改）----


def list_facts(user_id: str, limit: int = 200) -> list[dict]:
    """列出用户的事实记忆（新→旧），供记忆管理页/纠偏仲裁。"""
    with db._lock:
        rows = db.conn.execute(
            "SELECT id, content, ts FROM facts WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_fact(user_id: str, fact_id: int) -> bool:
    """删除一条事实。返回是否真的删到（存在且属于该用户）。"""
    with db._lock:
        cur = db.conn.execute(
            "DELETE FROM facts WHERE id = ? AND user_id = ?", (fact_id, user_id)
        )
        db.conn.commit()
        return cur.rowcount > 0


def update_fact(user_id: str, fact_id: int, content: str) -> bool:
    """改写一条事实的内容。返回是否真的改到。"""
    content = content.strip()[:100]
    if not content:
        return False
    with db._lock:
        cur = db.conn.execute(
            "UPDATE facts SET content = ? WHERE id = ? AND user_id = ?",
            (content, fact_id, user_id),
        )
        db.conn.commit()
        return cur.rowcount > 0


def delete_important_date(date_id: int, user_id: str | None = None) -> bool:
    """删除一条特殊日子记录。若指定 user_id，则只有该用户的日子才被删（跨用户隔离）。"""
    with db._lock:
        if user_id:
            cur = db.conn.execute(
                "DELETE FROM important_dates WHERE id = ? AND user_id = ?", (date_id, user_id)
            )
        else:
            cur = db.conn.execute("DELETE FROM important_dates WHERE id = ?", (date_id,))
        ok = cur.rowcount > 0
        db.conn.commit()
        return ok


# ---- stickers（表情包收藏）----


def save_sticker(user_id: str, file: str, url: str, desc: str, emotion: str = "") -> int:
    """收藏一张表情包；同 URL 已存在则累计 count，返回记录 id。"""
    with db._lock:
        db.conn.execute("PRAGMA busy_timeout = 5000")
        try:
            row = db.conn.execute(
                "SELECT id FROM stickers WHERE user_id = ? AND url = ?", (user_id, url)
            ).fetchone()
        except sqlite3.OperationalError:
            db.conn.executescript(_SCHEMA)  # 旧库补建表
            row = db.conn.execute(
                "SELECT id FROM stickers WHERE user_id = ? AND url = ?", (user_id, url)
            ).fetchone()
        if row:
            db.conn.execute(
                "UPDATE stickers SET count = count + 1, desc = CASE WHEN desc = '' THEN ? ELSE desc END "
                "WHERE id = ?",
                (desc, row["id"]),
            )
            if emotion:
                update_sticker_emotion(row["id"], emotion)
            db.conn.commit()
            return row["id"]
        cur = db.conn.execute(
            "INSERT INTO stickers (user_id, file, url, desc, emotion, count, ts) VALUES (?, ?, ?, ?, ?, 1, ?)",
            (user_id, file, url, desc, emotion, datetime.now().isoformat(timespec="seconds")),
        )
        db.conn.commit()
        return cur.lastrowid


def get_stickers(user_id: str, limit: int = 50) -> list[dict]:
    """取该用户收藏的表情包（按出现次数排序，热门靠前）。"""
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM stickers WHERE user_id = ? ORDER BY count DESC, id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_sticker_used(sticker_id: int) -> None:
    """记录一次贴纸复用，让常用收藏自然排到前面。"""
    if not sticker_id:
        return
    with db._lock:
        db.conn.execute("UPDATE stickers SET count = count + 1 WHERE id = ?", (sticker_id,))
        db.conn.commit()


def get_sticker_by_desc(user_id: str, keyword: str, limit: int = 30) -> list[dict]:
    """按描述关键词挑表情包（话题匹配回发）。

    用「描述里是否包含关键词的任一词/子串」判断（对中文单字词友好），
    有多词时按命中词数排序。keyword 为空返回热门几张。
    """
    kw = re.split(r"[\s,，。！、/]+", keyword.strip())
    kw = [k for k in kw if k]
    if not kw:
        return []
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM stickers WHERE user_id = ? ORDER BY count DESC LIMIT 300",
            (user_id,),
        ).fetchall()
        scored = []
        for r in rows:
            desc = r["desc"] or ""
            hits = sum(1 for k in kw if k in desc)
            if hits > 0:
                scored.append((hits, dict(r)))
    scored.sort(key=lambda x: (x[0], x[1].get("count", 0)), reverse=True)
    return [d for _, d in scored[:limit]]


def get_sticker_by_emotion(user_id: str, emotion: str, limit: int = 10) -> list[dict]:
    """按情绪标签挑表情包（情绪匹配回发）。

    emotion 是单个情绪词（如"开心""难过"）；匹配 emotion 字段中包含该词的收藏，
    无匹配返回 []（调用方再回退到话题/热门）。
    """
    emotion = emotion.strip()
    if not emotion:
        return []
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM stickers WHERE user_id = ? ORDER BY count DESC LIMIT 300",
            (user_id,),
        ).fetchall()
        hits = []
        for r in rows:
            emo = (r["emotion"] or "").split(",")
            if any(emotion in e.strip() or e.strip() in emotion for e in emo if e.strip()):
                hits.append(dict(r))
    hits.sort(key=lambda x: -x.get("count", 0))
    return hits[:limit]


def update_sticker_emotion(sticker_id: int, emotion: str) -> None:
    """为指定表情包写入/合并情绪标签。"""
    if not sticker_id:
        return
    emotion = emotion.strip()
    if not emotion:
        return
    with db._lock:
        row = db.conn.execute("SELECT emotion FROM stickers WHERE id = ?", (sticker_id,)).fetchone()
        if not row:
            return
        existing = {e.strip() for e in (row["emotion"] or "").split(",") if e.strip()}
        for e in emotion.split(","):
            e = e.strip()
            if e:
                existing.add(e)
        merged = ",".join(sorted(existing))
        db.conn.execute("UPDATE stickers SET emotion = ? WHERE id = ?", (merged, sticker_id))
        db.conn.commit()


db = UserDB()


# ---- kv_store（通用键值存储，用于每日奖励去重等）----


def kv_get(user_id: str, key: str) -> str | None:
    """读取 kv 值；不存在返回 None。"""
    with db._lock:
        row = db.conn.execute(
            "SELECT value FROM kv_store WHERE user_id=? AND key=?", (user_id, key)
        ).fetchone()
    return row["value"] if row else None


def kv_set(user_id: str, key: str, value: str) -> None:
    """写入 kv 值（UPSERT）。"""
    with db._lock:
        db.conn.execute(
            "INSERT OR REPLACE INTO kv_store (user_id, key, value) VALUES (?, ?, ?)",
            (user_id, key, value),
        )
        db.conn.commit()


def kv_del(user_id: str, key: str) -> bool:
    """删除 kv 值；删除成功（原本存在）返回 True，否则 False。"""
    with db._lock:
        cur = db.conn.execute(
            "DELETE FROM kv_store WHERE user_id=? AND key=?", (user_id, key)
        )
        db.conn.commit()
        return cur.rowcount > 0


# ---- diary / research_reports（C2：私人日记与阶段研究报告）----


def save_diary(user_id: str, day: str, content: str, mood: str = "") -> int | None:
    """幂等写入某天日记；已存在时不覆盖，返回现有/新增 id。"""
    content = content.strip()
    if not content:
        return None
    with db._lock:
        db.conn.execute(
            "INSERT OR IGNORE INTO diary (user_id, date, content, mood, ts) VALUES (?, ?, ?, ?, ?)",
            (user_id, day, content[:1200], mood.strip()[:24], datetime.now().isoformat(timespec="seconds")),
        )
        row = db.conn.execute(
            "SELECT id FROM diary WHERE user_id = ? AND date = ?", (user_id, day)
        ).fetchone()
        db.conn.commit()
        return int(row["id"]) if row else None


def get_diary(user_id: str, day: str) -> dict | None:
    with db._lock:
        row = db.conn.execute(
            "SELECT id, date, content, mood, ts FROM diary WHERE user_id = ? AND date = ?",
            (user_id, day),
        ).fetchone()
    return dict(row) if row else None


def list_diaries(user_id: str, limit: int = 60) -> list[dict]:
    with db._lock:
        rows = db.conn.execute(
            "SELECT id, date, content, mood, ts FROM diary WHERE user_id = ? "
            "ORDER BY date DESC LIMIT ?",
            (user_id, max(1, min(365, int(limit)))),
        ).fetchall()
    return [dict(row) for row in rows]


def save_research_report(user_id: str, period: str, title: str, content: str) -> int | None:
    content = content.strip()
    if not content:
        return None
    with db._lock:
        db.conn.execute(
            "INSERT OR IGNORE INTO research_reports (user_id, period, title, content, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                user_id, period[:32], title.strip()[:80] or "观察人类：阶段记录",
                content[:2400], datetime.now().isoformat(timespec="seconds"),
            ),
        )
        row = db.conn.execute(
            "SELECT id FROM research_reports WHERE user_id = ? AND period = ?", (user_id, period[:32])
        ).fetchone()
        db.conn.commit()
        return int(row["id"]) if row else None


def list_research_reports(user_id: str, limit: int = 24) -> list[dict]:
    with db._lock:
        rows = db.conn.execute(
            "SELECT id, period, title, content, ts FROM research_reports WHERE user_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (user_id, max(1, min(100, int(limit)))),
        ).fetchall()
    return [dict(row) for row in rows]
