"""P0-02：每日备份清单、校验、轮转与保守恢复。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from backend.maintenance.backup_manifest import (
    backup_due,
    create_periodic_backup,
    load_manifest,
    valid_backups,
)
from scripts.restore_backup import restore


def _database(path: Path, value: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE marker(value TEXT)")
    conn.execute("INSERT INTO marker VALUES (?)", (value,))
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    data = tmp_path / "data"
    backups = data / "backups"
    data.mkdir()
    for name in ("bot.db", "sessions.db", "agent_tasks.db"):
        _database(data / name, name)
    for dirname in ("imgs", "screenshots", "personas", "documents"):
        folder = data / dirname
        folder.mkdir()
        (folder / f"{dirname}.txt").write_text(dirname, encoding="utf-8")
    persona = tmp_path / "persona.md"
    persona.write_text("# 菟菚", encoding="utf-8")
    return data, backups, persona


def suite_manifest_due_integrity_and_scope(tmp_path: Path) -> None:
    data, backups, persona = _fixture(tmp_path)
    assert backup_due(backups, now=1000)
    folder = create_periodic_backup(data, backups, persona_file=persona, now=1000)
    manifest = load_manifest(folder, verify_files=True)
    assert manifest["format_version"] == 1 and manifest["status"] == "success"
    assert set(manifest["scope"]["databases"]) == {"bot.db", "sessions.db", "agent_tasks.db"}
    assert "not a cross-database atomic snapshot" in manifest["scope"]["consistency"]
    assert manifest["scope"]["chroma"] == "excluded_rebuildable"
    assert not backup_due(backups, now=1001)
    assert backup_due(backups, now=1000 + 86400)

    first_file = folder / manifest["files"][0]["path"]
    first_file.write_bytes(b"corrupt")
    try:
        load_manifest(folder, verify_files=True)
    except ValueError as exc:
        assert "mismatch" in str(exc)
    else:
        raise AssertionError("损坏文件必须校验失败")


def suite_failure_leaves_no_success_snapshot(tmp_path: Path) -> None:
    data, backups, persona = _fixture(tmp_path)
    (data / "agent_tasks.db").unlink()
    try:
        create_periodic_backup(data, backups, persona_file=persona)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("缺少必需数据库必须失败")
    assert valid_backups(backups) == []
    assert not list(backups.glob(".partial-*"))


def suite_rotation_ignores_schema_and_restore_is_dry_by_default(tmp_path: Path) -> None:
    data, backups, persona = _fixture(tmp_path)
    schema = backups / "schema-bot-v1-to-v2-example"
    schema.mkdir(parents=True)
    for i in range(4):
        create_periodic_backup(data, backups, persona_file=persona, keep=2, now=1000 + i)
    assert len(valid_backups(backups)) == 2
    assert schema.exists()

    latest = valid_backups(backups)[-1][0]
    target = tmp_path / "restored"
    plan = restore(latest, target, apply=False)
    assert plan and not target.exists()
    restore(latest, target, apply=True)
    for name in ("bot.db", "sessions.db", "agent_tasks.db"):
        conn = sqlite3.connect(target / "data" / name)
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        conn.close()
    try:
        restore(latest, target, apply=True)
    except ValueError as exc:
        assert "为空目录" in str(exc)
    else:
        raise AssertionError("非空目标必须拒绝覆盖")
