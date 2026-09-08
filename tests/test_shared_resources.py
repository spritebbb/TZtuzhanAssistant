# -*- coding: utf-8 -*-
"""L16 可选择共享：授权/撤销/读取校验/导出收紧/人格隔离。

验收锚点（docs/Zcode技术指导.md L16 + 总纲批次 12）：
- 默认一切隔离：无 grant 不可读，owner 自己的资源无需 grant；
- 只能分享真实存在的资源；分享给本人格被拒绝；
- 撤销立即使读取资格失效（现读现验，无缓存旧授权）；
- 资源删除后 check_access 拒绝（源删除 LC-1 级联外的二次防御）；
- grantee 视角只拿授权片段；跨人格默认不可见；
- 导出恢复后授权全部收紧为私有。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_l16_"))

from backend.core import shared_resources as sr
from backend.core.userdb import db


def _kb_doc(uid: str, name: str) -> int:
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO kb_documents (user_id, filename, stored_path, format, ts) "
            "VALUES (?, ?, ?, 'txt', datetime('now'))",
            (uid, name, f"documents/{name}"),
        )
        db.conn.commit()
    return int(cur.lastrowid)


def test_default_isolated_and_share_flow() -> int:
    owner, grantee = "l16-owner", "l16-owner::persona::b"
    db.ensure_user(owner)
    doc = _kb_doc(owner, "共享书.pdf")
    # 默认隔离：未分享前 grantee 不可读
    assert sr.check_access(grantee, "kb_document", doc) is False
    info = sr.share(owner, "kb_document", doc, grantee)
    assert info["permission"] == "read"
    assert sr.check_access(grantee, "kb_document", doc) is True
    # 幂等：重复分享复用同一授权
    again = sr.share(owner, "kb_document", doc, grantee)
    assert again["grant_id"] == info["grant_id"]
    # 分享给本人格被拒绝
    try:
        sr.share(owner, "kb_document", doc, owner)
        raise AssertionError("分享给本人格应被拒绝")
    except sr.SharedResourceError:
        pass
    print("[OK] 默认隔离 / 分享幂等 / 本人格拒绝")
    return 0


def test_nonexistent_resource_rejected() -> int:
    owner, grantee = "l16-ghost", "l16-ghost::persona::b"
    db.ensure_user(owner)
    try:
        sr.share(owner, "kb_document", 999999, grantee)
        raise AssertionError("不存在资源应被拒绝")
    except sr.SharedResourceError:
        pass
    assert sr.check_access(grantee, "kb_document", 999999) is False
    # 未知资源类型
    try:
        sr.share(owner, "memory_dump", 1, grantee)
        raise AssertionError("未知资源类型应被拒绝")
    except sr.SharedResourceError:
        pass
    print("[OK] 无源资源 / 未知类型拒绝")
    return 0


def test_revoke_and_source_deleted() -> int:
    owner, grantee = "l16-revoke", "l16-revoke::persona::b"
    db.ensure_user(owner)
    doc = _kb_doc(owner, "会被撤销.pdf")
    sr.share(owner, "kb_document", doc, grantee)
    assert sr.check_access(grantee, "kb_document", doc) is True
    assert sr.revoke(owner, "kb_document", doc, grantee) == 1
    assert sr.check_access(grantee, "kb_document", doc) is False
    # 撤销后重新分享可恢复
    sr.share(owner, "kb_document", doc, grantee)
    assert sr.check_access(grantee, "kb_document", doc) is True
    # 源删除后 check_access 拒绝（二次防御）
    with db._lock:
        db.conn.execute("DELETE FROM kb_documents WHERE id=?", (doc,))
        db.conn.commit()
    assert sr.check_access(grantee, "kb_document", doc) is False
    print("[OK] 撤销即失效 / 重新分享 / 源删除二次防御")
    return 0


def test_persona_isolation_and_fragments() -> int:
    owner = "l16-frag"
    friend_a, friend_b = "l16-frag::persona::a", "l16-frag::persona::b"
    db.ensure_user(owner)
    doc1, doc2 = _kb_doc(owner, "给A.pdf"), _kb_doc(owner, "没给.pdf")
    sr.share(owner, "kb_document", doc1, friend_a)
    # B 拿不到任何片段；A 只拿到被授权的那份
    assert sr.authorized_fragments(friend_b, "kb_document") == []
    frags = sr.authorized_fragments(friend_a, "kb_document")
    assert len(frags) == 1 and frags[0]["resource_id"] == doc1
    assert frags[0]["title"] == "给A.pdf"
    # owner 清单能看到授权状态
    shares = sr.list_shares(owner)
    assert any(s["resource_id"] == doc1 for s in shares)
    print("[OK] 跨人格隔离 / 授权片段 / owner 清单")
    return 0


def test_export_roundtrip_and_tighten() -> int:
    from backend.core.relationship_export import export_bundle, restore_bundle

    owner, target = "l16-export", "l16-import"
    grantee = "l16-export::persona::b"
    db.ensure_user(owner)
    doc = _kb_doc(owner, "导出书.pdf")
    sr.share(owner, "kb_document", doc, grantee)
    bundle = export_bundle(owner, ["knowledge"])
    assert "shared_resources" in bundle["data"], list(bundle["data"].keys())
    assert "resource_grants" in bundle["data"]
    restore_bundle(bundle, target)
    # 导入收紧为私有：grantee 原授权在新命名空间不可用
    frags = sr.authorized_fragments(grantee, "kb_document")
    assert all(f["owner"] != target for f in frags), frags
    rows = db.conn.execute(
        "SELECT revoked_at FROM resource_grants WHERE user_id=?", (target,)
    ).fetchall()
    assert rows and all(r["revoked_at"] is not None for r in rows)
    print("[OK] LC-1 导出恢复 + 导入默认收紧为私有")
    return 0


def main() -> int:
    failed = (
        test_default_isolated_and_share_flow()
        + test_nonexistent_resource_rejected()
        + test_revoke_and_source_deleted()
        + test_persona_isolation_and_fragments()
        + test_export_roundtrip_and_tighten()
    )
    if failed:
        print(f"\n=== L16 共享资源：{failed} 项失败 ===")
        return 1
    print("\n=== L16 共享资源：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
