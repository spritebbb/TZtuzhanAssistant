# -*- coding: utf-8 -*-
"""L16 可选择共享知识与创作导出：默认一切隔离，明确授权才可读。

契约（docs/Zcode技术指导.md L16 + 总纲批次 12）：

- ``shared_resources``：把本人格命名空间下的一件真实资源（kb 文档/共同产物/
  共创/清单）登记为可共享；``resource_grants`` 记录「分享给谁」，首期 permission
  只读（read）；
- 只有用户在既有详情入口明确「分享给某角色」才创建 grant；默认全部隔离；
- 读取校验五件事：资源仍存在、owner 未撤销、grantee 未撤销、permission 仍
  read、版本一致；任一失败即拒绝（不缓存旧授权）；
- 共享不复制原文：授权的是「可读资格」，消费方（context_registry / 导出）按
  id 现读现校验；源删除通过既有 LC-1 级联与 trigger 收口；
- 导出包含 ACL 清单（knowledge 类别），导入时默认全部收紧为私有（grants
  的 revoked_at 全部置位），待用户重新授权。
"""
from __future__ import annotations

from datetime import datetime

from .log import logger

RESOURCE_TYPES: tuple[str, ...] = ("kb_document", "artifact", "writing", "list")
PERMISSIONS: tuple[str, ...] = ("read",)  # 首期只读

_RESOURCE_TABLES = {
    "kb_document": "kb_documents",
    "artifact": "artifacts",
    "writing": "activity_writings",
    "list": "activity_lists",
}


class SharedResourceError(ValueError):
    """共享授权的预期业务错误。"""


def enabled() -> bool:
    """L16 开关：关闭后停止新增授权，已有授权读取同样拒绝（默认开）。"""
    try:
        from .features import flag

        return flag("shared_resources_enabled")
    except Exception:
        return False


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _resource_exists(user_id: str, resource_type: str, resource_id: int) -> bool:
    table = _RESOURCE_TABLES.get(resource_type)
    if table is None:
        return False
    from .userdb import db

    try:
        row = db.conn.execute(
            f"SELECT id FROM {table} WHERE id = ? AND user_id = ?",
            (int(resource_id), user_id),
        ).fetchone()
    except Exception:
        return False
    return row is not None


def share(user_id: str, resource_type: str, resource_id: int,
          grantee_scope: str) -> dict:
    """把一件资源分享给 grantee 人格（幂等；重复分享复用既有授权）。"""
    if not enabled():
        raise SharedResourceError("共享功能已关闭，无法新增授权")
    if resource_type not in RESOURCE_TYPES:
        raise SharedResourceError(f"未知的资源类型：{resource_type}")
    if grantee_scope == user_id:
        raise SharedResourceError("资源已在本人格内，无需分享")
    from .userdb import db

    with db._lock:
        if not _resource_exists(user_id, resource_type, resource_id):
            raise SharedResourceError("资源不存在或已删除，无法分享")
        db.conn.execute(
            "INSERT OR IGNORE INTO shared_resources "
            "(user_id, resource_type, resource_id, created_at) VALUES (?, ?, ?, ?)",
            (user_id, resource_type, int(resource_id), _now()),
        )
        row = db.conn.execute(
            "SELECT id FROM shared_resources WHERE user_id=? AND resource_type=? "
            "AND resource_id=?",
            (user_id, resource_type, int(resource_id)),
        ).fetchone()
        share_id = int(row["id"])
        # 重新分享：先复活既有 revoked 行，再幂等插入（UNIQUE 不挡复活路径）
        db.conn.execute(
            "UPDATE resource_grants SET revoked_at=NULL WHERE resource_id=? "
            "AND grantee_scope=? AND permission='read' AND revoked_at IS NOT NULL",
            (share_id, grantee_scope),
        )
        db.conn.execute(
            "INSERT OR IGNORE INTO resource_grants "
            "(user_id, resource_id, grantee_scope, permission, created_at) "
            "VALUES (?, ?, ?, 'read', ?)",
            (user_id, share_id, grantee_scope, _now()),
        )
        db.conn.execute(
            "UPDATE shared_resources SET revoked_at=NULL WHERE id=? AND revoked_at IS NOT NULL",
            (share_id,),
        )
        grant = db.conn.execute(
            "SELECT id FROM resource_grants WHERE resource_id=? AND grantee_scope=? "
            "AND permission='read'",
            (share_id, grantee_scope),
        ).fetchone()
        db.conn.commit()
    logger.info("[共享] {} 分享 {}#{} → {}", user_id, resource_type, resource_id, grantee_scope)
    return {"resource_id": share_id, "grant_id": int(grant["id"]),
            "resource_type": resource_type, "permission": "read"}


def revoke(user_id: str, resource_type: str, resource_id: int,
           grantee_scope: str | None = None) -> int:
    """撤销共享；grantee_scope=None 撤销全部授权（owner 下线即失效）。"""
    from .userdb import db

    now = _now()
    with db._lock:
        row = db.conn.execute(
            "SELECT id FROM shared_resources WHERE user_id=? AND resource_type=? "
            "AND resource_id=?",
            (user_id, resource_type, int(resource_id)),
        ).fetchone()
        if row is None:
            return 0
        share_id = int(row["id"])
        if grantee_scope is None:
            cur = db.conn.execute(
                "UPDATE resource_grants SET revoked_at=? WHERE resource_id=? "
                "AND revoked_at IS NULL",
                (now, share_id),
            )
            db.conn.execute(
                "UPDATE shared_resources SET revoked_at=?, version=version+1 "
                "WHERE id=? AND revoked_at IS NULL",
                (now, share_id),
            )
        else:
            cur = db.conn.execute(
                "UPDATE resource_grants SET revoked_at=? WHERE resource_id=? "
                "AND grantee_scope=? AND revoked_at IS NULL",
                (now, share_id, grantee_scope),
            )
        db.conn.commit()
    return int(cur.rowcount) if grantee_scope is not None else 1


def check_access(grantee_scope: str, resource_type: str, resource_id: int,
                 *, version: int | None = None) -> bool:
    """读取资格校验：五件事同时成立才可读（现读现验，不缓存）。

    shared_resources.id 全局唯一（AUTOINCREMENT），可直接按
    (resource_type, resource_id) 找到 owner 的登记行。
    """
    from .userdb import db

    if not enabled():
        return False
    if _RESOURCE_TABLES.get(resource_type) is None:
        return False
    with db._lock:
        shares = db.conn.execute(
            "SELECT id, user_id, version, revoked_at FROM shared_resources "
            "WHERE resource_type=? AND resource_id=?",
            (resource_type, int(resource_id)),
        ).fetchall()
        for share_row in shares:
            if share_row["revoked_at"] is not None:
                continue
            if version is not None and int(share_row["version"]) != int(version):
                continue
            owner = str(share_row["user_id"])
            if not _resource_exists(owner, resource_type, resource_id):
                continue
            grant = db.conn.execute(
                "SELECT id FROM resource_grants WHERE resource_id=? AND grantee_scope=? "
                "AND permission='read' AND revoked_at IS NULL",
                (int(share_row["id"]), grantee_scope),
            ).fetchone()
            if grant is not None:
                return True
    return False


def authorized_fragments(grantee_scope: str, resource_type: str,
                         limit: int = 20) -> list[dict]:
    """grantee 视角：列出当前可读的资源摘要（context_registry 只拿授权片段）。"""
    from .userdb import db

    table = _RESOURCE_TABLES.get(resource_type)
    if table is None or resource_type not in ("kb_document", "artifact"):
        return []
    if not enabled():
        return []
    limit = max(1, min(100, int(limit)))
    with db._lock:
        rows = db.conn.execute(
            "SELECT s.resource_id, s.version, s.user_id AS owner "
            "FROM resource_grants g JOIN shared_resources s ON s.id = g.resource_id "
            "WHERE g.grantee_scope=? AND g.permission='read' AND g.revoked_at IS NULL "
            "AND s.revoked_at IS NULL AND s.resource_type=? "
            "ORDER BY s.id DESC LIMIT ?",
            (grantee_scope, resource_type, limit),
        ).fetchall()
    items: list[dict] = []
    for row in rows:
        owner = str(row["owner"])
        resource_id = int(row["resource_id"])
        # 现读现验：owner 资源必须仍存在
        if not _resource_exists(owner, resource_type, resource_id):
            continue
        if resource_type == "kb_document":
            title_col, text_col = "filename", None
        else:
            title_col, text_col = "title", "content"
        with db._lock:
            res = db.conn.execute(
                f"SELECT * FROM {table} WHERE id=? AND user_id=?",
                (resource_id, owner),
            ).fetchone()
        if res is None:
            continue
        item = {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "owner": owner,
            "version": int(row["version"]),
            "title": res[title_col],
        }
        if text_col and res[text_col]:
            item["excerpt"] = str(res[text_col])[:200]
        items.append(item)
    return items


def list_shares(user_id: str) -> list[dict]:
    """owner 视角：我分享出去的东西与当前授权状态。"""
    from .userdb import db

    with db._lock:
        rows = db.conn.execute(
            "SELECT s.id, s.resource_type, s.resource_id, s.version, s.revoked_at, "
            "g.grantee_scope, g.permission, g.revoked_at AS grant_revoked_at "
            "FROM shared_resources s LEFT JOIN resource_grants g "
            "ON g.resource_id = s.id WHERE s.user_id=? ORDER BY s.id DESC",
            (user_id,),
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "resource_type": row["resource_type"],
            "resource_id": int(row["resource_id"]),
            "version": int(row["version"]),
            "revoked": row["revoked_at"] is not None,
            "grants": [
                {"grantee": row["grantee_scope"], "permission": row["permission"],
                 "revoked": row["grant_revoked_at"] is not None}
            ] if row["grantee_scope"] else [],
        }
        for row in rows
    ]


def tighten_all_on_restore(target_user_id: str) -> None:
    """导入关系包后把 ACL 全部收紧为私有：授权撤销待用户重新分享。"""
    from .userdb import db

    now = _now()
    with db._lock:
        db.conn.execute(
            "UPDATE resource_grants SET revoked_at=? WHERE user_id=? "
            "AND revoked_at IS NULL",
            (now, target_user_id),
        )
        db.conn.commit()
    logger.info("[共享] 恢复命名空间 {} 的授权已全部收紧", target_user_id)
