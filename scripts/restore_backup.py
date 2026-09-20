"""列出、校验或恢复 P0 每日备份（明文 periodic-* 与 P3-04 F 加密 encrypted-periodic-*）。

用法：
  python scripts/restore_backup.py list
  python scripts/restore_backup.py verify [snapshot] [--local-slot SLOT | --recovery-slot SLOT]
  python scripts/restore_backup.py restore [snapshot] --target DIR [--apply]

约定：
- restore 默认 dry-run，只有 --apply 才写目标；目标必须不存在或为空目录。
- 加密备份的主密钥只从本机 DPAPI 槽或交互式恢复口令取得，
  不接受命令行/环境变量传口令，也不把任何密钥写进日志。
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.keyslots import KeySlotError, read_local_slot, read_recovery_slot  # noqa: E402
from backend.maintenance.backup_manifest import DB_NAMES, load_manifest, valid_backups  # noqa: E402
from backend.maintenance.encrypted_backup import (  # noqa: E402
    BACKUP_KIND,
    EncryptedBackupError,
    load_encrypted_manifest,
    restore_encrypted_backup,
    valid_encrypted_backups,
)


def _manifest_kind(folder: Path) -> str:
    try:
        value = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取备份 manifest：{folder}") from exc
    return "encrypted" if value.get("kind") == BACKUP_KIND else "plaintext"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _all_backups(root: Path) -> list[tuple[str, Path, dict]]:
    items = [("plaintext", folder, manifest) for folder, manifest in valid_backups(root)]
    items.extend(
        ("encrypted", folder, manifest)
        for folder, manifest in valid_encrypted_backups(root)
    )
    return sorted(
        items,
        key=lambda row: (
            float(row[2].get("completed_at_epoch", 0)),
            str(row[2].get("completed_at", "")),
        ),
    )


def _choose(root: Path, value: str | None) -> tuple[str, Path]:
    if value:
        folder = Path(value).resolve()
        kind = _manifest_kind(folder)
        if kind == "encrypted":
            load_encrypted_manifest(folder, verify_files=True)
        else:
            load_manifest(folder, verify_files=True)
        return kind, folder
    items = _all_backups(root)
    if not items:
        raise ValueError("没有可用的成功备份")
    kind, folder, _manifest = items[-1]
    return kind, folder.resolve()


def _default_local_slot() -> Path:
    data_dir = Path(os.environ.get("TZTUZHAN_DATA_DIR") or (ROOT / "data"))
    return data_dir / "keyslots" / "local.dpapi"


def _unlock_master_key(
    manifest: dict,
    *,
    local_slot: Path | None,
    recovery_slot: Path | None,
) -> bytes:
    evidence = manifest.get("scope", {}).get("recovery")
    if not isinstance(evidence, dict):
        raise ValueError("加密备份缺少恢复材料证据")
    if recovery_slot is not None:
        slot = Path(recovery_slot).resolve()
        if not slot.is_file():
            raise ValueError(f"恢复槽不存在：{slot}")
        if _sha256_file(slot) != str(evidence.get("slot_sha256") or ""):
            raise ValueError("恢复槽与备份 manifest 不匹配")
        passphrase = getpass.getpass("恢复口令: ")
        try:
            return read_recovery_slot(slot.parent, passphrase)
        except KeySlotError as exc:
            raise ValueError("恢复口令不正确或恢复槽损坏") from exc
    slot = Path(local_slot).resolve() if local_slot else _default_local_slot().resolve()
    if not slot.is_file():
        raise ValueError("没有可用本机槽或恢复槽；请提供 --local-slot 或 --recovery-slot")
    try:
        return read_local_slot(slot.parent)
    except KeySlotError as exc:
        raise ValueError("本机槽无法解锁；请提供换机恢复槽") from exc


def _encrypted_verify_or_restore(
    folder: Path,
    target: Path | None,
    *,
    apply: bool,
    local_slot: Path | None,
    recovery_slot: Path | None,
) -> dict:
    manifest = load_encrypted_manifest(folder, verify_files=True)
    master_key = _unlock_master_key(
        manifest, local_slot=local_slot, recovery_slot=recovery_slot
    )
    if target is None:
        return restore_encrypted_backup(
            folder, Path(os.devnull), master_key=master_key, apply=False
        )
    return restore_encrypted_backup(
        folder,
        target,
        master_key=master_key,
        rebuild_local_slot=bool(apply),
        apply=apply,
    )


def _restore_destination(target: Path, rel: Path) -> Path:
    if rel.parts and rel.parts[0] == "project":
        return target / rel
    return target / "data" / rel


def restore(folder: Path, target: Path, *, apply: bool = False) -> list[tuple[Path, Path]]:
    manifest = load_manifest(folder, verify_files=True)
    plan = []
    for item in manifest["files"]:
        rel = Path(item["path"])
        plan.append((folder / rel, _restore_destination(target, rel)))
    if not apply:
        return plan
    if target.exists() and any(target.iterdir()):
        raise ValueError("恢复目标必须不存在或为空目录")
    target.mkdir(parents=True, exist_ok=True)
    for source, destination in plan:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    for name in DB_NAMES:
        restored = target / "data" / name
        conn = sqlite3.connect(str(restored))
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            if not row or str(row[0]).lower() != "ok":
                raise sqlite3.DatabaseError(f"恢复后数据库校验失败：{name}")
        finally:
            conn.close()
    return plan


def _report_encrypted(args, folder: Path, local_slot: Path | None, recovery_slot: Path | None) -> int:
    if args.command == "verify":
        result = _encrypted_verify_or_restore(
            folder, None, apply=False, local_slot=local_slot, recovery_slot=recovery_slot
        )
        print(
            f"OK {folder} (encrypted, generation={result['generation']}, {result['files']} files)"
        )
        return 0
    target = args.target.resolve()
    result = _encrypted_verify_or_restore(
        folder, target, apply=args.apply, local_slot=local_slot, recovery_slot=recovery_slot
    )
    mode = "RESTORED" if args.apply else "DRY-RUN"
    print(f"{mode} {folder} -> {target} (encrypted, {result['files']} files)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-root", type=Path, default=ROOT / "data" / "backups")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")

    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("snapshot", nargs="?")
    verify_parser.add_argument("--local-slot", type=Path)
    verify_parser.add_argument("--recovery-slot", type=Path)

    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("snapshot", nargs="?")
    restore_parser.add_argument("--target", type=Path, required=True)
    restore_parser.add_argument("--apply", action="store_true")
    restore_parser.add_argument("--local-slot", type=Path)
    restore_parser.add_argument("--recovery-slot", type=Path)

    args = parser.parse_args(argv)
    root = args.backup_root.resolve()
    try:
        if args.command == "list":
            for kind, folder, manifest in _all_backups(root):
                count = len(manifest.get("files") or [])
                print(f"{kind}\t{folder}\t{manifest.get('completed_at')}\t{count} files")
            return 0

        local_slot = getattr(args, "local_slot", None)
        recovery_slot = getattr(args, "recovery_slot", None)
        if local_slot and recovery_slot:
            raise ValueError("--local-slot 与 --recovery-slot 互斥，请只提供恢复材料其一")

        kind, folder = _choose(root, getattr(args, "snapshot", None))
        if kind == "encrypted":
            return _report_encrypted(args, folder, local_slot, recovery_slot)
        if local_slot or recovery_slot:
            raise ValueError("明文备份不涉及密钥槽，无需 --local-slot/--recovery-slot")

        if args.command == "verify":
            manifest = load_manifest(folder, verify_files=True)
            print(f"OK {folder} ({len(manifest['files'])} files)")
            return 0
        plan = restore(folder, args.target.resolve(), apply=args.apply)
        mode = "RESTORED" if args.apply else "DRY-RUN"
        print(f"{mode} {folder} -> {args.target.resolve()} ({len(plan)} files)")
        return 0
    except (EncryptedBackupError, KeySlotError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
