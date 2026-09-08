# -*- coding: utf-8 -*-
"""F02 日记、约定、研究与惊喜素材互通。

契约（docs/Zcode技术指导.md F02 + 调度文档批次 7）：

- 引用边落 LC-1 的 ``source_links``：``owner_type`` 白名单 diary / research /
  promise / artifact，``source_type`` 白名单 event / activity / fact /
  knowledge / character_life；方向明确，禁止无限递归；
- 解析最多深 2、总 5 项、1200 token；现实 / 角色虚构 / 观点三种 namespace
  分别编译——不能把她写的日记当第二独立证据证明同件事；
- 源消失立即拒绝使用（引用边删除、派生缓存失效）；已保存产物按删除契约处理，
  不保留敏感摘录；
- 只增强素材，不为既有历史推测补链接；关闭 provider 不删产物。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .log import logger

OWNER_TYPES: tuple[str, ...] = ("diary", "research", "promise", "artifact")
SOURCE_TYPES: tuple[str, ...] = ("event", "activity", "fact", "knowledge", "character_life")

MAX_DEPTH = 2
MAX_ITEMS = 5
MAX_TOKENS = 1200

# 三种命名空间分别编译：现实（可作证据）/ 角色虚构（不可冒充现实）/
# 观点（她的看法，不是事实）。
NAMESPACE_OF = {
    "event": "reality",
    "activity": "reality",
    "fact": "reality",
    "knowledge": "opinion",
    "character_life": "fiction",
}

NAMESPACES: tuple[str, ...] = ("reality", "fiction", "opinion")


class NarrativeSourceError(ValueError):
    """素材引用的预期业务错误。"""


@dataclass(frozen=True)
class SourceMaterial:
    source_type: str
    source_id: int
    namespace: str
    text: str
    version: str = ""
    title: str = ""

    @property
    def token_count(self) -> int:
        return len(self.text)


def _now(moment: datetime | None = None) -> str:
    return (moment or datetime.now()).isoformat(timespec="seconds")


# ---- 引用边 ----

def link_source(user_id: str, owner_type: str, owner_id: int, source_type: str,
                source_id: int, *, version: str = "",
                now: datetime | None = None) -> bool:
    """保存一条产物→来源的引用边（幂等：同 owner/source 只一条）。"""
    if owner_type not in OWNER_TYPES:
        raise NarrativeSourceError(f"未知产物类型：{owner_type}")
    if source_type not in SOURCE_TYPES:
        raise NarrativeSourceError(f"未知来源类型：{source_type}")
    from .userdb import db

    with db._lock:
        cur = db.conn.execute(
            "INSERT OR IGNORE INTO source_links "
            "(user_id, owner_type, owner_id, source_type, source_id, source_version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, owner_type, int(owner_id), source_type, int(source_id),
             str(version or ""), _now(now)),
        )
        db.conn.commit()
    return bool(cur.rowcount)


def links_for(user_id: str, owner_type: str, owner_id: int) -> list[dict]:
    from .userdb import db

    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM source_links WHERE user_id=? AND owner_type=? AND owner_id=? "
            "ORDER BY source_type, source_id",
            (user_id, owner_type, int(owner_id)),
        ).fetchall()
    return [dict(row) for row in rows]


def forget_for_source(user_id: str, source_type: str, source_id: int) -> int:
    """源消失：删除所有指向它的引用边（下游立即不可再引用）。"""
    from .userdb import db

    with db._lock:
        cur = db.conn.execute(
            "DELETE FROM source_links WHERE user_id=? AND source_type=? AND source_id=?",
            (user_id, source_type, int(source_id)),
        )
        db.conn.commit()
    if cur.rowcount:
        logger.info("[素材互通] 源消失，清理引用边 {} 条：{}#{}",
                    cur.rowcount, source_type, source_id)
    return int(cur.rowcount)


def owners_of(user_id: str, source_type: str, source_id: int) -> list[dict]:
    """哪些产物引用了该来源（源删除时用于产物失效判定）。"""
    from .userdb import db

    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM source_links WHERE user_id=? AND source_type=? AND source_id=?",
            (user_id, source_type, int(source_id)),
        ).fetchall()
    return [dict(row) for row in rows]


# ---- 解析（只认仍存在的源） ----

def _load_source(user_id: str, source_type: str, source_id: int) -> SourceMaterial | None:
    from .userdb import db

    with db._lock:
        if source_type == "event":
            row = db.conn.execute(
                "SELECT id, object, payload_json, occurred_at FROM relationship_events "
                "WHERE user_id=? AND id=? AND status='active'",
                (user_id, int(source_id)),
            ).fetchone()
            if row is None:
                return None
            text = str(row["object"] or "")
            if not text:
                return None
            return SourceMaterial("event", int(row["id"]), "reality", text,
                                  version=str(row["occurred_at"] or ""))
        if source_type == "activity":
            row = db.conn.execute(
                "SELECT id, title, status, updated_at FROM activities WHERE user_id=? AND id=?",
                (user_id, int(source_id)),
            ).fetchone()
            if row is None:
                return None
            return SourceMaterial("activity", int(row["id"]), "reality",
                                  str(row["title"] or ""),
                                  version=str(row["updated_at"] or ""))
        if source_type == "fact":
            row = db.conn.execute(
                "SELECT id, content, surface_policy, status FROM facts WHERE user_id=? AND id=?",
                (user_id, int(source_id)),
            ).fetchone()
            if row is None or row["status"] != "active" or row["surface_policy"] == "never_surface":
                return None
            return SourceMaterial("fact", int(row["id"]), "reality", str(row["content"] or ""))
        if source_type == "knowledge":
            row = db.conn.execute(
                "SELECT id, title, created_at FROM kb_documents WHERE user_id=? AND id=?",
                (user_id, int(source_id)),
            ).fetchone()
            if row is None:
                return None
            return SourceMaterial("knowledge", int(row["id"]), "opinion",
                                  str(row["title"] or ""),
                                  version=str(row["created_at"] or ""))
        if source_type == "character_life":
            row = db.conn.execute(
                "SELECT id, payload_json, occurred_at FROM character_life_events "
                "WHERE user_id=? AND id=?",
                (user_id, int(source_id)),
            ).fetchone()
            if row is None:
                return None
            import json

            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                payload = {}
            text = str(payload.get("description") or "")
            if not text:
                return None
            return SourceMaterial("character_life", int(row["id"]), "fiction", text,
                                  version=str(row["occurred_at"] or ""))
    return None


def resolve_source(user_id: str, ref: dict) -> SourceMaterial | None:
    """解析一条引用；源不存在/已删除/不可展示时返回 None（拒绝使用）。"""
    source_type = str(ref.get("source_type") or "")
    source_id = ref.get("source_id")
    if source_type not in SOURCE_TYPES or source_id is None:
        return None
    return _load_source(user_id, source_type, int(source_id))


def collect_sources(user_id: str, purpose: str, *, limit: int = MAX_ITEMS,
                    depth: int = MAX_DEPTH) -> list[SourceMaterial]:
    """按用途收集产物素材：深 ≤2、总 ≤5 项、≤1200 token，源消失即跳过。

    purpose 形如 ``diary:<id>`` / ``artifact:<id>``；未登记的用途返回空列表
    （只增强素材，不为既有历史推测补链接）。
    """
    owner_type, _, raw_id = str(purpose or "").partition(":")
    if owner_type not in OWNER_TYPES or not raw_id.isdigit():
        return []
    owner_id = int(raw_id)
    out: list[SourceMaterial] = []
    seen: set[tuple[str, int]] = set()
    budget = MAX_TOKENS
    limit = max(1, min(MAX_ITEMS, int(limit)))
    frontier: list[tuple[str, int, int]] = [
        (str(link["source_type"]), int(link["source_id"]), 1)
        for link in links_for(user_id, owner_type, owner_id)
    ]
    while frontier and len(out) < limit:
        source_type, source_id, level = frontier.pop(0)
        key = (source_type, source_id)
        if key in seen:
            continue
        seen.add(key)
        material = resolve_source(user_id, {"source_type": source_type, "source_id": source_id})
        if material is None:
            continue  # 源消失：本轮静默跳过（引用边由 forget_for_source 清理）
        if material.token_count > budget:
            continue
        budget -= material.token_count
        out.append(material)
        if level < depth and source_type in OWNER_TYPES:
            # 产物引用产物：继续向下解析一层（禁止无限递归）
            frontier.extend(
                (str(link["source_type"]), int(link["source_id"]), level + 1)
                for link in links_for(user_id, source_type, source_id)
            )
    return out


def compile_namespaces(materials: list[SourceMaterial]) -> dict[str, list[SourceMaterial]]:
    """三种命名空间分别编译；调用方不得跨层当证据。"""
    compiled: dict[str, list[SourceMaterial]] = {name: [] for name in NAMESPACES}
    for material in materials:
        compiled.setdefault(material.namespace, []).append(material)
    return compiled


def format_material(materials: list[SourceMaterial]) -> str:
    """给生成层的素材块；虚构与观点明确标注，不与现实事实混层。"""
    compiled = compile_namespaces(materials)
    lines: list[str] = []
    if compiled.get("reality"):
        lines.append("【真实记录】" + "；".join(m.text for m in compiled["reality"]))
    if compiled.get("fiction"):
        lines.append("【她自己的虚构日常（不是对方的现实）】"
                     + "；".join(m.text for m in compiled["fiction"]))
    if compiled.get("opinion"):
        lines.append("【资料/观点（不是事实断言）】" + "；".join(m.text for m in compiled["opinion"]))
    return "\n".join(lines)
