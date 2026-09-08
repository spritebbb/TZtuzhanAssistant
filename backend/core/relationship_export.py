# -*- coding: utf-8 -*-
"""M1/E03 关系导出与恢复：用户可以带走、预览和恢复这段关系。

设计约束（TECH-PLAN M1 退出标准：导出→新临时数据目录恢复→关键计数与引用一致）：
- 选择性导出：按类别（identity/memory/milestones/life/tasks/activities/
  events/knowledge/conversations）选择；kv_store 按登记表（kv_registry）枚举，
  只导出标记 export=True 的持久关系状态。
- 恢复预览：先校验格式、schema 版本、引用完整性（含跨类别引用），再展示
  将写入的计数；不做静默覆盖。
- 恢复写入当前 bot.db 的目标命名空间：要求目标命名空间为空；整数主键
  （bot.db 全局唯一）按 AUTOINCREMENT 重新分配，引用列按映射重建，
  user_id 文本列一律重写为目标命名空间。
- 恢复完成后向量库与 SQLite 可能暂时不一致：SQLite 是唯一权威源，由
  vector_store.rebuild_all 的既有机制在 embedding 就绪时全量重灌。
"""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime

from .kv_registry import match_spec
from .userdb import db

BUNDLE_KIND = "tuzhan-relationship-bundle"
BUNDLE_VERSION = 1

# 类别 → 表清单（导出与恢复顺序无关；全部表都在 userdb.reset 双清单里）
CATEGORIES: dict[str, tuple[str, ...]] = {
    "identity": ("users", "user_meta"),
    "memory": ("facts", "long_memory", "triples", "user_profile", "user_terms", "user_style_map",
               "memory_policy", "memory_annotations", "first_occurrences", "aesthetic_preferences"),
    "milestones": ("affection_log", "mood_log", "unlocks", "important_dates"),
    "life": ("diary", "research_reports", "stickers", "future_letters", "relationship_snapshots", "dual_perspectives", "relationship_versions", "character_life_events", "reunion_arcs", "companion_requests", "source_links"),
    "tasks": ("tasks", "promises", "open_questions"),
    "activities": (
        "activities", "activity_notes", "activity_viewpoints", "activity_goals",
        "goal_progress", "activity_writings", "writing_turns", "artifacts",
        "reading_segments", "reading_bookmarks", "observation_entries", "artifact_placements",
    ),
    "events": ("relationship_events", "pending_thoughts", "relationship_style_evidence",
               "domain_trust_events", "domain_trust_snapshot"),
    "knowledge": ("kb_documents", "kb_chunks", "knowledge_opinions", "knowledge_opinion_sources"),
    "conversations": ("messages",),
}

# 事件/心事的 source_type → 被引用表（payload 里的来源声明）
_SOURCE_TABLE_BY_TYPE = {
    "relationship_event": "relationship_events",
    "artifact": "artifacts",
    "knowledge_opinion": "knowledge_opinions",
    "activity": "activities",
    "promise": "promises",
    "fact": "facts",
    "important_date": "important_dates",
    "future_letter": "future_letters",
    "relationship_snapshot": "relationship_snapshots",
}

_SNAPSHOT_MANIFEST_TABLE_BY_TYPE = {
    "relationship_event": "relationship_events",
    "artifact": "artifacts",
    "diary": "diary",
    "user_term": "user_terms",
    "activity_viewpoint": "activity_viewpoints",
}


class BundleError(ValueError):
    """导出/恢复的可预期业务错误。"""


# ---- 引用规则：(所在表, 取引用函数 row → (被引用表, 值) | None, 列名) ----

def _rule_static(table: str, column: str, ref_table: str, sentinels=frozenset({0})):
    def ref(row: dict):
        value = row.get(column)
        if value is None or int(value) in sentinels:
            return None
        return ref_table, int(value)

    return (table, ref, column)


def _rule_dynamic(table: str, column: str, type_column: str):
    def ref(row: dict):
        value = row.get(column)
        if value is None:
            return None
        # source_id=0 是各来源模块的哨兵语义（占位、不指向真实行）——
        # important_date 事件、完成共读的 book_summary 等既有写入均如此，
        # 不参与引用完整性校验。
        if int(value) == 0:
            return None
        ref_table = _SOURCE_TABLE_BY_TYPE.get(str(row.get(type_column) or ""))
        if ref_table is None:
            return None
        return ref_table, int(value)

    return (table, ref, column)


_REFERENCE_RULES = (
    _rule_static("artifact_placements", "artifact_id", "artifacts", frozenset()),
    _rule_dynamic("aesthetic_preferences", "source_id", "source_type"),
    _rule_static("activity_notes", "activity_id", "activities"),
    _rule_static("reading_segments", "activity_id", "activities"),
    _rule_static("observation_entries", "activity_id", "activities"),
    _rule_static("reading_bookmarks", "segment_id", "reading_segments"),
    _rule_static("activity_viewpoints", "activity_id", "activities"),
    _rule_static("activity_goals", "activity_id", "activities"),
    _rule_static("goal_progress", "activity_id", "activities"),
    _rule_static("activity_writings", "activity_id", "activities"),
    _rule_static("writing_turns", "activity_id", "activities"),
    _rule_static("kb_chunks", "doc_id", "kb_documents"),
    _rule_static("knowledge_opinions", "document_id", "kb_documents"),
    _rule_static("knowledge_opinion_sources", "opinion_id", "knowledge_opinions"),
    _rule_static("knowledge_opinion_sources", "chunk_id", "kb_chunks"),
    _rule_static("activities", "document_id", "kb_documents"),
    _rule_static("facts", "conflicts_with_fact_id", "facts", frozenset()),
    _rule_static("memory_policy", "fact_id", "facts", frozenset()),
    _rule_static("memory_annotations", "fact_id", "facts", frozenset()),
    _rule_static("memory_annotations", "source_event_id", "relationship_events", frozenset()),
    _rule_static("first_occurrences", "source_event_id", "relationship_events", frozenset()),
    _rule_static("future_letters", "goal_id", "activities"),
    _rule_static("future_letters", "unlocked_by_event_id", "relationship_events", frozenset()),
    _rule_static("reunion_arcs", "source_snapshot_id", "character_life_events", frozenset()),
    _rule_static("relationship_style_evidence", "event_id", "relationship_events", frozenset()),
    _rule_static("domain_trust_events", "event_id", "relationship_events", frozenset()),
    _rule_static("companion_requests", "life_event_id", "character_life_events", frozenset()),
    # open_questions.source_message_id 指向 messages（不属于关系包）：按
    # memory_policy.source_message_ids 先例不建引用规则，恢复时统一清空。
    _rule_dynamic("artifacts", "source_id", "source_type"),
    _rule_dynamic("source_links", "source_id", "source_type"),
    _rule_dynamic("relationship_events", "source_id", "source_type"),
    _rule_dynamic("pending_thoughts", "source_id", "source_type"),
)


def _row_to_dict(row) -> dict:
    return {key: row[key] for key in row.keys()}


def _table_names() -> set[str]:
    with db._lock:
        rows = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    return {row["name"] for row in rows}


def _current_schema_version() -> int:
    from .userdb import _SCHEMA_VERSION

    return int(_SCHEMA_VERSION)


def export_bundle(user_id: str, categories: list[str] | None = None) -> dict:
    """导出一个人格命名空间的全部（或选定类别的）关系数据。"""
    known = _table_names()
    selected = list(CATEGORIES) if not categories else list(categories)
    unknown = [name for name in selected if name not in CATEGORIES]
    if unknown:
        raise BundleError(f"未知的数据类别：{'、'.join(unknown)}")

    data: dict[str, list[dict]] = {}
    counts: dict[str, dict[str, int]] = {}
    with db._lock:
        for category in selected:
            tables: dict[str, int] = {}
            for table in CATEGORIES[category]:
                if table not in known:
                    continue
                rows = db.conn.execute(
                    f"SELECT * FROM {table} WHERE user_id = ?", (user_id,)
                ).fetchall()
                data[table] = [_row_to_dict(row) for row in rows]
                tables[table] = len(data[table])
            counts[category] = tables

    # kv_store：按登记表枚举，只带走标记 export=True 的持久关系状态。
    with db._lock:
        kv_rows = db.conn.execute(
            "SELECT key, value FROM kv_store WHERE user_id = ?", (user_id,)
        ).fetchall()
    kv_export: dict[str, str] = {}
    kv_skipped = 0
    for row in kv_rows:
        spec = match_spec(str(row["key"]))
        if spec is not None and spec.export:
            kv_export[str(row["key"])] = str(row["value"])
        else:
            kv_skipped += 1

    return {
        "kind": BUNDLE_KIND,
        "version": BUNDLE_VERSION,
        "schema_version": _current_schema_version(),
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "source_user_id": user_id,
        "categories": selected,
        "counts": counts,
        "kv": {"exported": kv_export, "skipped_by_registry": kv_skipped},
        "data": data,
    }


def _validate_references(data: dict[str, list[dict]]) -> list[str]:
    errors: list[str] = []
    for table, ref_fn, _column in _REFERENCE_RULES:
        rows = data.get(table) or []
        if not rows:
            continue
        broken = False
        for row in rows:
            pointed = ref_fn(row)
            if pointed is None:
                continue
            ref_table, value = pointed
            ref_rows = data.get(ref_table)
            if ref_rows is None:
                errors.append(
                    f"类别不完整：{table}.{_column_label(table, ref_fn)} 引用了"
                    f" {ref_table}，但备份里没有该表（请导出完整类别组合）"
                )
                broken = True
                break
            if not any(int(candidate.get("id") or 0) == value for candidate in ref_rows):
                errors.append(f"引用断裂：{table} 的 {ref_table}.id={value} 在备份中不存在")
                broken = True
                break
        if broken:
            continue
    for snapshot in data.get("relationship_snapshots") or []:
        try:
            manifest = json.loads(snapshot.get("source_manifest_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            errors.append("关系快照的来源清单不是有效 JSON")
            continue
        for item in manifest.get("items") or []:
            source_type = str(item.get("type") or "")
            ref_table = _SNAPSHOT_MANIFEST_TABLE_BY_TYPE.get(source_type)
            if ref_table is None:
                errors.append(f"关系快照含未知来源类型：{source_type or '?'}")
                continue
            ref_rows = data.get(ref_table)
            if ref_rows is None:
                errors.append(
                    f"类别不完整：关系快照来源引用了 {ref_table}，但备份里没有该表"
                )
                continue
            try:
                source_id = int(item.get("id"))
            except (TypeError, ValueError):
                errors.append(f"关系快照的 {source_type} 来源 id 无效")
                continue
            if not any(int(row.get("id") or 0) == source_id for row in ref_rows):
                errors.append(
                    f"引用断裂：关系快照的 {source_type}.id={source_id} 在备份中不存在"
                )
    return errors


def _remap_snapshot_manifests(
    data: dict[str, list[dict]], id_maps: dict[str, dict[int, int]], target_user_id: str
) -> None:
    """恢复后二阶段重写冻结清单的来源 id，使其指向目标命名空间的新行。"""
    for source_row in data.get("relationship_snapshots") or []:
        old_snapshot_id = int(source_row["id"])
        new_snapshot_id = id_maps.get("relationship_snapshots", {}).get(old_snapshot_id)
        if new_snapshot_id is None:
            continue
        manifest = json.loads(source_row.get("source_manifest_json") or "{}")
        for item in manifest.get("items") or []:
            ref_table = _SNAPSHOT_MANIFEST_TABLE_BY_TYPE[str(item["type"])]
            item["id"] = id_maps[ref_table][int(item["id"])]
        db.conn.execute(
            "UPDATE relationship_snapshots SET source_manifest_json = ? "
            "WHERE id = ? AND user_id = ?",
            (json.dumps(manifest, ensure_ascii=False), new_snapshot_id, target_user_id),
        )


def _column_label(table: str, ref_fn: Callable) -> str:
    for rule_table, _fn, column in _REFERENCE_RULES:
        if rule_table == table and _fn == ref_fn:
            return column
    return "?"


def preview_restore(bundle: dict, target_user_id: str) -> dict:
    """恢复预览：格式/版本/引用校验 + 目标命名空间占用检查，不写任何数据。"""
    errors: list[str] = []
    if not isinstance(bundle, dict) or bundle.get("kind") != BUNDLE_KIND:
        raise BundleError("这不是菟菚的关系备份文件")
    if int(bundle.get("version") or 0) != BUNDLE_VERSION:
        errors.append(f"备份格式版本不支持：{bundle.get('version')}（需要 {BUNDLE_VERSION}）")
    bundle_schema = int(bundle.get("schema_version") or 0)
    current_schema = _current_schema_version()
    if bundle_schema != current_schema:
        errors.append(
            f"备份 schema v{bundle_schema} 与当前 v{current_schema} 不一致；"
            "请在相同版本的应用间迁移，避免列缺失导致静默丢数据"
        )
    data = bundle.get("data") or {}
    if not isinstance(data, dict) or not data:
        errors.append("备份里没有可恢复的数据")
        data = {}

    known = _table_names()
    counts = {table: len(rows) for table, rows in data.items()}
    errors.extend(_validate_references(data))

    occupied: dict[str, int] = {}
    with db._lock:
        for table in data:
            if table not in known:
                errors.append(f"当前应用没有 {table} 表（应用版本过旧？）")
                continue
            n = int(db.conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (target_user_id,)
            ).fetchone()[0])
            if n:
                occupied[table] = n
    if occupied:
        errors.append(
            "目标命名空间已有数据，恢复会污染现有关系；只允许恢复到空命名空间："
            + "、".join(f"{table}({n} 条)" for table, n in occupied.items())
        )

    return {
        "ok": not errors,
        "errors": errors,
        "counts": counts,
        "total": sum(counts.values()),
        "kv_exported": len((bundle.get("kv") or {}).get("exported") or {}),
        "target_user_id": target_user_id,
        "source_user_id": bundle.get("source_user_id"),
    }


def _topo_order(tables: list[str]) -> list[str]:
    """被引用的表先插入（Kahn 拓扑；引用边来自 _REFERENCE_RULES）。"""
    edges: dict[str, set[str]] = defaultdict(set)  # ref_table → {依赖它的表}
    indegree: dict[str, int] = {table: 0 for table in tables}
    for table, ref_fn, _column in _REFERENCE_RULES:
        if table not in indegree:
            continue
        for row in _rows_of.get(table, []):
            pointed = ref_fn(row)
            if pointed is None:
                continue
            ref_table = pointed[0]
            if ref_table in indegree and ref_table != table and table not in edges[ref_table]:
                edges[ref_table].add(table)
                indegree[table] += 1
    ready = sorted(table for table, degree in indegree.items() if degree == 0)
    order: list[str] = []
    while ready:
        table = ready.pop(0)
        order.append(table)
        for dependent in sorted(edges.get(table, ())):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
    # 环（自引用等）：剩余表按名字追加，插入时引用列先置空、二阶段回填。
    order.extend(sorted(table for table in tables if table not in order))
    return order


_rows_of: dict[str, list[dict]] = {}


def restore_bundle(bundle: dict, target_user_id: str, *, dry_run: bool = False) -> dict:
    """把备份恢复到空的目标命名空间（单事务；主键重映射、引用重建）。

    bot.db 被 multi 人格命名空间共享，整数主键全局唯一，因此不能原样保留
    id：按 AUTOINCREMENT 重新分配，引用列按新旧映射重建（拓扑序保证被引用
    表先插入；自引用列在全部插入完成后二阶段回填）。
    """
    preview = preview_restore(bundle, target_user_id)
    if not preview["ok"]:
        raise BundleError("；".join(preview["errors"]))
    if dry_run:
        return preview

    data: dict[str, list[dict]] = bundle["data"]
    kv_export: dict[str, str] = (bundle.get("kv") or {}).get("exported") or {}
    id_maps: dict[str, dict[int, int]] = defaultdict(dict)
    self_fixups: list[tuple[str, str, str, int, int]] = []  # (table, column, ref_table, referencing_old_id, old_ref_value)
    restored: dict[str, int] = {}
    global _rows_of
    _rows_of = data

    with db._lock:
        try:
            for table in _topo_order(list(data)):
                rows = data[table]
                rules = [
                    (ref_fn, column)
                    for rule_table, ref_fn, column in _REFERENCE_RULES
                    if rule_table == table
                ]
                has_id_column = bool(rows) and "id" in rows[0]
                for row in rows:
                    values = {
                        key: (target_user_id if key == "user_id" else row[key])
                        for key in row.keys()
                    }
                    if table == "memory_policy":
                        # messages 不属于关系包；旧编号不可指向目标库中的无关消息。
                        values["source_message_ids"] = "[]"
                    if table == "open_questions":
                        # 同上：源消息不在包内，清空编号避免指向目标库无关消息。
                        values["source_message_id"] = None
                    if table == "companion_requests":
                        # 同上：回应消息不在包内，清空编号。
                        values["response_message_id"] = None
                    # 导入的是历史，不得让旧重逢弧在新命名空间继续等待回应。
                    if table == "reunion_arcs":
                        values["state"] = "closed"
                        values["offered_message_id"] = None
                        values["response_message_id"] = None
                    old_id = int(values.pop("id")) if "id" in values else None
                    for ref_fn, column in rules:
                        if column not in values:
                            continue
                        pointed = ref_fn(row)
                        if pointed is None:
                            continue  # 保留原值（NULL 或哨兵 0）
                        ref_table, old_value = pointed
                        if ref_table == table:
                            values[column] = None  # 自引用：二阶段回填
                            if old_id is not None:
                                self_fixups.append((table, column, ref_table, old_id, old_value))
                        else:
                            mapped = id_maps.get(ref_table, {}).get(old_value)
                            if mapped is None:
                                raise BundleError(
                                    f"引用解析失败：{table}.{column} → {ref_table}.id={old_value}"
                                )
                            values[column] = mapped
                    columns = list(values.keys())
                    placeholders = ",".join("?" for _ in columns)
                    cur = db.conn.execute(
                        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
                        [values[column] for column in columns],
                    )
                    if has_id_column and old_id is not None:
                        id_maps[table][old_id] = int(cur.lastrowid)
                restored[table] = len(rows)
            # 二阶段：自引用回填（引用行与被引用行的新 id 都已确定）
            for table, column, ref_table, referencing_old_id, old_value in self_fixups:
                row_new_id = id_maps.get(table, {}).get(referencing_old_id)
                ref_new_id = id_maps.get(ref_table, {}).get(old_value)
                if row_new_id is None or ref_new_id is None:
                    continue  # 预览已校验引用完整；防御性跳过
                db.conn.execute(
                    f"UPDATE {table} SET {column} = ? WHERE id = ? AND user_id = ?",
                    (ref_new_id, row_new_id, target_user_id),
                )
            _remap_snapshot_manifests(data, id_maps, target_user_id)
            for key, value in kv_export.items():
                db.conn.execute(
                    "INSERT INTO kv_store (user_id, key, value) VALUES (?, ?, ?) "
                    "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
                    (target_user_id, key, value),
                )
            if "users" not in data:
                # 备份未包含 identity 类别时，补一行默认用户，避免关联查询悬空。
                db.conn.execute(
                    "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (target_user_id,)
                )
            db.conn.commit()
        except Exception:
            db.conn.rollback()
            raise
        finally:
            _rows_of = {}

    _schedule_vector_rebuild()
    return {"ok": True, "restored": restored, "total": sum(restored.values())}


def _schedule_vector_rebuild() -> None:
    """恢复后 SQLite 是唯一权威；向量库按既有机制全量重灌（就绪时执行）。"""
    try:
        from .memory import vector_store

        vector_store.rebuild_all("relationship-bundle-restore")
    except Exception:
        pass


def bundle_to_json(bundle: dict) -> str:
    return json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))


def bundle_from_json(raw: str | bytes) -> dict:
    try:
        parsed = json.loads(raw if isinstance(raw, str) else bytes(raw).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BundleError("备份文件不是有效的 JSON") from exc
    if not isinstance(parsed, dict):
        raise BundleError("备份文件内容格式不正确")
    return parsed


# 供测试断言登记表覆盖：跑真实流程后，kv 中不应出现未登记的键。
def unregistered_kv_keys(user_id: str) -> list[str]:
    with db._lock:
        rows = db.conn.execute(
            "SELECT key FROM kv_store WHERE user_id = ?", (user_id,)
        ).fetchall()
    return [str(row["key"]) for row in rows if match_spec(str(row["key"])) is None]
