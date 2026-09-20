# -*- coding: utf-8 -*-
"""P3-04 F：加密备份与空目录/换机恢复回归（一次性临时数据，绝不触碰真实 data/）。

覆盖：
- 加密三库 + 加密对象 + 明文资源的成功快照，源库后续改动不串入备份；
- manifest 哈希/generation/key_id/恢复槽证据被篡改时拒绝；
- 错误主密钥、非空目标、缺恢复槽时拒绝；
- 中途失败不留成功快照，也不留 .partial-encrypted-*；
- 本机槽与换机恢复槽两条解锁路径，恢复时重建目标机本机槽；
- CLI list/verify/restore 的 dry-run 与 --apply 行为。
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.keyslots import master_key_id, read_local_slot, write_local_slot, write_recovery_slot  # noqa: E402
from backend.maintenance.backup_manifest import create_periodic_backup  # noqa: E402
from backend.maintenance.encrypted_backup import (  # noqa: E402
    EncryptedBackupError,
    create_encrypted_periodic_backup,
    load_encrypted_manifest,
    valid_encrypted_backups,
)
from backend.maintenance.encrypted_backup import restore_encrypted_backup  # noqa: E402
from backend.storage.connect import (  # noqa: E402
    SQLITE_HEADER,
    connect_database,
    create_encrypted_copy,
    verify_encrypted_database,
)
from backend.storage.file_container import decrypt_to_bytes, encrypt_file  # noqa: E402
from scripts.restore_backup import (  # noqa: E402
    _encrypted_verify_or_restore,
    main as restore_cli,
)

KEY = bytes(range(32))
# 仅用于测试的假口令；真实恢复口令绝不进入仓库、日志或命令行。
PASSPHRASE = "test-only-passphrase-32"
ENCRYPTED_DIRS = (("imgs", "media"), ("screenshots", "media"), ("documents", "attachment"))


def _encrypted_db(path: Path, value: str, *, key: bytes = KEY) -> None:
    plain = path.with_suffix(".plain-tmp")
    conn = connect_database(plain)
    conn.executescript(
        f"""
        CREATE TABLE marker(value TEXT NOT NULL);
        INSERT INTO marker(value) VALUES ('{value}');
        PRAGMA user_version = 42;
        """
    )
    conn.commit()
    conn.close()
    create_encrypted_copy(plain, path, key)
    plain.unlink()


def _fixture(root: Path) -> tuple[Path, Path, Path]:
    """返回 (data_dir, backup_root, recovery_slot)。数据目录为已加密的真实布局。"""
    data = root / "data"
    keyslots = data / "keyslots"
    keyslots.mkdir(parents=True)
    for name in ("bot.db", "sessions.db", "agent_tasks.db"):
        _encrypted_db(data / name, name)
    for dirname, domain in ENCRYPTED_DIRS:
        target_dir = data / dirname
        target_dir.mkdir()
        source = root / f"{dirname}-source.bin"
        source.write_bytes(f"私密内容：{dirname}".encode("utf-8"))
        encrypt_file(source, target_dir, KEY, key_id=master_key_id(KEY), domain=domain)
        source.unlink()
    (data / "personas" / "default").mkdir(parents=True)
    (data / "personas" / "default" / "card.md").write_text("# 人格卡", encoding="utf-8")
    (data / "feature_flags.json").write_text("{}", encoding="utf-8")
    write_local_slot(keyslots, KEY)
    write_recovery_slot(keyslots, KEY, PASSPHRASE, PASSPHRASE)
    return data, data / "backups", keyslots / "recovery.json"


def _plaintext_data(root: Path) -> Path:
    data = root / "plain-data"
    data.mkdir(parents=True)
    for name in ("bot.db", "sessions.db", "agent_tasks.db"):
        conn = connect_database(data / name)
        conn.executescript("CREATE TABLE marker(value TEXT); INSERT INTO marker VALUES ('plain');")
        conn.commit()
        conn.close()
    (data / "personas").mkdir()
    (data / "personas" / "card.md").write_text("# 明文人格卡", encoding="utf-8")
    return data


def test_encrypted_snapshot_is_consistent_and_restores_into_empty_dir(root: Path) -> None:
    data, backups, _slot = _fixture(root)
    folder = create_encrypted_periodic_backup(data, backups, master_key=KEY, keep=7, now=1_700_000_000)
    manifest = load_encrypted_manifest(folder, verify_files=True)
    assert manifest["key_id"] == master_key_id(KEY)
    assert {item["path"] for item in manifest["files"] if item["kind"] == "sqlcipher"} == {
        "bot.db", "sessions.db", "agent_tasks.db"
    }
    assert manifest["scope"]["recovery"]["slot_in_backup"] is False
    assert manifest["scope"]["recovery"]["required"] is True
    # 备份包内绝不携带本机槽或恢复槽本体
    assert not (folder / "keyslots").exists()
    assert (folder / "bot.db").read_bytes()[:16] != SQLITE_HEADER
    assert (folder / "personas" / "default" / "card.md").is_file()

    # 快照之后源库继续写入：恢复结果必须回到快照时刻
    conn = connect_database(data / "bot.db", encrypted_key=KEY)
    conn.execute("INSERT INTO marker(value) VALUES ('mutated-after-snapshot')")
    conn.commit()
    conn.close()

    target = root / "restored-data"
    result = restore_encrypted_backup(folder, target, master_key=KEY, apply=True)
    assert result["applied"] is True and result["files"] == len(manifest["files"])
    conn = connect_database(target / "bot.db", encrypted_key=KEY)
    rows = [row[0] for row in conn.execute("SELECT value FROM marker")]
    conn.close()
    assert rows == ["bot.db"], rows
    sqlcipher_manifest = next(
        item["database_manifest"] for item in manifest["files"] if item["path"] == "bot.db"
    )
    assert verify_encrypted_database(target / "bot.db", KEY)["manifest"] == sqlcipher_manifest
    container = next((target / "imgs").glob("*.tzenc"))
    plain, _info = decrypt_to_bytes(container, KEY, expected_domain="media")
    assert plain == "私密内容：imgs".encode("utf-8")
    assert (target / "personas" / "default" / "card.md").read_text(encoding="utf-8") == "# 人格卡"
    # 未要求重建本机槽时，目标目录里不应出现 DPAPI 槽
    assert not (target / "keyslots" / "local.dpapi").exists()

    try:
        restore_encrypted_backup(folder, target, master_key=KEY, apply=True)
    except EncryptedBackupError as exc:
        assert "为空目录" in str(exc)
    else:
        raise AssertionError("非空目标必须拒绝覆盖")


def test_tampered_manifest_and_wrong_key_are_rejected(root: Path) -> None:
    data, backups, _slot = _fixture(root)
    folder = create_encrypted_periodic_backup(data, backups, master_key=KEY, now=1_700_000_000)

    try:
        restore_encrypted_backup(folder, root / "wrong-key", master_key=b"x" * 32, apply=False)
    except EncryptedBackupError as exc:
        assert "key_id" in str(exc)
    else:
        raise AssertionError("错误主密钥必须拒绝")

    hash_copy = root / "tamper-hash"
    shutil.copytree(folder, hash_copy)
    with (hash_copy / "bot.db").open("ab") as handle:
        handle.write(b"\x00")
    try:
        load_encrypted_manifest(hash_copy, verify_files=True)
    except EncryptedBackupError as exc:
        assert "校验和不匹配" in str(exc)
    else:
        raise AssertionError("被篡改的备份文件必须拒绝")

    missing_copy = root / "tamper-missing"
    shutil.copytree(folder, missing_copy)
    victim = next(item["path"] for item in load_encrypted_manifest(missing_copy)["files"]
                  if item["kind"] == "plaintext_resource")
    (missing_copy / victim).unlink()
    try:
        load_encrypted_manifest(missing_copy, verify_files=True)
    except EncryptedBackupError as exc:
        assert "缺失或路径不安全" in str(exc)
    else:
        raise AssertionError("缺文件的备份必须拒绝")

    generation_copy = root / "tamper-generation"
    shutil.copytree(folder, generation_copy)
    _edit_manifest(generation_copy, lambda value: value["files"][0].__setitem__("generation", "other"))
    try:
        load_encrypted_manifest(generation_copy, verify_files=True)
    except EncryptedBackupError as exc:
        assert "generation" in str(exc)
    else:
        raise AssertionError("generation 不一致必须拒绝")

    key_id_copy = root / "tamper-key-id"
    shutil.copytree(folder, key_id_copy)
    _edit_manifest(key_id_copy, lambda value: value["scope"]["recovery"].__setitem__("slot_key_id", "deadbeefdeadbeef"))
    try:
        load_encrypted_manifest(key_id_copy, verify_files=True)
    except EncryptedBackupError as exc:
        assert "恢复槽证据" in str(exc)
    else:
        raise AssertionError("恢复槽 key_id 不一致必须拒绝")


def test_recovery_slot_unlocks_and_rebuilds_local_slot(root: Path) -> None:
    data, backups, recovery_slot = _fixture(root)
    folder = create_encrypted_periodic_backup(data, backups, master_key=KEY, now=1_700_000_000)
    local_slot = data / "keyslots" / "local.dpapi"
    target = root / "new-machine-data"

    # 同机：本机槽即可 dry-run 校验
    assert _encrypted_verify_or_restore(
        folder, target, apply=False, local_slot=local_slot, recovery_slot=None
    )["applied"] is False
    assert not target.exists()

    # 换机：恢复槽指纹不符 / 口令错误都必须失败，且不留目标目录
    bogus = root / "bogus-recovery.json"
    bogus.write_text(recovery_slot.read_text(encoding="utf-8") + " ", encoding="utf-8")
    try:
        _encrypted_verify_or_restore(
            folder, target, apply=False, local_slot=None, recovery_slot=bogus
        )
    except ValueError as exc:
        assert "不匹配" in str(exc)
    else:
        raise AssertionError("恢复槽指纹不符必须拒绝")
    with patch("scripts.restore_backup.getpass.getpass", return_value="wrong-passphrase"):
        try:
            _encrypted_verify_or_restore(
                folder, target, apply=True, local_slot=None, recovery_slot=recovery_slot
            )
        except ValueError as exc:
            assert "口令" in str(exc)
        else:
            raise AssertionError("错误恢复口令必须拒绝")
    assert not target.exists()

    # 正确恢复口令 + 空目标：--apply 语义下重建目标机本机槽
    with patch("scripts.restore_backup.getpass.getpass", return_value=PASSPHRASE):
        result = _encrypted_verify_or_restore(
            folder, target, apply=True, local_slot=None, recovery_slot=recovery_slot
        )
    assert result["rebuild_local_slot"] is True and result["applied"] is True
    assert read_local_slot(target / "keyslots") == KEY


def test_failure_paths_leave_no_success_snapshot(root: Path) -> None:
    data, backups, _slot = _fixture(root)

    # 缺恢复槽：拒绝创建不可恢复的「成功」备份
    missing = root / "missing-slot"
    data2, backups2, slot2 = _fixture(missing)
    slot2.unlink()
    try:
        create_encrypted_periodic_backup(data2, backups2, master_key=KEY)
    except EncryptedBackupError as exc:
        assert "恢复槽" in str(exc)
    else:
        raise AssertionError("缺恢复槽必须拒绝创建")
    assert valid_encrypted_backups(backups2) == []
    assert not list(backups2.glob(".partial-encrypted-*"))

    # 中途失败（第 2 个库注入异常）：不留成功 manifest，也不留 partial
    from backend.maintenance import encrypted_backup as engine

    real_backup = engine._encrypted_online_backup
    calls = {"n": 0}

    def flaky(source, target, *, master_key):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated encrypted backup failure")
        return real_backup(source, target, master_key=master_key)

    with patch("backend.maintenance.encrypted_backup._encrypted_online_backup", new=flaky):
        try:
            create_encrypted_periodic_backup(data, backups, master_key=KEY)
        except RuntimeError as exc:
            assert "simulated" in str(exc)
        else:
            raise AssertionError("注入失败必须向上抛出")
    assert valid_encrypted_backups(backups) == []
    assert not list(backups.glob(".partial-encrypted-*"))


def test_cli_lists_verifies_and_restores_both_families(root: Path) -> None:
    data, backups, recovery_slot = _fixture(root)
    folder = create_encrypted_periodic_backup(data, backups, master_key=KEY, now=1_700_000_000)
    plain_backup = create_periodic_backup(_plaintext_data(root), backups, now=1_700_000_100)
    local_slot = data / "keyslots" / "local.dpapi"

    out = _run(["--backup-root", str(backups), "list"])
    assert "encrypted" in out and "plaintext" in out
    assert str(folder.name) in out and str(plain_backup.name) in out

    out = _run(["--backup-root", str(backups), "verify", str(folder), "--local-slot", str(local_slot)])
    assert "encrypted" in out and "OK" in out

    assert _run_rc([
        "--backup-root", str(backups), "verify", str(folder),
        "--local-slot", str(local_slot), "--recovery-slot", str(recovery_slot),
    ]) == 2
    assert _run_rc(["--backup-root", str(backups), "verify", str(plain_backup), "--local-slot", str(local_slot)]) == 2

    target = root / "cli-restored-data"
    out = _run([
        "--backup-root", str(backups), "restore", str(folder),
        "--target", str(target), "--local-slot", str(local_slot),
    ])
    assert "DRY-RUN" in out and not target.exists()

    with patch("scripts.restore_backup.getpass.getpass", return_value=PASSPHRASE):
        out = _run([
            "--backup-root", str(backups), "restore", str(folder),
            "--target", str(target), "--apply", "--recovery-slot", str(recovery_slot),
        ])
    assert "RESTORED" in out and (target / "bot.db").is_file()
    assert read_local_slot(target / "keyslots") == KEY

    plain_target = root / "cli-plain-target"
    out = _run([
        "--backup-root", str(backups), "restore", str(plain_backup),
        "--target", str(plain_target), "--apply",
    ])
    assert "RESTORED" in out and (plain_target / "data" / "bot.db").is_file()


def _edit_manifest(folder: Path, mutate) -> None:
    path = folder / "manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _run(argv: list[str]) -> str:
    stream = io.StringIO()
    with redirect_stdout(stream), redirect_stderr(stream):
        rc = restore_cli(argv)
    assert rc == 0, f"CLI 退出码 {rc}: {stream.getvalue()}"
    return stream.getvalue()


def _run_rc(argv: list[str]) -> int:
    stream = io.StringIO()
    with redirect_stdout(stream), redirect_stderr(stream):
        return restore_cli(argv)


def main() -> None:  # noqa: F811 - 本文件脚本入口（模块作用域独立）
    workspace_tmp = Path(__file__).resolve().parents[1] / ".tmp"
    workspace_tmp.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="encrypted-backup-", dir=workspace_tmp))
    try:
        for name, case in (
            ("roundtrip", test_encrypted_snapshot_is_consistent_and_restores_into_empty_dir),
            ("tamper", test_tampered_manifest_and_wrong_key_are_rejected),
            ("recovery", test_recovery_slot_unlocks_and_rebuilds_local_slot),
            ("failure", test_failure_paths_leave_no_success_snapshot),
            ("cli", test_cli_lists_verifies_and_restores_both_families),
        ):
            case(root / name)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("[OK] encrypted backup + empty-dir/new-machine restore")


if __name__ == "__main__":
    main()