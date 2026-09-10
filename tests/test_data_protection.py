"""P3-04 A/B: central connector and disposable SQLCipher export proof."""
from __future__ import annotations

import ast
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.storage.connect import (
    SQLITE_HEADER,
    connect_database,
    create_encrypted_copy,
    database_manifest,
    verify_encrypted_database,
)


def _plaintext_fixture(path: Path) -> dict:
    conn = connect_database(path)
    conn.executescript(
        """
        CREATE TABLE notes(id INTEGER PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE audit(note_id INTEGER NOT NULL, action TEXT NOT NULL);
        CREATE INDEX idx_notes_body ON notes(body);
        CREATE TRIGGER notes_audit AFTER INSERT ON notes BEGIN
          INSERT INTO audit(note_id, action) VALUES (NEW.id, 'created');
        END;
        INSERT INTO notes(body) VALUES ('disposable proof row');
        PRAGMA user_version = 17;
        """
    )
    conn.commit()
    manifest = database_manifest(conn)
    conn.close()
    return manifest


def test_sqlcipher_export_is_encrypted_verified_and_equivalent(root: Path) -> None:
    source = root / "plain.db"
    target = root / "encrypted.db"
    expected = _plaintext_fixture(source)
    key = bytes(range(32))

    evidence = create_encrypted_copy(source, target, key)

    assert target.read_bytes()[: len(SQLITE_HEADER)] != SQLITE_HEADER
    assert evidence["source_manifest"] == expected
    assert evidence["target_manifest"] == expected
    assert evidence["cipher_version"].startswith("4.")
    assert verify_encrypted_database(target, key)["manifest"] == expected
    with pytest.raises((sqlite3.DatabaseError, sqlite3.OperationalError)):
        sqlite3.connect(target).execute("SELECT count(*) FROM sqlite_master").fetchone()
    with pytest.raises(sqlcipher.DatabaseError):
        connect_database(target, encrypted_key=b"x" * 32)


def test_export_refuses_bad_keys_and_existing_targets(root: Path) -> None:
    source = root / "plain.db"
    target = root / "encrypted.db"
    _plaintext_fixture(source)
    with pytest.raises(ValueError, match="32 bytes"):
        create_encrypted_copy(source, target, b"short")
    target.write_bytes(b"do-not-overwrite")
    with pytest.raises(FileExistsError):
        create_encrypted_copy(source, target, b"k" * 32)
    assert target.read_bytes() == b"do-not-overwrite"


def test_backend_database_opens_use_the_central_connector() -> None:
    backend = Path(__file__).resolve().parents[1] / "backend"
    violations: list[str] = []
    for path in backend.rglob("*.py"):
        if path.as_posix().endswith("/storage/connect.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        sqlite_aliases = {
            node.asname or node.name
            for node in tree.body
            if isinstance(node, ast.Import)
            for node in node.names
            if node.name == "sqlite3"
        }
        imported_connects = {
            item.asname or item.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "sqlite3"
            for item in node.names
            if item.name == "connect"
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in sqlite_aliases
                and node.func.attr == "connect"
            ):
                violations.append(f"{path.relative_to(backend)}:{node.lineno}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in imported_connects
            ):
                violations.append(f"{path.relative_to(backend)}:{node.lineno}")
    assert violations == []


def main() -> None:
    workspace_tmp = Path(__file__).resolve().parents[1] / ".tmp"
    workspace_tmp.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="data-protection-", dir=workspace_tmp))
    try:
        export_root = root / "export"
        export_root.mkdir()
        test_sqlcipher_export_is_encrypted_verified_and_equivalent(export_root)
        guard_root = root / "guards"
        guard_root.mkdir()
        test_export_refuses_bad_keys_and_existing_targets(guard_root)
        test_backend_database_opens_use_the_central_connector()
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("[OK] central connector + disposable SQLCipher export proof")


if __name__ == "__main__":
    main()
