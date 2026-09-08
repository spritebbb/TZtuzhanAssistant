"""L07：有来源的审美选择和作品陈列。视觉词受白名单约束，事实库不回写。"""
from __future__ import annotations

import math
from datetime import datetime

from .userdb import db
from .features import flag

TOKENS = {
    "color": ["蓝色", "绿色", "暖色", "冷色", "黑白"],
    "style": ["水彩", "素描", "极简", "复古"],
    "motif": ["植物", "星空", "海洋", "几何"],
    "layout": ["留白", "对称", "层叠"],
}
SOURCES = {"artifact": "artifacts", "knowledge_opinion": "knowledge_opinions"}
OBJECTS = {"reading_finished": ("星图", "star"), "focus_finished": ("小盆栽", "plant"),
           "goal_completed": ("里程碑", "stone"), "story_finished": ("故事灯", "lamp"),
           "list_completed": ("收藏匣", "box")}


def _source_exists(uid: str, kind: str, sid: int) -> bool:
    table = SOURCES.get(kind)
    if table is None:
        return False
    row = db.conn.execute(f"SELECT * FROM {table} WHERE user_id=? AND id=? AND status='active'",
                          (uid, sid)).fetchone()
    if not row:
        return False
    if kind == "artifact" and row["artifact_type"] == "relationship_object":
        return _event_exists(uid, row["source_id"])
    if kind == "knowledge_opinion":
        return row["origin"] == "assistant" and bool(db.conn.execute("SELECT 1 FROM kb_documents WHERE user_id=? AND id=?",
                                    (uid, row["document_id"])).fetchone())
    return True


def _event_exists(uid: str, event_id: int) -> bool:
    return bool(db.conn.execute(
        "SELECT 1 FROM relationship_events e JOIN activities a ON a.id=e.source_id AND a.user_id=e.user_id "
        "WHERE e.user_id=? AND e.id=? AND e.status='active' AND e.source_type='activity' "
        "AND a.status='completed' AND (e.expires_at IS NULL OR e.expires_at>?)",
        (uid, event_id, datetime.now().isoformat(timespec="seconds"))).fetchone())


def preferences(uid: str) -> list[dict]:
    with db._lock:
        rows = db.conn.execute("SELECT * FROM aesthetic_preferences WHERE user_id=? AND status='active' ORDER BY id DESC", (uid,)).fetchall()
        # 导入的数据也必须经过同一白名单，不能把外部原文拼进生图提示。
        return [dict(r) for r in rows if r["value"] in TOKENS.get(r["category"], [])
                and ((r["owner"] == "user" and r["origin"] == "user_teaching")
                     or (r["owner"] == "assistant" and r["origin"] == "user_confirmed"
                         and _source_exists(uid, r["source_type"], r["source_id"])))]


def put_preference(uid: str, owner: str, category: str, value: str,
                   source_type: str | None = None, source_id: int | None = None) -> None:
    if owner not in {"user", "assistant"} or value not in TOKENS.get(category, []):
        raise ValueError("请选择支持的审美选项")
    if not flag("aesthetics_enabled"):
        raise ValueError("审美功能已关闭")
    with db._lock:
        if owner == "assistant" and (not source_id or not _source_exists(uid, source_type, source_id)):
            raise ValueError("她的偏好需要当前人格仍有效的作品或观点来源")
        if owner == "user":
            source_type, source_id = None, None
        db.conn.execute(
            "INSERT INTO aesthetic_preferences(user_id,owner,category,value,origin,source_type,source_id,status) "
            "VALUES (?,?,?,?,?,?,?,'active') ON CONFLICT(user_id,owner,category) DO UPDATE SET "
            "value=excluded.value,origin=excluded.origin,source_type=excluded.source_type,source_id=excluded.source_id,status='active'",
            (uid, owner, category, value, "user_teaching" if owner == "user" else "user_confirmed", source_type, source_id))
        db.conn.commit()


def remove_preference(uid: str, pref_id: int) -> bool:
    with db._lock:
        cur = db.conn.execute("DELETE FROM aesthetic_preferences WHERE user_id=? AND id=?", (uid, pref_id))
        db.conn.commit()
        return bool(cur.rowcount)


def resolve_aesthetics(uid: str, purpose: str, explicit_request: str = "") -> list[dict]:
    if purpose not in {"image", "room"} or not flag("aesthetics_enabled"):
        return []
    selected = {}
    for category, words in TOKENS.items():
        for word in words:
            if word in explicit_request:
                # 即使是“不要蓝色”也阻止该类别的持久偏好覆盖本轮原话。
                selected[category] = {"token": word, "category": category, "source_id": None, "origin": "request"}
                break
    rows = preferences(uid)
    for owner in ("user", "assistant"):
        for row in sorted(rows, key=lambda r: list(TOKENS).index(r["category"])):
            if row["owner"] == owner and row["category"] not in selected:
                selected[row["category"]] = {"token": row["value"], "category": row["category"], "source_id": row["id"], "origin": owner}
    return list(selected.values())[:3]


def image_prompt(uid: str, request: str) -> str:
    additions = [r["token"] for r in resolve_aesthetics(uid, "image", request) if r["origin"] != "request"]
    # 请求保留原文；持久偏好只补未明确指定的视觉类别。
    if not additions or any(cue in request for cue in ("只按", "不要参考", "忽略偏好", "别用偏好")):
        return request
    return request + "\n可选的视觉偏好（与本次要求冲突时忽略）：" + "、".join(additions)


def create_relationship_object(uid: str, event_id: int, *, commit: bool = True) -> int | None:
    if not flag("aesthetics_enabled"):
        return None
    with db._lock:
        event = db.conn.execute("SELECT * FROM relationship_events WHERE user_id=? AND id=?", (uid, event_id)).fetchone()
        if not event or event["event_type"] not in OBJECTS or not _event_exists(uid, event_id):
            return None
        title, shape = OBJECTS[event["event_type"]]
        now = datetime.now().isoformat(timespec="seconds")
        db.conn.execute("INSERT OR IGNORE INTO artifacts(user_id,artifact_type,source_type,source_id,title,content,created_at,updated_at) "
                        "VALUES (?,'relationship_object','relationship_event',?,?,?,?,?)",
                        (uid, event_id, title, shape, now, now))
        row = db.conn.execute("SELECT id FROM artifacts WHERE user_id=? AND artifact_type='relationship_object' AND source_id=?", (uid, event_id)).fetchone()
        if commit:
            db.conn.commit()
        return int(row["id"])


def place(uid: str, artifact_id: int, *, x: float = .5, y: float = .5,
          hidden: bool = False, slot: str = "room", theme_version: int = 1) -> None:
    if slot != "room" or theme_version != 1 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in (x, y)):
        raise ValueError("房间位置或主题版本无效")
    with db._lock:
        if not _source_exists(uid, "artifact", artifact_id):
            raise ValueError("作品不存在或不属于当前人格")
        if not flag("aesthetics_enabled") and not db.conn.execute("SELECT 1 FROM artifact_placements WHERE user_id=? AND artifact_id=?", (uid, artifact_id)).fetchone():
            raise ValueError("审美功能已关闭")
        db.conn.execute("INSERT INTO artifact_placements(user_id,artifact_id,slot,x,y,theme_version,hidden) VALUES (?,?,?,?,?,?,?) "
                        "ON CONFLICT(user_id,artifact_id) DO UPDATE SET slot=excluded.slot,x=excluded.x,y=excluded.y,hidden=excluded.hidden,theme_version=excluded.theme_version",
                        (uid, artifact_id, slot, x, y, theme_version, int(hidden)))
        db.conn.commit()


def room(uid: str) -> dict:
    with db._lock:
        items = db.conn.execute("SELECT a.id,a.title,a.artifact_type,a.content,p.x,p.y,p.hidden FROM artifacts a "
                               "LEFT JOIN artifact_placements p ON p.user_id=a.user_id AND p.artifact_id=a.id "
                               "WHERE a.user_id=? AND a.status='active' ORDER BY a.id DESC LIMIT 100", (uid,)).fetchall()
        items = [dict(r) for r in items if _source_exists(uid, "artifact", r["id"])]
        for item in items:
            item["shape"] = item.pop("content") if item["artifact_type"] == "relationship_object" else "box"
            item["x"] = max(0, min(1, float(item["x"]))) if item["x"] is not None else .5
            item["y"] = max(0, min(1, float(item["y"]))) if item["y"] is not None else .5
            item["placed"] = item["hidden"] is not None
            item["hidden"] = bool(item["hidden"])
        return {"items": items, "preferences": preferences(uid), "options": TOKENS,
                "theme": resolve_aesthetics(uid, "room"), "enabled": flag("aesthetics_enabled")}
