# -*- coding: utf-8 -*-
"""Q4 去敏旧库升级、幂等、备份与中断恢复验证。"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
FIXTURE = ROOT / "tests" / "fixtures" / "legacy" / "v40_watch.sql"
TARGET_VERSION = 42


def _root() -> Path:
    return Path(tempfile.mkdtemp(prefix="tztuzhan-old-db-upgrade-"))


def _seed(data_root: Path) -> Path:
    data_root.mkdir(parents=True, exist_ok=True)
    path = data_root / "bot.db"
    conn = sqlite3.connect(path)
    conn.executescript(FIXTURE.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    return path


def _run_import(
    data_root: Path,
    code: str = "from backend.core.userdb import db\ndb.ensure_user('x')",
) -> None:
    # ensure_user 触发 _prepare_schema：db 连接自懒加载重构后不再在 import 时建立
    env = os.environ.copy()
    env.update({
        "TZTUZHAN_DATA_DIR": str(data_root),
        "MEMORY_V2": "0",
        "MEMORY_MEM0": "0",
        "MEMORY_EMBED_FORCE": "1",
    })
    proc = subprocess.run(
        [str(PYTHON), "-X", "utf8", "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, (proc.stdout + "\n" + proc.stderr)[-3000:]


def _version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def test_v40_upgrade_and_repeat_idempotency() -> tuple[Path, Path]:
    data_root = _root()
    db_path = _seed(data_root)
    _run_import(data_root)

    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(watches)")}
    row = conn.execute(
        "SELECT last_hash,last_notified_hash FROM watches WHERE user_id='fixture-user'"
    ).fetchone()
    assert _version(conn) == TARGET_VERSION
    assert "last_notified_hash" in columns and row == ("fixture-hash", "fixture-hash")
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    before_count = conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0]
    conn.close()

    backups = list((data_root / "backups").glob("schema-bot-v40-to-v42-*/bot.db"))
    assert len(backups) == 1, backups
    old = sqlite3.connect(backups[0])
    assert _version(old) == 40
    assert "last_notified_hash" not in {
        row[1] for row in old.execute("PRAGMA table_info(watches)")
    }
    old.close()

    _run_import(data_root)
    after_backups = list((data_root / "backups").glob("schema-bot-v40-to-v42-*/bot.db"))
    conn = sqlite3.connect(db_path)
    assert len(after_backups) == 1
    assert _version(conn) == TARGET_VERSION
    assert conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == before_count
    conn.close()
    print("[PASS] v40→v41 数据/回填/版本/幂等升级")
    return data_root, backups[0]


def test_interrupted_target_restores_from_verified_snapshot(snapshot: Path) -> None:
    from backend.maintenance.schema_backup import restore_sqlite_backup

    folder = _root()
    interrupted = folder / "interrupted.db"
    shutil.copy2(snapshot, interrupted)
    with interrupted.open("r+b") as handle:
        handle.truncate(128)
    broken = sqlite3.connect(interrupted)
    try:
        broken.execute("PRAGMA integrity_check").fetchone()
        raise AssertionError("截断库不应通过完整性检查")
    except sqlite3.DatabaseError:
        pass
    finally:
        broken.close()
    interrupted.unlink()
    restored = restore_sqlite_backup(snapshot, interrupted)
    conn = sqlite3.connect(restored)
    assert _version(conn) == 40
    assert conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 1
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()
    print("[PASS] 中断目标不损坏升级前快照，可验证恢复")


def test_reset_after_upgrade(data_root: Path) -> None:
    _run_import(data_root, "from backend.core.userdb import db; db.reset()")
    conn = sqlite3.connect(data_root / "bot.db")
    assert _version(conn) == TARGET_VERSION
    assert conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 0
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()
    print("[PASS] 升级后 reset 保持当前 schema 且清空业务行")


def main() -> None:
    data_root, snapshot = test_v40_upgrade_and_repeat_idempotency()
    test_interrupted_target_restores_from_verified_snapshot(snapshot)
    test_reset_after_upgrade(data_root)
    print("\nQ4 old database upgrade: 3/3 passed")


if __name__ == "__main__":
    main()
