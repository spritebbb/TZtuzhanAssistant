# -*- coding: utf-8 -*-
"""SQLite schema-upgrade snapshots and conservative offline restore helpers."""
from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path


def schema_version(conn: sqlite3.Connection) -> int:
    """Return ``PRAGMA user_version`` as an integer."""
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def mark_schema_current(conn: sqlite3.Connection, target_version: int) -> None:
    """Mark a successfully migrated database with its application schema version."""
    version = int(target_version)
    if version < 1:
        raise ValueError("target_version must be positive")
    conn.execute(f"PRAGMA user_version = {version}")


def _integrity_check(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if not row or str(row[0]).lower() != "ok":
            raise sqlite3.DatabaseError(f"integrity_check failed for {path.name}: {row}")
    finally:
        conn.close()


def create_pre_upgrade_backup(
    database: Path,
    backup_root: Path,
    target_version: int,
) -> Path | None:
    """Create an online snapshot before an older SQLite schema is upgraded.

    Fresh databases and databases already at ``target_version`` are skipped. The
    caller must only mark the target version after every migration succeeds.
    """
    database = Path(database)
    target = int(target_version)
    if target < 1:
        raise ValueError("target_version must be positive")
    if not database.is_file() or database.stat().st_size == 0:
        return None

    source = sqlite3.connect(str(database), timeout=5)
    try:
        current = schema_version(source)
        if current >= target:
            return None

        backup_root = Path(backup_root)
        backup_root.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        folder = backup_root / (
            f"schema-{database.stem}-v{current}-to-v{target}-{stamp}-{uuid.uuid4().hex[:8]}"
        )
        folder.mkdir(parents=False, exist_ok=False)
        snapshot = folder / database.name

        target_conn = sqlite3.connect(str(snapshot))
        try:
            source.backup(target_conn)
        finally:
            target_conn.close()
    finally:
        source.close()

    _integrity_check(snapshot)
    return snapshot


def restore_sqlite_backup(snapshot: Path, destination: Path) -> Path:
    """Restore a verified snapshot to a new path without overwriting data."""
    snapshot = Path(snapshot)
    destination = Path(destination)
    if not snapshot.is_file():
        raise FileNotFoundError(snapshot)
    if destination.exists():
        raise FileExistsError(destination)

    _integrity_check(snapshot)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(str(snapshot), timeout=5)
    target = sqlite3.connect(str(destination))
    try:
        source.backup(target)
    except Exception:
        target.close()
        if destination.exists():
            destination.unlink()
        raise
    else:
        target.close()
    finally:
        source.close()

    _integrity_check(destination)
    return destination
