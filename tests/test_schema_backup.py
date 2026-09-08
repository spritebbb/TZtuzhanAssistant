# -*- coding: utf-8 -*-
"""Schema upgrades create a consistent snapshot that can be restored offline."""
from __future__ import annotations

import sqlite3
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.maintenance.schema_backup import (
    create_pre_upgrade_backup,
    mark_schema_current,
    restore_sqlite_backup,
    schema_version,
)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="tz_schema_backup_") as raw:
        root = Path(raw)
        database = root / "bot.db"
        conn = sqlite3.connect(database)
        conn.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        conn.execute("INSERT INTO marker VALUES ('before-upgrade')")
        conn.commit()
        conn.close()

        snapshot = create_pre_upgrade_backup(database, root / "backups", 1)
        assert snapshot is not None and snapshot.is_file()
        assert snapshot.parent.parent == root / "backups"

        conn = sqlite3.connect(database)
        conn.execute("DELETE FROM marker")
        conn.execute("ALTER TABLE marker ADD COLUMN migrated INTEGER NOT NULL DEFAULT 1")
        mark_schema_current(conn, 1)
        conn.commit()
        assert schema_version(conn) == 1
        conn.close()

        # Current schemas do not generate redundant pre-upgrade snapshots.
        assert create_pre_upgrade_backup(database, root / "backups", 1) is None

        restored = restore_sqlite_backup(snapshot, root / "restore" / "bot.db")
        conn = sqlite3.connect(restored)
        assert schema_version(conn) == 0
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "before-upgrade"
        columns = [row[1] for row in conn.execute("PRAGMA table_info(marker)")]
        assert columns == ["value"]
        conn.close()

        try:
            restore_sqlite_backup(snapshot, restored)
        except FileExistsError:
            pass
        else:
            raise AssertionError("restore must never overwrite an existing database")

        # Integration smoke: importing each database owner upgrades an existing
        # v0 file only after snapshotting it.  The marker proves the backup is a
        # real pre-migration image rather than an empty placeholder.
        runtime = root / "runtime"
        runtime.mkdir()
        versions = {"bot.db": 24, "sessions.db": 1, "agent_tasks.db": 1}
        for name in versions:
            conn = sqlite3.connect(runtime / name)
            conn.execute("CREATE TABLE pre_upgrade_marker (value TEXT NOT NULL)")
            conn.execute("INSERT INTO pre_upgrade_marker VALUES (?)", (name,))
            conn.commit()
            conn.close()

        os.environ["TZTUZHAN_DATA_DIR"] = str(runtime)
        os.environ["MEMORY_V2"] = "0"
        os.environ["MEMORY_MEM0"] = "0"
        from backend.core.userdb import db
        from backend.session import store  # noqa: F401
        from backend.agent import session  # noqa: F401

        for name, expected_version in versions.items():
            conn = sqlite3.connect(runtime / name)
            assert schema_version(conn) == expected_version
            if name == "bot.db":
                fact_columns = {row[1] for row in conn.execute("PRAGMA table_info(facts)")}
                assert {
                    "source_type", "source_message_ids", "confidence", "verified_at",
                    "expires_at", "pinned", "surface_policy",
                    "status", "conflicts_with_fact_id",
                } <= fact_columns
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                assert {
                    "activity_viewpoints", "activity_goals", "goal_progress",
                    "relationship_events", "artifacts", "future_letters",
                    "relationship_snapshots", "dual_perspectives",
                    "reunion_arcs",
                    "knowledge_opinions", "knowledge_opinion_sources",
                    "relationship_style_evidence", "memory_policy",
                    "memory_annotations", "first_occurrences",
                } <= tables
                # v6（M3.2 专注陪伴）：activities 复用计时字段，旧库自动补齐
                activity_columns = {row[1] for row in conn.execute("PRAGMA table_info(activities)")}
                assert {"planned_minutes", "remaining_seconds", "ends_at"} <= activity_columns
            conn.close()
            matches = list((runtime / "backups").glob(
                f"schema-{Path(name).stem}-v0-to-v{expected_version}-*/{name}"
            ))
            assert len(matches) == 1, f"missing pre-upgrade snapshot for {name}"
            conn = sqlite3.connect(matches[0])
            assert schema_version(conn) == 0
            assert conn.execute("SELECT value FROM pre_upgrade_marker").fetchone()[0] == name
            conn.close()

        from backend.maintenance.loop import backup
        periodic = backup()
        assert periodic is not None
        assert {path.name for path in periodic.glob("*.db")} == {
            "bot.db", "sessions.db", "agent_tasks.db",
        }

        db.conn.close()
        from backend.core.log import logger
        logger.remove()

    print("[OK] schema upgrade snapshot + integrity check + conservative restore")


if __name__ == "__main__":
    main()
