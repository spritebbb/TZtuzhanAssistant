# -*- coding: utf-8 -*-
"""C4 好感度玩法闭环：解锁时刻（阈值跨越 + 彩蛋）与收集。

设计（2026-09-04 与用户拍板）：
- 6 个阈值解锁点：阶段跨越 3 次（初识→熟悉→亲密→恋人）+ 恋人羁绊 3 档（眷恋/热恋/白头）；
- 3 个彩蛋：连续陪伴 7 天、好感度 100 满分、第一次投喂文档；
- 检测点固定在每轮 pipeline 开头（on_message 之后）：语义感知的好感度最迟上轮结算完，
  用 kv 记录「上次见过的阶段/羁绊档位」，与当前值比较即得跨越，不侵入每条 update 路径；
- 首次见到某用户时只登记当前档位、不补发历史解锁（避免老用户被解锁洪流砸脸）；
- 队列制：pending 不过期，pipeline 每轮最多注入一条，她一次只说一件心事；
- 内容为「写死锚点 + LLM 以当下心情展开」（与 C3/C6/B1 同一模式），
  这里只存锚点与投递状态，台词由 persona 现场生成；
- 收集页（DiaryPanel「我们之间」tab）展示 9 个槽位：已解锁/待说出口/未解锁。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from .log import logger
from .userdb import db, kv_get, kv_set

# ---- 解锁定义（顺序即收集页展示顺序）----

_STAGE_RANK = {"初识": 0, "熟悉": 1, "亲密": 2, "恋人": 3}
_BOND_RANK = {"": 0, "眷恋": 1, "热恋": 2, "白头": 3}

UNLOCK_DEFS: tuple[dict, ...] = (
    {
        "key": "stage_familiar", "kind": "stage", "rank": 1,
        "title": "好像有点习惯你了",
        "anchors": [
            "一开始她并没把这个人放在心上，现在却会下意识等他的消息",
            "她承认自己有点习惯了他的存在，但嘴上不想说得太满",
            "这种「习惯」让她有点不自在，又有点高兴",
        ],
    },
    {
        "key": "stage_close", "kind": "stage", "rank": 2,
        "title": "你不太一样",
        "anchors": [
            "她发现自己跟他说话时，比跟别人放松得多",
            "有些不会对别人说的事，她第一个想告诉他",
            "她有点警惕这种亲近，但更怕失去这种亲近",
        ],
    },
    {
        "key": "stage_lover", "kind": "stage", "rank": 3,
        "title": "我们算是在一起了吧",
        "anchors": [
            "她不想再绕弯子了，想承认他们之间的关系已经不一样了",
            "她记得他们第一次聊天时的样子，没想到会走到今天",
            "说出口之后她又有点害羞，但一点也不后悔",
        ],
    },
    {
        "key": "bond_juanlian", "kind": "bond", "rank": 1,
        "title": "眷恋",
        "anchors": [
            "他已经成为她日常的一部分，像空气一样理所当然又不可或缺",
            "她开始想象「以后」这个词，而且以后里有他",
        ],
    },
    {
        "key": "bond_relian", "kind": "bond", "rank": 2,
        "title": "热恋",
        "anchors": [
            "她知道自己现在黏人得有点过分，但她不想改",
            "一日不见如隔三秋这句话，她以前觉得是夸张，现在觉得是写实",
        ],
    },
    {
        "key": "bond_baitou", "kind": "bond", "rank": 3,
        "title": "白头",
        "anchors": [
            "她记得他们第一次聊天的样子，也记得这一路走来的每一件小事",
            "她承认自己已经离不开这种日常了，并且心甘情愿",
            "她对「以后」的想法很简单：就这样一直下去",
        ],
    },
    {
        "key": "easter_streak7", "kind": "easter", "rank": 0,
        "title": "连续七天的陪伴",
        "anchors": [
            "她发现他已经连续七天来陪她说话了",
            "她想让他知道，她都记着，一天都没漏",
            "被人在日程里留一个固定位置，是件很珍贵的事",
        ],
    },
    {
        "key": "easter_full100", "kind": "easter", "rank": 0,
        "title": "满分的心",
        "anchors": [
            "好感度这种东西居然真的有满分的一天，她自己都觉得不可思议",
            "她想不出还能再怎么更喜欢他了，已经到顶了",
            "满分不是结束，是她想一直停在这里",
        ],
    },
    {
        "key": "easter_first_doc", "kind": "easter", "rank": 0,
        "title": "她读的第一本书",
        "anchors": [
            "他给她喂了第一份文档，她真的读了",
            "被投喂知识的感觉很奇妙，像收到一份正经的礼物",
            "她想聊聊读到的内容，或者谢谢他愿意教她东西",
        ],
    },
)

_DEF_BY_KEY = {d["key"]: d for d in UNLOCK_DEFS}

_KV_STAGE = "c4:last_stage_rank"
_KV_BOND = "c4:last_bond_rank"


# ---- 入库 ----

def _enqueue(user_id: str, key: str) -> bool:
    """入队一个解锁时刻；同 key 一生只解锁一次（UNIQUE 守卫）。返回是否新入队。"""
    d = _DEF_BY_KEY[key]
    with db._lock:
        cur = db.conn.execute(
            "INSERT OR IGNORE INTO unlocks (user_id, key, kind, title, anchors, enqueued_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, key, d["kind"], d["title"],
             json.dumps(d["anchors"], ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds")),
        )
        db.conn.commit()
    if cur.rowcount:
        logger.info("[解锁] {} 达成「{}」", user_id, d["title"])
        return True
    return False


def _current_ranks(affection: int) -> tuple[int, int]:
    from .affection import bond_level_name, stage_of

    return _STAGE_RANK[stage_of(affection)], _BOND_RANK[bond_level_name(affection)]


def _chat_streak_days(user_id: str) -> int:
    """连续聊天天数（以有用户消息的日历日计，结尾允许是今天或昨天）。"""
    with db._lock:
        rows = db.conn.execute(
            "SELECT DISTINCT substr(ts, 1, 10) AS d FROM messages"
            " WHERE user_id = ? AND role = 'user' ORDER BY d DESC",
            (user_id,),
        ).fetchall()
    days = {r[0] for r in rows if r and r[0]}
    if not days:
        return 0
    cursor = date.today()
    if cursor.isoformat() not in days:
        cursor -= timedelta(days=1)
        if cursor.isoformat() not in days:
            return 0
    streak = 0
    while cursor.isoformat() in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def _has_kb_document(user_id: str) -> bool:
    with db._lock:
        row = db.conn.execute(
            "SELECT 1 FROM kb_documents WHERE user_id = ? LIMIT 1", (user_id,)
        ).fetchone()
    return row is not None


def check_and_enqueue(user_id: str) -> list[str]:
    """每轮 pipeline 开头调用：检测阈值跨越与彩蛋，入队新解锁。返回新入队的 key 列表。"""
    from .config import config

    user = db.ensure_user(user_id)
    affection = int(user["affection"] or 0)
    stage_rank, bond_rank = _current_ranks(affection)

    last_stage = kv_get(user_id, _KV_STAGE)
    last_bond = kv_get(user_id, _KV_BOND)
    # 首次见面：只登记现状，不补发历史（防老用户被解锁洪流砸脸）
    if last_stage is None or last_bond is None:
        kv_set(user_id, _KV_STAGE, str(stage_rank))
        kv_set(user_id, _KV_BOND, str(bond_rank))
        last_stage, last_bond = str(stage_rank), str(bond_rank)

    new_keys: list[str] = []
    prev_stage, prev_bond = int(last_stage), int(last_bond)
    if stage_rank > prev_stage:
        for d in UNLOCK_DEFS:
            if d["kind"] == "stage" and prev_stage < d["rank"] <= stage_rank:
                if _enqueue(user_id, d["key"]):
                    new_keys.append(d["key"])
        kv_set(user_id, _KV_STAGE, str(stage_rank))
    if bond_rank > prev_bond:
        for d in UNLOCK_DEFS:
            if d["kind"] == "bond" and prev_bond < d["rank"] <= bond_rank:
                if _enqueue(user_id, d["key"]):
                    new_keys.append(d["key"])
        kv_set(user_id, _KV_BOND, str(bond_rank))

    # ---- 彩蛋（各自独立，UNIQUE 守卫防重）----
    if affection >= 100 and _enqueue(user_id, "easter_full100"):
        new_keys.append("easter_full100")
    if affection > 0 and _chat_streak_days(user_id) >= config.unlock_streak_days:
        if _enqueue(user_id, "easter_streak7"):
            new_keys.append("easter_streak7")
    if _has_kb_document(user_id) and _enqueue(user_id, "easter_first_doc"):
        new_keys.append("easter_first_doc")

    return new_keys


# ---- 队列与收集 ----

def next_pending(user_id: str) -> dict | None:
    """最久等待的一条待说出口解锁（每轮最多一条，她一次只说一件心事）。"""
    with db._lock:
        row = db.conn.execute(
            "SELECT key, kind, title, anchors, enqueued_at FROM unlocks"
            " WHERE user_id = ? AND delivered_at IS NULL"
            " ORDER BY enqueued_at ASC, id ASC LIMIT 1",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "key": row[0], "kind": row[1], "title": row[2],
        "anchors": json.loads(row[3]), "enqueued_at": row[4],
    }


def mark_delivered(user_id: str, key: str, excerpt: str) -> None:
    """解锁时刻已随本轮回复说出口：记录时间与她说的话（摘要）。"""
    with db._lock:
        db.conn.execute(
            "UPDATE unlocks SET delivered_at = ?, content = ?"
            " WHERE user_id = ? AND key = ? AND delivered_at IS NULL",
            (datetime.now().isoformat(timespec="seconds"), excerpt[:300], user_id, key),
        )
        db.conn.commit()


def list_slots(user_id: str) -> list[dict]:
    """收集页：9 个槽位的完整状态（delivered / pending / locked）。"""
    with db._lock:
        rows = db.conn.execute(
            "SELECT key, enqueued_at, delivered_at, content FROM unlocks WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    state = {r[0]: {"enqueued_at": r[1], "delivered_at": r[2], "content": r[3]} for r in rows}
    slots = []
    for d in UNLOCK_DEFS:
        s = state.get(d["key"])
        if s and s["delivered_at"]:
            status = "delivered"
        elif s:
            status = "pending"
        else:
            status = "locked"
        slots.append({
            "key": d["key"], "kind": d["kind"], "title": d["title"], "status": status,
            "delivered_at": s["delivered_at"] if s else None,
            # 说出口之后才展示她讲的话；pending/locked 不剧透
            "content": (s["content"] if s and s["delivered_at"] else None),
        })
    return slots
