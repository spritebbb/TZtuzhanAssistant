"""SQLite 数据层：每用户独立数据。

表：
- users         用户状态（好感度、称呼偏好、恋人确认、首次对话、日期标记）
- messages      会话历史（短期上下文的来源）
- long_memory   长期记忆原文片段（关键词检索）
- facts         LLM 提炼的长期事实（喜好/约定等，带去重）
- user_meta     事实提炼游标等元数据
- affection_log 好感度变动流水
- mood_log      心情绝对值流水（养成仪表盘趋势）
- activities    共同活动进度（D3，首期为共读）
- activity_notes 共同活动的分段书签/笔记
"""
import re
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta

from .config import config
from ..maintenance.schema_backup import create_pre_upgrade_backup, mark_schema_current

_SCHEMA_VERSION = 38  # v38: L07 aesthetic preferences and artifact placements

_SCHEMA = """
CREATE TABLE IF NOT EXISTS aesthetic_preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
    owner TEXT NOT NULL CHECK(owner IN ('user','assistant')),
    category TEXT NOT NULL CHECK(category IN ('color','style','motif','layout')),
    value TEXT NOT NULL, origin TEXT NOT NULL, source_type TEXT, source_id INTEGER,
    status TEXT NOT NULL DEFAULT 'active', UNIQUE(user_id,owner,category)
);
CREATE TABLE IF NOT EXISTS artifact_placements (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, artifact_id INTEGER NOT NULL,
    slot TEXT NOT NULL DEFAULT 'room' CHECK(slot='room'), x REAL NOT NULL CHECK(x BETWEEN 0 AND 1),
    y REAL NOT NULL CHECK(y BETWEEN 0 AND 1), theme_version INTEGER NOT NULL DEFAULT 1 CHECK(theme_version=1),
    hidden INTEGER NOT NULL DEFAULT 0 CHECK(hidden IN (0,1)), UNIQUE(user_id,artifact_id)
);
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
    mood_updated_at TEXT,
    trust           INTEGER,            -- P2-01 二维关系：信任 0-100（NULL=未迁移）
    intimacy        INTEGER             -- P2-01 二维关系：亲密 0-100
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
    ts      TEXT NOT NULL,
    pinned  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS affection_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    delta   INTEGER NOT NULL,
    reason  TEXT NOT NULL,
    ts      TEXT NOT NULL,
    value   INTEGER
);
CREATE TABLE IF NOT EXISTS mood_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    value   INTEGER NOT NULL,
    ts      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            TEXT NOT NULL,
    content            TEXT NOT NULL,
    ts                 TEXT NOT NULL,
    source_type        TEXT NOT NULL DEFAULT 'legacy',
    source_message_ids TEXT NOT NULL DEFAULT '[]',
    confidence         REAL NOT NULL DEFAULT 0.5,
    verified_at        TEXT,
    expires_at         TEXT,
    pinned             INTEGER NOT NULL DEFAULT 0,
    surface_policy     TEXT NOT NULL DEFAULT 'normal',
    status             TEXT NOT NULL DEFAULT 'active',
    conflicts_with_fact_id INTEGER
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
    ts      TEXT NOT NULL,
    namespace TEXT NOT NULL DEFAULT 'user_real'  -- P1-05：user_real / character_fiction
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
    status     TEXT NOT NULL DEFAULT 'open',  -- open / done / cancelled / expired
    source     TEXT NOT NULL DEFAULT '',    -- 来源对话片段（溯源）
    created_at TEXT NOT NULL,
    done_at    TEXT,
    owner      TEXT NOT NULL DEFAULT 'user',   -- P2-03：谁许下的（user/assistant），旧行=user
    due_at     TEXT,                           -- 到期（可空；空不自动失约）
    action_kind TEXT,                          -- 可执行动作类别；空=纯叙事约定
    namespace  TEXT NOT NULL DEFAULT 'user_real',  -- 角色虚构约定标 character_fiction
    source_message_id INTEGER,
    promise_hash TEXT                          -- NFKC→lower→去标点→压空白 后 sha256
);
-- token 用量（D5 成本面板）：每次 LLM 调用一行
CREATE TABLE IF NOT EXISTS usage_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           TEXT NOT NULL,
    channel           TEXT NOT NULL,         -- reply / chat / perception / tool
    model             TEXT NOT NULL DEFAULT '',
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    estimated         INTEGER NOT NULL DEFAULT 0,  -- 1=本地估算（端点未返回 usage）
    ts                TEXT NOT NULL
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
CREATE TABLE IF NOT EXISTS kb_documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    filename    TEXT NOT NULL,    -- 原始文件名（展示用）
    stored_path TEXT NOT NULL,    -- 落盘路径（data/documents/ 下）
    format      TEXT NOT NULL,    -- pdf / txt / md
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    ts          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_chunks (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    doc_id  INTEGER NOT NULL,     -- 对应 kb_documents.id
    seq     INTEGER NOT NULL,     -- 文档内分块序号
    text    TEXT NOT NULL,
    ts      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_docs_user ON kb_documents(user_id);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_doc ON kb_chunks(user_id, doc_id);
-- P3-02C 知识内化：角色观点与来源片段分表，绝不写入用户事实。
CREATE TABLE IF NOT EXISTS knowledge_opinions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    document_id INTEGER NOT NULL,
    stance TEXT NOT NULL,
    opinion_hash TEXT NOT NULL,
    origin TEXT NOT NULL,             -- assistant / user
    confidence REAL NOT NULL DEFAULT 0.5,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active', -- active / revoked
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (user_id, document_id, opinion_hash)
);
CREATE TABLE IF NOT EXISTS knowledge_opinion_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    opinion_id INTEGER NOT NULL,
    chunk_id INTEGER NOT NULL,
    start_offset INTEGER NOT NULL DEFAULT 0,
    end_offset INTEGER NOT NULL DEFAULT 0,
    source_hash TEXT NOT NULL,
    UNIQUE (user_id, opinion_id, chunk_id, start_offset, end_offset)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_opinions_user
    ON knowledge_opinions(user_id, document_id, status);
CREATE INDEX IF NOT EXISTS idx_knowledge_opinion_sources
    ON knowledge_opinion_sources(user_id, opinion_id);
-- D3 共同活动：通用活动壳，首期落地「共读」。
CREATE TABLE IF NOT EXISTS activities (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'reading',
    document_id  INTEGER NOT NULL,   -- focus 类型用 0 作哨兵（无文档）
    title        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',  -- active / paused / completed / cancelled
    position     INTEGER NOT NULL DEFAULT 0,
    planned_minutes INTEGER,          -- M3.2 focus：计划时长（25/50）
    remaining_seconds INTEGER,        -- M3.2 focus：暂停时结算的剩余秒数
    ends_at      TEXT,                -- M3.2 focus：进行中时的预计结束时刻
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS activity_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    activity_id INTEGER NOT NULL,
    position    INTEGER NOT NULL,
    content     TEXT NOT NULL,
    ts          TEXT NOT NULL,
    UNIQUE(activity_id, position)
);
CREATE INDEX IF NOT EXISTS idx_activities_user_status
    ON activities(user_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_activity_notes_activity
    ON activity_notes(user_id, activity_id, position);
-- Sprint 3 共读 2.0：双方观点按角色分开保存，模型观点不得冒充用户观点。
CREATE TABLE IF NOT EXISTS activity_viewpoints (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    activity_id INTEGER NOT NULL,
    role        TEXT NOT NULL,      -- user / tuzhan / shared
    position    INTEGER NOT NULL,   -- 针对哪一段（-1 表示全书整体）
    content     TEXT NOT NULL,
    ts          TEXT NOT NULL,
    UNIQUE(activity_id, role, position)
);
CREATE INDEX IF NOT EXISTS idx_activity_viewpoints_activity
    ON activity_viewpoints(user_id, activity_id);
-- M3.3 共同目标：活动壳负责生命周期，目标表保存动机、下一步与陪伴偏好。
CREATE TABLE IF NOT EXISTS activity_goals (
    activity_id  INTEGER PRIMARY KEY,
    user_id      TEXT NOT NULL,
    motivation   TEXT NOT NULL DEFAULT '',
    next_step    TEXT NOT NULL DEFAULT '',
    support_mode TEXT NOT NULL DEFAULT 'companion', -- companion / reminder
    reminder_at  TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS goal_progress (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    activity_id INTEGER NOT NULL,
    content     TEXT NOT NULL,
    percent     INTEGER,
    next_step   TEXT NOT NULL DEFAULT '',
    ts          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_goals_user
    ON activity_goals(user_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_goal_progress_activity
    ON goal_progress(user_id, activity_id, ts);
-- M3.4 共同创作：活动壳负责生命周期，开头设定与轮流正文保存在专属侧表。
-- 虚构隔离：故事正文只存这里，不进会话历史与记忆提炼；进 prompt 的唯一
-- 通道是 cowriting.cowriting_context 的虚构声明门控注入。
CREATE TABLE IF NOT EXISTS activity_writings (
    activity_id  INTEGER PRIMARY KEY,
    user_id      TEXT NOT NULL,
    premise      TEXT NOT NULL DEFAULT '',   -- 开头设定（题材/世界观一句话）
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS writing_turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    activity_id INTEGER NOT NULL,
    author      TEXT NOT NULL,      -- user / tuzhan
    content     TEXT NOT NULL,
    ts          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_writing_turns_activity
    ON writing_turns(user_id, activity_id, id);
-- M3.4 共同清单（歌单/书单）：活动壳负责生命周期，清单类型与条目在专属侧表。
CREATE TABLE IF NOT EXISTS activity_lists (
    activity_id  INTEGER PRIMARY KEY,
    user_id      TEXT NOT NULL,
    list_kind    TEXT NOT NULL DEFAULT 'song',   -- song / book
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS list_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    activity_id INTEGER NOT NULL,
    title       TEXT NOT NULL,
    creator     TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',
    added_by    TEXT NOT NULL DEFAULT 'user',    -- user / tuzhan
    ts          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_list_items_activity
    ON list_items(user_id, activity_id, id);
-- M8「写给未来的我们」：用户亲手写的未来信件。正文是唯一的私密内容，
-- 锁定态（sealed 且条件未达成）绝不离开后端；ready 由查询时按条件确定性计算。
CREATE TABLE IF NOT EXISTS future_letters (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    body          TEXT NOT NULL,                   -- 正文：锁定态任何接口都不返回
    unlock_type   TEXT NOT NULL,                   -- date / goal / event
    unlock_at     TEXT,                            -- date：到达时刻（必须晚于创建时间）
    goal_id       INTEGER,                         -- goal：activities.id（kind='goal'）
    event_type    TEXT,                            -- event：完成/修复类事件白名单
    status        TEXT NOT NULL DEFAULT 'sealed',  -- sealed / opened（ready 是计算态不落状态列）
    unlocked_at   TEXT,                            -- 条件达成落账时间（惰性写、幂等）
    unlocked_by_event_id INTEGER,                  -- event 类型首次匹配的关系事件 id
    opened_at     TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_future_letters_user
    ON future_letters(user_id, status, id);
-- M2 前置最小版：关系事件事实层。本切片只写 reading_finished，
-- 后续事件类型、pending_thoughts 与 Narrative Planner 按 Sprint 2 扩展。
CREATE TABLE IF NOT EXISTS relationship_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    event_type   TEXT NOT NULL,      -- 首批：reading_finished
    source_type  TEXT NOT NULL,      -- 来源表：activity / promise / important_date / fact
    source_id    INTEGER NOT NULL,
    subject      TEXT NOT NULL DEFAULT '',
    object       TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    confidence   REAL NOT NULL DEFAULT 1.0,
    privacy      TEXT NOT NULL DEFAULT 'normal',  -- normal / sensitive / ephemeral / never_surface
    occurred_at  TEXT NOT NULL,
    expires_at   TEXT,
    status       TEXT NOT NULL DEFAULT 'active',  -- active / corrected / forgotten / archived
    created_at   TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_relationship_events_idem
    ON relationship_events(user_id, event_type, source_id) WHERE status = 'active';
-- M8 第二垂直切片：30/100/365 天关系快照。确定性汇编真实持久数据（不调用
-- LLM），用户显式创建/删除、绝不自动再生；UNIQUE(user_id, snapshot_days)
-- 保证每个里程碑至多一页。
CREATE TABLE IF NOT EXISTS relationship_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             TEXT NOT NULL,
    snapshot_days       INTEGER NOT NULL,              -- 30 / 100 / 365
    start_date          TEXT NOT NULL,                 -- 关系起点（最早 user 消息的本地日期）
    cutoff_date         TEXT NOT NULL,                 -- start_date + snapshot_days - 1
    generated_at        TEXT NOT NULL,                 -- 快照整理时刻
    source_manifest_json TEXT NOT NULL DEFAULT '{}',   -- 来源清单（type/id/date + counts/omitted）
    rendered_markdown   TEXT NOT NULL,                 -- 模板化正文（只来自真实行）
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(user_id, snapshot_days)
);
CREATE INDEX IF NOT EXISTS idx_relationship_snapshots_user
    ON relationship_snapshots(user_id, snapshot_days);
-- M8 第三垂直切片：双视角叙事。同一件真实经历，用户与菟菚各自保留一段解释，
-- 并存不合并；菟菚视角默认由 LLM 基于锚点真实记录生成草稿、经用户确认落库，
-- 也可由用户代填（origin 标记来源）。锚点可空（自由主题）；有锚点时取其
-- 真实记录作生成素材并用于展示跳转，锚点 id 属展示性快照，不参与导出重映射。
CREATE TABLE IF NOT EXISTS dual_perspectives (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            TEXT NOT NULL,
    title              TEXT NOT NULL,               -- 经历标题（用户起名）
    source_type        TEXT,                        -- event / diary / activity / artifact / free
    source_id          INTEGER,                     -- 锚点 id（free 为 NULL）
    source_date        TEXT,                        -- 锚点日期快照（展示用）
    source_label       TEXT NOT NULL DEFAULT '',    -- 锚点摘要快照（展示与取材说明）
    user_view          TEXT NOT NULL DEFAULT '',
    tuzhan_view        TEXT NOT NULL DEFAULT '',
    tuzhan_view_origin TEXT NOT NULL DEFAULT 'user',  -- llm / user
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dual_perspectives_user
    ON dual_perspectives(user_id, id);
-- M8.7「不同版本的我们」：用户显式创建的关系版本检查点。snapshot_json 只保存
-- 当时的结构化状态、行为帧白名单与记录计数（format_version=1），绝不保存消息
-- 原文、事实正文、事件 payload 或日记/产物内容；一经创建不可修改，只可删除。
CREATE TABLE IF NOT EXISTS relationship_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    label TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_relationship_versions_user
    ON relationship_versions(user_id, captured_at, id);
-- M5 未完成心事：想问但时机不对、想确认的事；只能携带叙事素材，不能带可执行指令。
CREATE TABLE IF NOT EXISTS pending_thoughts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        TEXT NOT NULL,
    kind           TEXT NOT NULL,     -- resume_reading / confirm_memory
    source_type    TEXT NOT NULL,     -- activity / fact
    source_id      INTEGER NOT NULL,
    content        TEXT NOT NULL,     -- 叙事素材（她惦记的事，自然语言）
    earliest_at    TEXT NOT NULL,     -- 最早可表达时间（Narrative Planner 门控）
    expires_at     TEXT,              -- 过期即放弃
    priority       INTEGER NOT NULL DEFAULT 5,  -- 1-9，小=优先
    max_attempts   INTEGER NOT NULL DEFAULT 2,
    attempts       INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',  -- pending / expressed / dismissed
    created_at     TEXT NOT NULL,
    UNIQUE(user_id, kind, source_id)
);
CREATE INDEX IF NOT EXISTS idx_pending_thoughts_user
    ON pending_thoughts(user_id, status, earliest_at);
-- M2 前置最小版：可保存的共同产物（首批：共读共同书摘）。
CREATE TABLE IF NOT EXISTS artifacts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL,
    artifact_type TEXT NOT NULL,     -- 首批：book_summary
    source_type   TEXT NOT NULL,
    source_id     INTEGER NOT NULL,
    title         TEXT NOT NULL,
    content       TEXT NOT NULL,
    version       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    UNIQUE (user_id, artifact_type, source_id)
);
-- C4 好感度玩法闭环：解锁时刻（阈值跨越/彩蛋）队列与收集
CREATE TABLE IF NOT EXISTS unlocks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    key          TEXT NOT NULL,     -- UNLOCK_DEFS.key（如 stage_lover / easter_streak7）
    kind         TEXT NOT NULL,     -- stage / bond / easter
    title        TEXT NOT NULL,
    anchors      TEXT NOT NULL,     -- JSON 数组：内容锚点（注入 LLM 的要点）
    enqueued_at  TEXT NOT NULL,
    delivered_at TEXT,              -- NULL = 待说出口（pending）
    content      TEXT,              -- 说出口那轮她说的话（摘要）
    UNIQUE (user_id, key)           -- 同 key 一生只解锁一次
);
CREATE INDEX IF NOT EXISTS idx_unlocks_user ON unlocks(user_id, delivered_at);
-- P1-02 语境注册表生命周期：按成功提交的 turn 记 sticky/cooldown（运行态，
-- 不导出；user_id 已含人格 scope，天然按人格隔离）。
CREATE TABLE IF NOT EXISTS context_lifecycle (
    user_id            TEXT NOT NULL,
    entry_id           TEXT NOT NULL,
    last_committed_turn INTEGER NOT NULL,
    sticky_until_turn  INTEGER NOT NULL,
    cooldown_until_turn INTEGER NOT NULL,
    updated_at         TEXT NOT NULL,
    PRIMARY KEY (user_id, entry_id)
);
-- P1-04 角色虚构生活事件：结构化、确定性生成（character_fiction 命名空间，
-- 与双方真实关系事件严格分离）。唯一 (user_id, block_id, occurrence, kind)。
CREATE TABLE IF NOT EXISTS character_life_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    block_id TEXT NOT NULL,
    occurrence TEXT NOT NULL,          -- 发生粒度锚（本地日期或 UTC 周期键）
    kind TEXT NOT NULL,                -- daily_life / block_change
    payload_json TEXT NOT NULL,
    namespace TEXT NOT NULL DEFAULT 'character_fiction',
    occurred_at TEXT NOT NULL,         -- 事件发生在哪个时刻（补算保留真实时刻）
    computed_at TEXT NOT NULL,         -- 实际计算时刻（迟到补算不冒充在线观察）
    UNIQUE (user_id, block_id, occurrence, kind)
);
CREATE INDEX IF NOT EXISTS idx_life_events_user ON character_life_events(user_id, occurred_at);
-- P1-04 后台任务持久认领（JOB-1）：scope/job/period 唯一，租约 CAS 防跨进程
-- 双跑；运行态记录，不随 E03 导出，reset 清理。
CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_key TEXT NOT NULL,
    job_key TEXT NOT NULL,
    period_start TEXT NOT NULL,        -- UTC 周期键（整点 ISO）
    status TEXT NOT NULL,              -- pending/running/succeeded/failed/cancelled
    lease_owner TEXT,
    lease_until TEXT,
    attempt INTEGER NOT NULL DEFAULT 0,
    next_retry TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (scope_key, job_key, period_start)
);
-- P2-01 关系二维事件账本：唯一 (user_id, event_id, rule_id)，幂等入账，
-- reverted_at 标记撤销；进入 E03 关系状态类别与 reset 清单。
CREATE TABLE IF NOT EXISTS relationship_dimension_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    rule_id TEXT NOT NULL,
    trust_delta INTEGER NOT NULL DEFAULT 0,
    intimacy_delta INTEGER NOT NULL DEFAULT 0,
    occurred_at TEXT NOT NULL,
    reverted_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_ledger_unique
    ON relationship_dimension_ledger(user_id, event_id, rule_id);
-- L03 关系气质证据：唯一 user/event/style；只吃明确事件，90 天窗+30 天半衰期
-- 由 derive_style 现算，不存派生分数。reset 清空，LC-1 长期关系类别导出。
CREATE TABLE IF NOT EXISTS relationship_style_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    style TEXT NOT NULL,               -- companion/playful/confidant/growth/romantic
    weight REAL NOT NULL DEFAULT 1.0,
    occurred_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_style_evidence_unique
    ON relationship_style_evidence(user_id, event_id, style);
CREATE INDEX IF NOT EXISTS idx_style_evidence_user
    ON relationship_style_evidence(user_id, occurred_at);
-- G01 记忆显著度：policy 一条事实一条（shadow 记录分层，不改写 facts）；
-- annotations 她的事实视角侧表；first_occurrences 初历标记（topic_key 规范化唯一）。
-- 三表全部 reset 清空 + LC-1 记忆类别导出。
CREATE TABLE IF NOT EXISTS memory_policy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    fact_id INTEGER NOT NULL,
    tier TEXT NOT NULL DEFAULT 'short',   -- short / long / legacy
    score INTEGER NOT NULL DEFAULT 0,
    score_version INTEGER NOT NULL DEFAULT 1,
    explicit_importance INTEGER NOT NULL DEFAULT 0,
    relationship_anchor INTEGER NOT NULL DEFAULT 0,
    distinct_days INTEGER NOT NULL DEFAULT 0,
    first_event INTEGER NOT NULL DEFAULT 0,
    legacy INTEGER NOT NULL DEFAULT 0,
    source_message_ids TEXT NOT NULL DEFAULT '[]',
    first_observed_at TEXT,
    review_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_policy_unique
    ON memory_policy(user_id, fact_id);
CREATE TABLE IF NOT EXISTS memory_annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    fact_id INTEGER NOT NULL,
    role TEXT NOT NULL DEFAULT 'assistant',
    emotion TEXT NOT NULL DEFAULT '',
    viewpoint TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'observed',   -- observed / user_teaching / inference
    confidence REAL NOT NULL DEFAULT 0.7,
    source_event_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_memory_annotations_fact
    ON memory_annotations(user_id, fact_id);
CREATE TABLE IF NOT EXISTS first_occurrences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    topic_key TEXT NOT NULL,
    source_event_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, event_type, topic_key)
);
-- F01 问候变体冷却（runtime，不导出）：同一变体 7 天冷却，记录上次使用的
-- 素材来源 id 便于审计；变体资源本身在人格切片目录，不在库里。
CREATE TABLE IF NOT EXISTS greeting_variant_usage (
    user_id        TEXT NOT NULL,
    variant_id     TEXT NOT NULL,
    last_used_at   TEXT NOT NULL,
    last_source_id TEXT,
    PRIMARY KEY (user_id, variant_id)
);
-- G03 悬念/开放问题：用户明确要求「有结果告诉我」的事；7 天到期、最多 2 次
-- 复查、间隔 24h；last_evidence_hash 只存证据条目规范化哈希，不存模型叙述。
CREATE TABLE IF NOT EXISTS open_questions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            TEXT NOT NULL,
    source_message_id  INTEGER,
    topic              TEXT NOT NULL,
    topic_key          TEXT NOT NULL,           -- 规范化主题（同主题幂等）
    status             TEXT NOT NULL DEFAULT 'open',  -- open/researching/resolved/dismissed/expired
    next_check_at      TEXT,
    expires_at         TEXT NOT NULL,
    attempts           INTEGER NOT NULL DEFAULT 0,
    last_evidence_hash TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_open_questions_user
    ON open_questions(user_id, status, next_check_at);
-- G04 她的求助/愿望：亲密与信任双门槛达标时，她偶尔请对方帮个小忙
-- （挑歌/挑书）。候选经主动仲裁投递，24h 未回应过期不再问；接受只写
-- 角色虚构产物，与现实承诺账分离。
CREATE TABLE IF NOT EXISTS companion_requests (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             TEXT NOT NULL,
    life_event_id       INTEGER,
    kind                TEXT NOT NULL,           -- song_choice / book_choice
    status              TEXT NOT NULL DEFAULT 'candidate',  -- candidate/offered/accepted/declined/expired
    offered_at          TEXT,
    expires_at          TEXT,
    response_message_id INTEGER,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_companion_requests_user
    ON companion_requests(user_id, status);
-- F03 心事注入回执（runtime，不导出）：记录某轮把哪条心事作为语境选中
-- （selected）或确认被采用（committed）；唯一三元组，不存模型全文。
CREATE TABLE IF NOT EXISTS thought_context_receipts (
    user_id    TEXT NOT NULL,
    thought_id INTEGER NOT NULL,
    turn_id    INTEGER NOT NULL,
    status     TEXT NOT NULL DEFAULT 'selected',  -- selected / committed
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, thought_id, turn_id)
);
-- L04 幽默记忆（runtime，不导出）：逐次使用/反馈明细。状态由明确反馈推导
-- （7 天内 ≥2 次 positive → approved；负反馈优先 → retired），有效偏好仍由
-- user_terms 承载并随关系包导出，本表只做冷却与授权判定。
CREATE TABLE IF NOT EXISTS humor_usage (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        TEXT NOT NULL,
    term_id        INTEGER NOT NULL,
    source_turn_id INTEGER,
    reaction       TEXT NOT NULL DEFAULT 'unknown',  -- positive / negative / unknown
    last_used_at   TEXT,
    blocked_until  TEXT,
    status         TEXT NOT NULL DEFAULT 'candidate',  -- candidate / approved / retired
    created_at     TEXT NOT NULL,
    UNIQUE (user_id, term_id, source_turn_id)
);
CREATE INDEX IF NOT EXISTS idx_humor_usage_user
    ON humor_usage(user_id, term_id);
-- F02 素材互通（LC-1）：产物 → 真实来源的引用边。方向明确、无限递归禁止；
-- 源消失即删边，下游不可再引用。owner_type/source_type 均为封闭白名单。
CREATE TABLE IF NOT EXISTS source_links (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        TEXT NOT NULL,
    owner_type     TEXT NOT NULL,   -- diary / research / promise / artifact
    owner_id       INTEGER NOT NULL,
    source_type    TEXT NOT NULL,   -- event / activity / fact / knowledge / character_life
    source_id      INTEGER NOT NULL,
    source_version TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    UNIQUE (user_id, owner_type, owner_id, source_type, source_id)
);
CREATE INDEX IF NOT EXISTS idx_source_links_owner
    ON source_links(user_id, owner_type, owner_id);
CREATE INDEX IF NOT EXISTS idx_source_links_source
    ON source_links(user_id, source_type, source_id);
-- F04 专注收尾投递箱：完成事件先写，收尾消息在同事务入箱，后台重试 ≤2 次，
-- delivery_id 去重；用户发起的活动闭环，不占每日主动额度（语义保留）。
CREATE TABLE IF NOT EXISTS wrapup_outbox (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        TEXT NOT NULL,
    activity_id    INTEGER NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',  -- pending / sent / failed / cancelled
    candidate_text TEXT NOT NULL DEFAULT '',
    attempt        INTEGER NOT NULL DEFAULT 0,
    next_retry     TEXT,
    delivery_id    TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE (user_id, activity_id)
);
-- F05 共读方案 C：阅读地图与书签解锁。segments 只建空框架（按文档文本稳定
-- 切分），bookmarks 由用户确认后才算数；finish 是唯一解锁事件。
CREATE TABLE IF NOT EXISTS reading_segments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        TEXT NOT NULL,
    activity_id    INTEGER NOT NULL,
    segment_index  INTEGER NOT NULL,
    source_start   INTEGER NOT NULL DEFAULT 0,
    source_end     INTEGER NOT NULL DEFAULT 0,
    source_hash    TEXT NOT NULL DEFAULT '',
    title          TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'locked',  -- locked/current/read/legacy_position
    completed_at   TEXT,
    UNIQUE (user_id, activity_id, segment_index)
);
CREATE TABLE IF NOT EXISTS reading_bookmarks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        TEXT NOT NULL,
    segment_id     INTEGER NOT NULL,
    origin         TEXT NOT NULL DEFAULT 'user',    -- user / tuzhan_draft
    user_view      TEXT NOT NULL DEFAULT '',
    tuzhan_view    TEXT NOT NULL DEFAULT '',
    summary        TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'empty',   -- empty / draft / confirmed
    source_version TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE (user_id, segment_id)
);
-- F06 活动草稿确认回执（runtime，不导出）：幂等键 user/draft_id → activity_id；
-- 同一草稿二次确认返回同一活动，不重复创建。
CREATE TABLE IF NOT EXISTS activity_draft_receipts (
    user_id     TEXT NOT NULL,
    draft_id    TEXT NOT NULL,
    activity_id INTEGER NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, draft_id)
);
-- L01 文档摄入：结构化阅读段（独立于检索 chunks）与导入任务。
CREATE TABLE IF NOT EXISTS document_segments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL,
    document_id   INTEGER NOT NULL,
    segment_index INTEGER NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    text_start    INTEGER NOT NULL DEFAULT 0,
    text_end      INTEGER NOT NULL DEFAULT 0,
    content_hash  TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    UNIQUE (user_id, document_id, segment_index)
);
CREATE TABLE IF NOT EXISTS document_import_jobs (
    job_id     TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    kind       TEXT NOT NULL,            -- url / upload
    source     TEXT NOT NULL DEFAULT '', -- 规范化 URL 或文件名（不含正文）
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending/running/succeeded/failed/cancelled
    document_id INTEGER,
    error      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- L02 观察日志：她/用户对真实生活的观察条目（来源可追溯，禁止无源补成纪实）
CREATE TABLE IF NOT EXISTS observation_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    activity_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    observer    TEXT NOT NULL DEFAULT 'user',   -- user / assistant
    content     TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT '',       -- event/activity/fact/character_life
    source_id   INTEGER,
    confidence  REAL NOT NULL DEFAULT 1.0,
    created_at  TEXT NOT NULL
);
-- L05 领域信任：五域事件账与派生快照（与 P2-01 共用唯一 reducer，禁止双算）
CREATE TABLE IF NOT EXISTS domain_trust_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    event_id     INTEGER NOT NULL,
    domain       TEXT NOT NULL,
    delta        INTEGER NOT NULL DEFAULT 0,
    rule_version INTEGER NOT NULL DEFAULT 1,
    occurred_at  TEXT NOT NULL,
    reverted_at  TEXT,
    UNIQUE (user_id, event_id, domain)
);
CREATE TABLE IF NOT EXISTS domain_trust_snapshot (
    user_id    TEXT NOT NULL,
    domain     TEXT NOT NULL,
    value      INTEGER NOT NULL DEFAULT 0,
    version    INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, domain)
);
-- P2-02 用户偏好教学：四类封闭类别，候选→确认→撤销状态机；
-- origin=legacy 的行由旧称呼/提醒配置一次性迁移生成。
CREATE TABLE IF NOT EXISTS user_preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    category TEXT NOT NULL,        -- comfort / address / reminder / humor / style
    value_json TEXT NOT NULL,
    origin TEXT NOT NULL,          -- user_teaching / legacy / observed
    source_message_id INTEGER,
    confidence REAL NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'candidate',  -- candidate / active / revoked
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revoked_at TEXT,
    version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_user_prefs ON user_preferences(user_id, category, status);
-- P2-04 链式反应实例：最小引擎（明确约定→一次跟进→完成后正向回望收束）。
-- 唯一 (user_id, source_event_id, rule_id)；运行态，reset 清理。
CREATE TABLE IF NOT EXISTS event_chains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    source_event_id INTEGER NOT NULL,
    rule_id TEXT NOT NULL,
    rule_version INTEGER NOT NULL DEFAULT 1,
    node TEXT NOT NULL,
    status TEXT NOT NULL,            -- waiting / done / closed / expired / cancelled
    due_at TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    result_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (user_id, source_event_id, rule_id)
);
-- P2-06 久别重逢三段式：只引用已完成的角色生活事件，不复制叙事原文。
CREATE TABLE IF NOT EXISTS reunion_arcs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    absence_bucket TEXT NOT NULL,
    source_snapshot_id INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending', -- pending/offered/responded/closed/expired
    narrative_version INTEGER NOT NULL DEFAULT 1,
    offered_message_id INTEGER,
    response_message_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    UNIQUE (user_id, source_snapshot_id)
);
CREATE INDEX IF NOT EXISTS idx_reunion_arcs_user
    ON reunion_arcs(user_id, state, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id, status);
CREATE INDEX IF NOT EXISTS idx_triples_user ON triples(user_id);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, id);
CREATE INDEX IF NOT EXISTS idx_long_memory_user ON long_memory(user_id, id);
CREATE INDEX IF NOT EXISTS idx_facts_user ON facts(user_id, id);
CREATE INDEX IF NOT EXISTS idx_dates_user ON important_dates(user_id);
CREATE INDEX IF NOT EXISTS idx_promises_user ON promises(user_id, status);
CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_log(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_affection_log_user_ts ON affection_log(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_mood_log_user_ts ON mood_log(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_stickers_user ON stickers(user_id);
CREATE INDEX IF NOT EXISTS idx_profile_user ON user_profile(user_id);
CREATE INDEX IF NOT EXISTS idx_terms_user ON user_terms(user_id);
CREATE INDEX IF NOT EXISTS idx_style_map_user ON user_style_map(user_id);
"""


def _enable_wal(conn: sqlite3.Connection) -> None:
    """并发冷启动时等待另一个进程完成 WAL 切换。"""
    for attempt in range(20):
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 19:
                raise
            time.sleep(0.05 * (attempt + 1))


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
        path = config.data_dir / "bot.db"
        create_pre_upgrade_backup(path, config.data_dir / "backups", _SCHEMA_VERSION)
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 5000")
        _enable_wal(self.conn)
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.executescript(_SCHEMA)
        self.conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS room_artifact_deleted AFTER DELETE ON artifacts BEGIN
          DELETE FROM artifact_placements WHERE user_id=OLD.user_id AND artifact_id=OLD.id;
          DELETE FROM aesthetic_preferences WHERE user_id=OLD.user_id AND source_type='artifact' AND source_id=OLD.id;
        END;
        CREATE TRIGGER IF NOT EXISTS room_event_deleted AFTER DELETE ON relationship_events BEGIN
          DELETE FROM artifacts WHERE user_id=OLD.user_id AND artifact_type='relationship_object' AND source_type='relationship_event' AND source_id=OLD.id;
        END;
        CREATE TRIGGER IF NOT EXISTS room_event_invalidated AFTER UPDATE OF status ON relationship_events
        WHEN NEW.status!='active' BEGIN
          DELETE FROM artifacts WHERE user_id=NEW.user_id AND artifact_type='relationship_object' AND source_type='relationship_event' AND source_id=NEW.id;
        END;
        CREATE TRIGGER IF NOT EXISTS aesthetic_opinion_deleted AFTER DELETE ON knowledge_opinions BEGIN
          DELETE FROM aesthetic_preferences WHERE user_id=OLD.user_id AND source_type='knowledge_opinion' AND source_id=OLD.id;
        END;
        """)
        # L01：kb_documents 增加来源与解析版本列（旧库 ALTER 补齐）
        # L02：共创壳增加 subtype 与结构化大纲列（旧库 ALTER 补齐）
        writing_columns = {
            row[1] for row in self.conn.execute("PRAGMA table_info(activity_writings)")
        }
        for column, definition in (
            ("subtype", "TEXT NOT NULL DEFAULT 'story'"),
            ("structured_outline_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("outline_version", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column not in writing_columns:
                self.conn.execute(
                    f"ALTER TABLE activity_writings ADD COLUMN {column} {definition}")
        kb_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(kb_documents)")}
        for column, definition in (
            ("source_url", "TEXT"),
            ("source_hash", "TEXT"),
            ("parser_version", "TEXT"),
        ):
            if column not in kb_columns:
                self.conn.execute(f"ALTER TABLE kb_documents ADD COLUMN {column} {definition}")
        policy_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(memory_policy)")}
        for column, definition in (
            ("source_message_ids", "TEXT NOT NULL DEFAULT '[]'"),
            ("first_observed_at", "TEXT"),
        ):
            if column not in policy_columns:
                self.conn.execute(f"ALTER TABLE memory_policy ADD COLUMN {column} {definition}")
        # 兼容旧库：补上 style_profile 列
        try:
            self.conn.execute("ALTER TABLE users ADD COLUMN style_profile TEXT")
        except sqlite3.OperationalError:
            pass
        # P1-05：important_dates 补 namespace 列（角色纪念日/用户真实日子分流）
        try:
            self.conn.execute(
                "ALTER TABLE important_dates ADD COLUMN namespace TEXT NOT NULL DEFAULT 'user_real'"
            )
        except sqlite3.OperationalError:
            pass
        # P2-03：promises 补对称约定列；旧 pending 状态统一迁移为 open
        for col_decl in (
            "owner TEXT NOT NULL DEFAULT 'user'",
            "due_at TEXT",
            "action_kind TEXT",
            "namespace TEXT NOT NULL DEFAULT 'user_real'",
            "source_message_id INTEGER",
            "promise_hash TEXT",
        ):
            try:
                self.conn.execute(f"ALTER TABLE promises ADD COLUMN {col_decl}")
            except sqlite3.OperationalError:
                pass
        self.conn.execute("UPDATE promises SET status='open' WHERE status='pending'")
        # P2-01：users 补 trust/intimacy 两列（NULL=未迁移，首次回填=旧 affection）
        for col in ("trust", "intimacy"):
            try:
                self.conn.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER")
            except sqlite3.OperationalError:
                pass
        # P2-01 首次迁移：仅回填 NULL（判据=trust IS NULL），二次运行零副作用
        self.conn.execute(
            "UPDATE users SET trust = affection, intimacy = affection WHERE trust IS NULL"
        )
        # 兼容旧库：long_memory 补 pinned 列（用户显式要求记住的记忆不被轮转清理）
        try:
            self.conn.execute(
                "ALTER TABLE long_memory ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"
            )
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
        # C5 养成仪表盘：旧 affection_log 补绝对值列，便于准确画历史曲线。
        try:
            self.conn.execute("ALTER TABLE affection_log ADD COLUMN value INTEGER")
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
        # v2 记忆溯源：旧事实保守标为 legacy/中等置信度，保持原有召回行为。
        fact_columns = (
            ("source_type", "TEXT NOT NULL DEFAULT 'legacy'"),
            ("source_message_ids", "TEXT NOT NULL DEFAULT '[]'"),
            ("confidence", "REAL NOT NULL DEFAULT 0.5"),
            ("verified_at", "TEXT"),
            ("expires_at", "TEXT"),
            ("pinned", "INTEGER NOT NULL DEFAULT 0"),
            ("surface_policy", "TEXT NOT NULL DEFAULT 'normal'"),
            ("status", "TEXT NOT NULL DEFAULT 'active'"),
            ("conflicts_with_fact_id", "INTEGER"),
        )
        for column, definition in fact_columns:
            try:
                self.conn.execute(f"ALTER TABLE facts ADD COLUMN {column} {definition}")
            except sqlite3.OperationalError:
                pass
        # M3.2 专注陪伴：activities 复用为 focus 类型的计时字段（旧库迁移）。
        # 幂等：列已存在时 ALTER 报 duplicate column，按惯例静默跳过。
        for statement in (
            "ALTER TABLE activities ADD COLUMN planned_minutes INTEGER",
            "ALTER TABLE activities ADD COLUMN remaining_seconds INTEGER",
            "ALTER TABLE activities ADD COLUMN ends_at TEXT",
        ):
            try:
                self.conn.execute(statement)
            except sqlite3.OperationalError:
                pass
        # 用户身份统一迁移：历史版本聊天链路用 f"session_{session_id}"（单一会话
        # 下即 "session_current"），现统一为 "assistant-main"，与 agent 任务代理、
        # contextvar 默认值对齐。此处把旧的 session_current 数据合并进 assistant-main，
        # 避免菟菚「失忆」（好感度/心情/记忆/待办全部保留）。幂等：无旧数据时无副作用。
        self._migrate_legacy_user_identity()
        self._backfill_dashboard_history()
        mark_schema_current(self.conn, _SCHEMA_VERSION)
        self.conn.commit()

    def _backfill_dashboard_history(self) -> None:
        """为升级前数据补齐可画曲线的绝对值，并给现有用户留一枚心情锚点。"""
        affection_by_user: dict[str, int] = {}
        rows = self.conn.execute(
            "SELECT id, user_id, delta, value FROM affection_log ORDER BY user_id, ts, id"
        ).fetchall()
        for row in rows:
            current = affection_by_user.get(row["user_id"], 0)
            if row["value"] is None:
                current = max(0, min(100, current + int(row["delta"])))
                self.conn.execute(
                    "UPDATE affection_log SET value = ? WHERE id = ?", (current, row["id"])
                )
            else:
                current = max(0, min(100, int(row["value"])))
            affection_by_user[row["user_id"]] = current

        now = datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO mood_log (user_id, value, ts) "
            "SELECT u.user_id, COALESCE(u.mood_value, 60), "
            "COALESCE(u.mood_updated_at, ?) FROM users u "
            "WHERE NOT EXISTS (SELECT 1 FROM mood_log m WHERE m.user_id = u.user_id)",
            (now,),
        )

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
            "mood_log", "stickers", "user_profile", "user_terms", "user_style_map", "triples", "tasks",
            "promises", "usage_log", "kb_documents", "kb_chunks", "activities", "activity_notes",
            "activity_goals", "goal_progress",
        ):
            self.conn.execute(
                f"UPDATE {table} SET user_id = ? WHERE user_id = ?", (target, legacy)
            )

    # ---- users ----
    @_locked
    def ensure_user(self, user_id: str):
        now = datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
        )
        self.conn.execute(
            "INSERT INTO mood_log (user_id, value, ts) "
            "SELECT ?, 60, ? WHERE NOT EXISTS "
            "(SELECT 1 FROM mood_log WHERE user_id = ?)",
            (user_id, now, user_id),
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
        """P2-01 起降级为 legacy 互动统计：只记 affection_log，不改写关系数值。

        两维（trust/intimacy）只经 apply_relationship_event（事件入账）与
        set_affection_absolute（调试入口）变化；旧频次奖励路径调用本方法
        仅留痕，不再刷分（14.10：普通聊天频次不直接刷两维）。
        """
        now = datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
        )
        row = self.conn.execute(
            "SELECT affection FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        self.conn.execute(
            "INSERT INTO affection_log (user_id, delta, reason, ts, value) VALUES (?, ?, ?, ?, ?)",
            (user_id, delta, f"[legacy] {reason}", now, int(row["affection"] or 0)),
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
        now = datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
        )
        self.conn.execute(
            "UPDATE users SET mood_value = ?, mood_updated_at = ? WHERE user_id = ?",
            (mood, now, user_id),
        )
        self.conn.execute(
            "INSERT INTO mood_log (user_id, value, ts) VALUES (?, ?, ?)",
            (user_id, mood, now),
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
        """手动设置好感度（0-100），用于调试/调节：两维同值，affection 同步 min。"""
        value = max(0, min(100, int(value)))
        self.ensure_user(user_id)
        cur = self.get_user(user_id)["affection"]
        self.conn.execute(
            "UPDATE users SET trust = ?, intimacy = ?, affection = ? WHERE user_id = ?",
            (value, value, value, user_id),
        )
        self.conn.execute(
            "INSERT INTO affection_log (user_id, delta, reason, ts, value) VALUES (?, ?, ?, ?, ?)",
            (user_id, value - cur, "手动设置（两维同值）",
             datetime.now().isoformat(timespec="seconds"), value),
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
    def add_message(self, user_id: str, role: str, content: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO messages (user_id, role, content, ts) VALUES (?, ?, ?, ?)",
            (user_id, role, content, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

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
    def add_long_memory(self, user_id: str, content: str, pinned: bool = False) -> int:
        """写入一条长期记忆。pinned=True 表示用户显式要求记住，不被容量清理截断。"""
        cur = self.conn.execute(
            "INSERT INTO long_memory (user_id, content, ts, pinned) VALUES (?, ?, ?, ?)",
            (user_id, content, datetime.now().isoformat(timespec="seconds"), 1 if pinned else 0),
        )
        self.conn.commit()
        return cur.lastrowid

    @_locked
    def prune_long_memory(self, user_id: str, keep: int = 800) -> list[int]:
        """长期记忆超过上限时删除最旧的记录，返回被删除的 id 列表。

        调用方拿到 id 后应同步清理对应的向量索引，避免 Chroma 里残留孤儿向量。
        长期记忆表按用户无限增长，每轮对话还会双写（用户说/菟菚说），
        这里把每用户记录数限制在 keep 条以内。
        pinned=1（用户显式要求记住的）永不删除，不计入 keep 配额。
        """
        keep_rows = self.conn.execute(
            "SELECT id FROM long_memory WHERE user_id=? AND pinned=0 ORDER BY id DESC LIMIT ?",
            (user_id, keep),
        ).fetchall()
        keep_ids = {r["id"] for r in keep_rows}
        all_rows = self.conn.execute(
            "SELECT id FROM long_memory WHERE user_id=? AND pinned=0", (user_id,)
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
    def add_fact(
        self,
        user_id: str,
        content: str,
        *,
        source_type: str = "conversation_inference",
        source_message_ids: str = "[]",
        confidence: float = 0.7,
        verified_at: str | None = None,
        expires_at: str | None = None,
        pinned: bool = False,
        surface_policy: str = "normal",
        conflicts_with_fact_id: int | None = None,
    ) -> int | None:
        """存一条事实；与已有事实二元组重叠≥50% 视为重复则跳过。

        返回新记录 id；重复/跳过返回 None。
        """
        content = content.strip()
        if not content:
            return None
        q = _bigrams(content)
        rows = self.conn.execute(
            "SELECT id, content FROM facts WHERE user_id = ? AND status = 'active' "
            "ORDER BY id DESC LIMIT 200",
            (user_id,),
        ).fetchall()
        conflict_target = None
        if conflicts_with_fact_id is not None:
            conflict_target = next(
                (r for r in rows if int(r["id"]) == int(conflicts_with_fact_id)), None
            )
        for r in rows:
            if r["content"].strip() == content:
                return None
            if conflict_target is not None:
                continue
            existing = _bigrams(r["content"])
            if q and existing:
                overlap = len(q & existing) / min(len(q), len(existing))
                if overlap >= 0.5:
                    return None
        confidence = max(0.0, min(1.0, float(confidence)))
        if surface_policy not in {"normal", "do_not_proactively_surface", "never_surface"}:
            surface_policy = "normal"
        status = "pending_confirmation" if conflict_target is not None else "active"
        cur = self.conn.execute(
            "INSERT INTO facts "
            "(user_id, content, ts, source_type, source_message_ids, confidence, verified_at, "
            "expires_at, pinned, surface_policy, status, conflicts_with_fact_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id, content, datetime.now().isoformat(timespec="seconds"), source_type,
                source_message_ids, confidence, verified_at, expires_at, int(pinned), surface_policy,
                status, int(conflict_target["id"]) if conflict_target is not None else None,
            ),
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
        now = datetime.now().isoformat(timespec="seconds")
        rows = self.conn.execute(
            "SELECT id, content FROM facts WHERE user_id = ? "
            "AND status = 'active' "
            "AND surface_policy != 'never_surface' "
            "AND (expires_at IS NULL OR expires_at > ?) ORDER BY id DESC LIMIT 500",
            (user_id, now),
        ).fetchall()
        min_overlap = 1 if len(q_bigrams) != 2 else 2
        allowed = self.recallable_fact_ids(user_id, [int(row["id"]) for row in rows])
        scored = []
        for r in rows:
            if int(r["id"]) not in allowed:
                continue
            content_bigrams = _bigrams(r["content"])
            overlap = len(q_bigrams & content_bigrams)
            if overlap >= min_overlap:
                scored.append((overlap, r["content"]))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"content": c} for _, c in scored[:top_k]]

    @_locked
    def fact_ids_by_content(self, user_id: str, contents: list[str]) -> dict[str, int]:
        """按正文反查 active 事实 id（F07 只给本轮实际引用的事实附生命周期元数据）。

        正文统一 strip 归一化：检索/向量返回的文本若与库中正文存在前后空白
        差异，反查仍能命中（同 user 下正文已由 add_fact 查重保证唯一）。
        """
        clean = [str(c).strip() for c in contents if str(c or "").strip()]
        if not clean:
            return {}
        placeholders = ",".join("?" for _ in clean)
        rows = self.conn.execute(
            f"SELECT id, content FROM facts WHERE user_id = ? AND status = 'active' "
            f"AND content IN ({placeholders})",
            (user_id, *clean),
        ).fetchall()
        return {str(r["content"]).strip(): int(r["id"]) for r in rows}

    @_locked
    def facts_not_for_proactive(self, user_id: str, limit: int = 50) -> list[str]:
        """返回用户明确要求不要在主动消息里提起的事实。"""
        now = datetime.now().isoformat(timespec="seconds")
        rows = self.conn.execute(
            "SELECT content FROM facts WHERE user_id = ? "
            "AND status = 'active' "
            "AND surface_policy = 'do_not_proactively_surface' "
            "AND (expires_at IS NULL OR expires_at > ?) ORDER BY id DESC LIMIT ?",
            (user_id, now, limit),
        ).fetchall()
        return [str(row["content"]) for row in rows]

    @_locked
    def recallable_fact_ids(self, user_id: str, fact_ids: list[int]) -> set[int]:
        """以 SQLite 为权威过滤向量命中，阻断已删/待确认/过期事实残留召回。"""
        clean_ids = sorted({int(value) for value in fact_ids})
        if not clean_ids:
            return set()
        placeholders = ",".join("?" for _ in clean_ids)
        now = datetime.now().isoformat(timespec="seconds")
        rows = self.conn.execute(
            f"SELECT id FROM facts WHERE user_id = ? AND id IN ({placeholders}) "
            "AND status = 'active' AND surface_policy != 'never_surface' "
            "AND (expires_at IS NULL OR expires_at > ?)",
            (user_id, *clean_ids, now),
        ).fetchall()
        from .memory_salience import policy_expired_ids

        return {int(row["id"]) for row in rows} - policy_expired_ids(user_id, database=self)

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

        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 5000")
        _enable_wal(self.conn)
        self.conn.execute("PRAGMA synchronous = NORMAL")
        if deleted:
            self.conn.executescript(_SCHEMA)
            mark_schema_current(self.conn, _SCHEMA_VERSION)
        else:
            # 文件删除失败（被占用）时退化的清空路径：覆盖全部业务表
            for table in (
                "affection_log", "long_memory", "facts", "user_meta", "messages",
                "users", "kv_store", "important_dates", "stickers",
                "user_profile", "user_terms", "user_style_map", "diary", "research_reports", "triples",
                "tasks", "promises", "usage_log", "activity_notes", "activities",
                "activity_viewpoints", "activity_goals", "goal_progress",
                "activity_writings", "writing_turns", "activity_lists", "list_items",
                "relationship_events", "artifacts", "context_lifecycle",
                "character_life_events", "job_runs",
                "relationship_dimension_ledger", "relationship_style_evidence",
                "memory_policy", "memory_annotations", "first_occurrences",
                "user_preferences", "event_chains", "reunion_arcs",
                "greeting_variant_usage", "open_questions", "companion_requests",
                "thought_context_receipts", "humor_usage", "source_links", "wrapup_outbox", "reading_segments", "reading_bookmarks", "activity_draft_receipts",
                "document_segments", "document_import_jobs", "observation_entries",
                "domain_trust_events", "domain_trust_snapshot",
                "aesthetic_preferences", "artifact_placements",
                "knowledge_opinion_sources", "knowledge_opinions",
                "pending_thoughts", "future_letters", "relationship_snapshots", "dual_perspectives",
                "relationship_versions",
                "kb_documents", "kb_chunks", "unlocks", "mood_log",
            ):
                self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()


def _bigrams(text: str) -> set[str]:
    text = text.strip()
    if len(text) < 2:
        return set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


# ---- important_dates（情感记忆：生日/纪念日/特殊日子）----


def save_important_date(user_id: str, date_str: str, label: str, kind: str = "other", year: int | None = None, namespace: str = "user_real") -> bool:
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
            "INSERT INTO important_dates (user_id, date, label, kind, year, ts, namespace) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, date_str, label, kind, year,
             datetime.now().isoformat(timespec="seconds"),
             namespace if namespace in ("user_real", "character_fiction") else "user_real"),
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


def normalize_promise_hash(content: str) -> str:
    """约定规范化哈希（13.2 固定顺序）：NFKC → lower → 去标点 → 压空白 → sha256。

    owner/due_at/action_kind 不参与（14.7）。
    """
    import hashlib
    import unicodedata

    text = unicodedata.normalize("NFKC", str(content or "")).lower()
    text = "".join(ch for ch in text if ch.isalnum() or ch.isspace())
    text = " ".join(text.split())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def save_promise(user_id: str, content: str, follow_up: str = "", source: str = "",
                 *, owner: str = "user", due_at: str | None = None,
                 action_kind: str | None = None, namespace: str = "user_real",
                 source_message_id: int | None = None) -> int | None:
    """保存一条约定。同用户存在相同 hash 的 open 约定时不重复插入（返回 None）。"""
    content = content.strip()[:100]
    if not content:
        return None
    if owner not in ("user", "assistant"):
        owner = "user"
    if namespace not in ("user_real", "character_fiction"):
        namespace = "user_real"
    follow_up = follow_up.strip()[:10]
    promise_hash = normalize_promise_hash(content)
    with db._lock:
        row = db.conn.execute(
            "SELECT id, follow_up FROM promises WHERE user_id = ? AND promise_hash = ? AND status = 'open'",
            (user_id, promise_hash),
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
            "INSERT INTO promises (user_id, content, follow_up, source, created_at, "
            "owner, due_at, action_kind, namespace, source_message_id, promise_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, content, follow_up, source[:200],
             datetime.now().isoformat(timespec="seconds"),
             owner, due_at, action_kind, namespace, source_message_id, promise_hash),
        )
        db.conn.commit()
        return int(cur.lastrowid)


def get_due_promises(user_id: str, day: date) -> list[dict]:
    """到点该跟进的约定：pending 且 follow_up 已到期（≤ day）。"""
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM promises WHERE user_id = ? AND status = 'open' "
            "AND follow_up != '' AND follow_up <= ? ORDER BY follow_up",
            (user_id, day.isoformat()),
        ).fetchall()
    return [dict(r) for r in rows]


def get_open_promises(user_id: str, limit: int = 5) -> list[dict]:
    """所有未完成的约定（不限日期，供对话内自然提起）。

    读取前惰性推进到期状态机（P2-03）：due_at 已过的 open 约定标记 expired。
    """
    expire_due_promises(user_id, date.today().isoformat())
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM promises WHERE user_id = ? AND status = 'open' "
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
    # P2-03：关系入账按 promise id 幂等（ledger 唯一键防重复加分）。事件记录
    # 由调用方负责（initiative 走 record_promise_completed，单一事件类型）。
    try:
        row = db.conn.execute(
            "SELECT user_id, owner FROM promises WHERE id = ?", (promise_id,)
        ).fetchone()
        if row is not None and row["owner"] == "user":
            from .affection import apply_relationship_event

            apply_relationship_event(row["user_id"], int(promise_id), "promise_confirmed")
    except Exception:
        pass


def expire_due_promises(user_id: str, today: str) -> int:
    """到期状态机推进：due_at 已过且仍 open 的标记 expired。

    用户未做到不当失约（不扣分）；她自己的到期在对话里坦白并提供修复，
    这里只推进状态。due_at 为空的永不自动失约。
    """
    with db._lock:
        cur = db.conn.execute(
            "UPDATE promises SET status='expired', done_at=? "
            "WHERE user_id=? AND status='open' AND due_at IS NOT NULL AND due_at < ?",
            (datetime.now().isoformat(timespec="seconds"), user_id, today),
        )
        db.conn.commit()
    return int(cur.rowcount or 0)


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
            "SELECT f.id, f.content, f.ts, f.source_type, f.source_message_ids, f.confidence, "
            "f.verified_at, f.expires_at, f.pinned, f.surface_policy, f.status, "
            "f.conflicts_with_fact_id, old.content AS conflicting_content FROM facts AS f "
            "LEFT JOIN facts AS old ON old.id = f.conflicts_with_fact_id AND old.user_id = f.user_id "
            "WHERE f.user_id = ? AND f.status IN ('active', 'pending_confirmation') "
            "ORDER BY CASE f.status WHEN 'pending_confirmation' THEN 0 ELSE 1 END, f.id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_fact(user_id: str, fact_id: int) -> bool:
    """删除事实及其待确认冲突候选。向量清理由 fact_lifecycle 统一执行。"""
    return bool(delete_fact_cascade(user_id, fact_id))


def delete_fact_cascade(user_id: str, fact_id: int) -> list[int]:
    """事务内删除事实与依赖它的冲突候选，返回所有需清理索引的 id。"""
    with db._lock:
        rows = db.conn.execute(
            "SELECT id FROM facts WHERE user_id = ? "
            "AND (id = ? OR (conflicts_with_fact_id = ? AND status = 'pending_confirmation'))",
            (user_id, fact_id, fact_id),
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        try:
            db.conn.execute("BEGIN")
            db.conn.execute(
                f"DELETE FROM facts WHERE user_id = ? AND id IN ({placeholders})",
                (user_id, *ids),
            )
            for table in ("memory_policy", "memory_annotations"):
                db.conn.execute(
                    f"DELETE FROM {table} WHERE user_id = ? AND fact_id IN ({placeholders})",
                    (user_id, *ids),
                )
            db.conn.commit()
        except Exception:
            db.conn.rollback()
            raise
        return ids


def update_fact(user_id: str, fact_id: int, content: str) -> bool:
    """用户改写事实；同时把来源升级为已验证的一手纠正。"""
    content = content.strip()[:100]
    if not content:
        return False
    with db._lock:
        now = datetime.now().isoformat(timespec="seconds")
        try:
            db.conn.execute("BEGIN")
            cur = db.conn.execute(
                "UPDATE facts SET content = ?, source_type = 'user_correction', confidence = 1.0, "
                "verified_at = ? WHERE id = ? AND user_id = ? AND status = 'active'",
                (content, now, fact_id, user_id),
            )
            if cur.rowcount:
                db.conn.execute(
                    "UPDATE facts SET status = 'rejected', verified_at = ? WHERE user_id = ? "
                    "AND conflicts_with_fact_id = ? AND status = 'pending_confirmation'",
                    (now, user_id, fact_id),
                )
            db.conn.commit()
            return cur.rowcount > 0
        except Exception:
            db.conn.rollback()
            raise


def update_fact_surface_policy(user_id: str, fact_id: int, surface_policy: str) -> bool:
    """修改事实的呈现策略；当前管理页支持 normal / 不主动提起。"""
    if surface_policy not in {"normal", "do_not_proactively_surface", "never_surface"}:
        return False
    with db._lock:
        cur = db.conn.execute(
            "UPDATE facts SET surface_policy = ? WHERE id = ? AND user_id = ?",
            (surface_policy, fact_id, user_id),
        )
        db.conn.commit()
        return cur.rowcount > 0


def update_fact_pinned(user_id: str, fact_id: int, pinned: bool) -> bool:
    """固定或取消固定一条有效事实；固定事实不会被自然衰减清理。"""
    with db._lock:
        cur = db.conn.execute(
            "UPDATE facts SET pinned = ? WHERE id = ? AND user_id = ? AND status = 'active'",
            (1 if pinned else 0, fact_id, user_id),
        )
        db.conn.commit()
        return cur.rowcount > 0


def resolve_fact_conflict(user_id: str, fact_id: int, accept_new: bool) -> dict | None:
    """确认一个冲突候选；事务内二选一，返回供向量索引同步的事实信息。"""
    with db._lock:
        candidate = db.conn.execute(
            "SELECT id, content, conflicts_with_fact_id FROM facts "
            "WHERE id = ? AND user_id = ? AND status = 'pending_confirmation'",
            (fact_id, user_id),
        ).fetchone()
        if candidate is None:
            return None
        old_id = candidate["conflicts_with_fact_id"]
        now = datetime.now().isoformat(timespec="seconds")
        try:
            db.conn.execute("BEGIN")
            if accept_new:
                if old_id is not None:
                    db.conn.execute(
                        "UPDATE facts SET status = 'superseded' "
                        "WHERE id = ? AND user_id = ? AND status = 'active'",
                        (old_id, user_id),
                    )
                db.conn.execute(
                    "UPDATE facts SET status = 'active', source_type = 'user_confirmation', "
                    "confidence = 1.0, verified_at = ? WHERE id = ? AND user_id = ?",
                    (now, fact_id, user_id),
                )
            else:
                db.conn.execute(
                    "UPDATE facts SET status = 'rejected', verified_at = ? "
                    "WHERE id = ? AND user_id = ?",
                    (now, fact_id, user_id),
                )
            db.conn.commit()
        except Exception:
            db.conn.rollback()
            raise
        return {
            "fact_id": int(fact_id),
            "content": str(candidate["content"]),
            "old_fact_id": int(old_id) if old_id is not None else None,
            "accepted": bool(accept_new),
        }


# ---- usage_log（D5 成本面板：token 用量记录与聚合）----


def log_usage(user_id: str, channel: str, model: str,
              prompt_tokens: int, completion_tokens: int, estimated: bool = False) -> None:
    """记录一次 LLM 调用的 token 用量（单条插入，调用方已兜底异常）。"""
    with db._lock:
        db.conn.execute(
            "INSERT INTO usage_log (user_id, channel, model, prompt_tokens, completion_tokens, estimated, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, channel, model, prompt_tokens, completion_tokens,
             1 if estimated else 0, datetime.now().isoformat(timespec="seconds")),
        )
        db.conn.commit()


def usage_summary(user_id: str, days: int = 7) -> dict:
    """聚合用量：今天 / 近 N 天总量 + 近 N 天按 channel 分组。"""
    today = date.today().isoformat()
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    with db._lock:
        def _sum(where: str, args: tuple) -> dict:
            row = db.conn.execute(
                f"SELECT COALESCE(SUM(prompt_tokens),0) p, COALESCE(SUM(completion_tokens),0) c, "
                f"COUNT(*) n, COALESCE(SUM(estimated),0) e FROM usage_log WHERE user_id = ? AND {where}",
                (user_id, *args),
            ).fetchone()
            return {"prompt": row["p"], "completion": row["c"], "calls": row["n"], "estimated": row["e"]}

        by_channel_rows = db.conn.execute(
            "SELECT channel, COALESCE(SUM(prompt_tokens),0) p, COALESCE(SUM(completion_tokens),0) c, "
            "COUNT(*) n FROM usage_log WHERE user_id = ? AND ts >= ? GROUP BY channel ORDER BY p + c DESC",
            (user_id, since),
        ).fetchall()
    return {
        "today": _sum("ts >= ?", (today,)),
        "period": _sum("ts >= ?", (since,)),
        "days": days,
        "by_channel": [
            {"channel": r["channel"], "prompt": r["p"], "completion": r["c"], "calls": r["n"]}
            for r in by_channel_rows
        ],
    }


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
