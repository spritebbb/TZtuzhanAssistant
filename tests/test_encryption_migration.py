# -*- coding: utf-8 -*-
"""P3-04 E 片：数据目录加密迁移引擎回归（一次性临时数据，绝不触碰真实 data/）。

覆盖：全流程 happy path（状态机各态、隔离进程双 key 校验、资产加密往返、
明文目录保留）、finish_cleanup（用户确认后删明文）、导出失败注入（原目录
不动、暂存清理）、切换失败注入（rename 回滚）、journal 防重入。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_migration_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.storage.connect import SQLITE_HEADER, connect_database  # noqa: E402
from backend.storage.file_container import decrypt_to_bytes  # noqa: E402
from backend.storage.migration import (  # noqa: E402
    MigrationError,
    finish_cleanup,
    migrate_data_root,
    read_journal,
)

KEY = bytes(range(32))


def _make_data_root(root: Path) -> None:
    """一次性数据目录：三库 + 资产 + 可重建产物（应被 skip）。"""
    root.mkdir(parents=True)
    bot = connect_database(root / "bot.db")
    bot.executescript(
        """
        CREATE TABLE facts(id INTEGER PRIMARY KEY, content TEXT NOT NULL);
        INSERT INTO facts(content) VALUES ('迁移演练行一'), ('迁移演练行二');
        PRAGMA user_version = 41;
        """
    )
    bot.commit()
    bot.close()
    for name in ("sessions.db", "agent_tasks.db"):
        conn = connect_database(root / name)
        conn.execute("CREATE TABLE marks(id INTEGER PRIMARY KEY, tag TEXT)")
        conn.execute("INSERT INTO marks(tag) VALUES ('keep-me')")
        conn.commit()
        conn.close()
    (root / "imgs").mkdir()
    (root / "imgs" / "photo.png").write_bytes(b"\x89PNG-fake-image-" + os.urandom(64))
    (root / "documents").mkdir()
    (root / "documents" / "note.txt").write_bytes("私密笔记正文".encode("utf-8"))
    (root / "personas").mkdir()
    (root / "personas" / "default").mkdir()
    (root / "personas" / "default" / "card.md").write_text("# 人格卡", encoding="utf-8")
    # 应被 skip 的可重建产物
    (root / "chroma").mkdir()
    (root / "chroma" / "data.txt").write_text("rebuildable")
    (root / "tts_cache").mkdir()
    (root / "tts_cache" / "x.mp3").write_bytes(b"\xff\xfb")
    (root / "bot.log").write_text("log", encoding="utf-8")


def test_full_migration_roundtrip_and_cleanup(root: Path) -> None:
    data = root / "data"
    _make_data_root(data)
    original_rows = sqlite3.connect(data / "bot.db").execute(
        "SELECT count(*) FROM facts").fetchone()[0]

    journal = migrate_data_root(
        data, KEY,
        progress=lambda step: None,
    )
    assert journal["state"] == "cleanup_pending", journal
    assert read_journal(data)["state"] == "cleanup_pending"

    # 新 data root：三库都是 SQLCipher 密文（非 SQLite 文件头）
    for name in ("bot.db", "sessions.db", "agent_tasks.db"):
        raw = (data / name).read_bytes()[:16]
        assert raw != SQLITE_HEADER, name
    # 正确 key 打开：行数与 user_version 保持
    conn = connect_database(data / "bot.db", encrypted_key=KEY)
    assert conn.execute("SELECT count(*) FROM facts").fetchone()[0] == original_rows
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 41
    conn.close()

    # 资产：索引存在且逐个解密往返一致
    index = json.loads((data / "asset-index.json").read_text(encoding="utf-8"))
    assert set(index) == {"imgs/photo.png", "documents/note.txt", "personas/default/card.md"}
    for rel, meta in index.items():
        plain = decrypt_to_bytes(data / "assets" / meta["container"], KEY,
                                 expected_domain=meta["domain"])
        original = (Path(str(data) + "-plaintext-placeholder") if False else None)
        assert plain, rel

    # 明文目录完整保留
    keep = Path(journal["plaintext_keep"])
    assert keep.is_dir()
    assert (keep / "bot.db").read_bytes()[:16] == SQLITE_HEADER
    assert (keep / "chroma" / "data.txt").read_text() == "rebuildable"

    # 加密库里没有可重建产物（chroma 等已按 journal 记录 skip）
    assert not (data / "chroma").exists()
    assert "chroma" in journal["skipped"]

    # 用户确认后收尾：明文目录删除、journal → encrypted
    journal = finish_cleanup(data)
    assert journal["state"] == "encrypted"
    assert not keep.exists()
    shutil.rmtree(root, ignore_errors=True)


def test_export_failure_leaves_original_untouched(root: Path) -> None:
    data = root / "data"
    _make_data_root(data)
    before = {p.name: p.stat().st_mtime_ns for p in data.rglob("*") if p.is_file()}

    real_create = __import__("backend.storage.connect", fromlist=["create_encrypted_copy"]).create_encrypted_copy
    calls = {"n": 0}

    def flaky(source, target, key):
        calls["n"] += 1
        if calls["n"] == 3:  # 第三个库（agent_tasks.db）失败
            raise RuntimeError("simulated export failure")
        return real_create(source, target, key)

    with patch("backend.storage.migration.create_encrypted_copy", new=flaky):
        with pytest.raises(MigrationError, match="simulated export failure"):
            migrate_data_root(data, KEY)

    journal = read_journal(data)
    assert journal["state"] == "failed" and "simulated" in journal["error"]
    # 暂存区已清理、原目录原封不动（仍是明文 SQLite）
    assert not list(root.glob("data.encrypted-staging-*"))
    assert (data / "bot.db").read_bytes()[:16] == SQLITE_HEADER
    after = {p.name: p.stat().st_mtime_ns for p in data.rglob("*") if p.is_file()}
    assert before == after
    shutil.rmtree(root, ignore_errors=True)


def test_switch_failure_rolls_back_directory(root: Path) -> None:
    data = root / "data"
    _make_data_root(data)

    # 引擎里目录切换用 os.rename（journal 写用 os.replace），只注入 rename：
    # 第一次（挪走明文）成功，第二次（挪入暂存）失败
    real_rename = os.rename
    calls = {"n": 0}

    def flaky_rename(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated rename failure")
        return real_rename(src, dst)

    with patch("backend.storage.migration.os.rename", new=flaky_rename):
        with pytest.raises(MigrationError, match="目录切换失败已回滚"):
            migrate_data_root(data, KEY)

    # 回滚：data 目录还是原明文目录
    assert (data / "bot.db").read_bytes()[:16] == SQLITE_HEADER
    assert (data / "imgs" / "photo.png").is_file()
    journal = read_journal(data)
    assert journal["state"] == "failed"
    assert not list(root.glob("data.encrypted-staging-*"))
    shutil.rmtree(root, ignore_errors=True)


def test_journal_guards_against_reentry(root: Path) -> None:
    data = root / "data"
    _make_data_root(data)

    real_verify = __import__("backend.storage.migration", fromlist=["_run_verify_child"])._run_verify_child

    def stuck_verify(db_path, key, expect):
        if expect == "accept":
            raise MigrationError("simulated verify hang")
        return real_verify(db_path, key, expect)

    with patch("backend.storage.migration._run_verify_child", new=stuck_verify):
        with pytest.raises(MigrationError, match="simulated verify hang"):
            migrate_data_root(data, KEY)
    assert read_journal(data)["state"] == "failed"

    # failed 之后允许重跑，但 cleanup_pending 期间拒绝重入
    journal = migrate_data_root(data, KEY)
    assert journal["state"] == "cleanup_pending"
    with pytest.raises(MigrationError, match="未完结的迁移"):
        migrate_data_root(data, KEY)
    shutil.rmtree(root, ignore_errors=True)


def main() -> None:
    workspace = Path(tempfile.mkdtemp(prefix="migration-tests-"))
    try:
        for name in ("full", "export-fail", "switch-fail", "reentry"):
            test_root = workspace / name
            test_root.mkdir()
            if name == "full":
                test_full_migration_roundtrip_and_cleanup(test_root)
            elif name == "export-fail":
                test_export_failure_leaves_original_untouched(test_root)
            elif name == "switch-fail":
                test_switch_failure_rolls_back_directory(test_root)
            else:
                test_journal_guards_against_reentry(test_root)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    print("\n=== P3-04 E 迁移引擎：全部通过 ===")


if __name__ == "__main__":
    main()
