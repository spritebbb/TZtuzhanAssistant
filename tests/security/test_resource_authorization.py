# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from backend.core import shared_resources as sr
from backend.core.userdb import db


def _document(owner: str, filename: str = "private.txt") -> int:
    db.ensure_user(owner)
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO kb_documents (user_id, filename, stored_path, format, ts) "
            "VALUES (?, ?, ?, 'txt', datetime('now'))",
            (owner, filename, f"documents/{filename}"),
        )
        db.conn.commit()
    return int(cur.lastrowid)


def suite_read_requires_exact_live_grant_and_persona_scope() -> None:
    owner = "q2-owner"
    persona_a = "q2-owner::persona::a"
    persona_b = "q2-owner::persona::b"
    doc_id = _document(owner)
    assert not sr.check_access("", "kb_document", doc_id)
    assert not sr.check_access(persona_a, "kb_document", doc_id)
    sr.share(owner, "kb_document", doc_id, persona_a)
    assert sr.check_access(persona_a, "kb_document", doc_id)
    assert not sr.check_access(persona_b, "kb_document", doc_id)
    assert not sr.check_access(owner, "kb_document", doc_id)

    sr.revoke(owner, "kb_document", doc_id, persona_a)
    assert not sr.check_access(persona_a, "kb_document", doc_id)


def suite_deleted_source_and_stale_version_fail_closed() -> None:
    owner = "q2-race"
    persona = "q2-race::persona::a"
    doc_id = _document(owner, "race.txt")
    sr.share(owner, "kb_document", doc_id, persona)
    assert sr.check_access(persona, "kb_document", doc_id, version=1)
    assert not sr.check_access(persona, "kb_document", doc_id, version=2)
    with db._lock:
        db.conn.execute("DELETE FROM kb_documents WHERE id=? AND user_id=?", (doc_id, owner))
        db.conn.commit()
    assert not sr.check_access(persona, "kb_document", doc_id)
    assert sr.authorized_fragments(persona, "kb_document") == []


def suite_resource_type_and_filename_path_traversal_are_rejected() -> None:
    owner = "q2-path"
    persona = "q2-path::persona::a"
    doc_id = _document(owner, Path("../../secret.txt").name)
    try:
        sr.share(owner, "../../kb_document", doc_id, persona)
        raise AssertionError("路径伪装的资源类型必须拒绝")
    except sr.SharedResourceError:
        pass
    assert not sr.check_access(persona, "../../kb_document", doc_id)
