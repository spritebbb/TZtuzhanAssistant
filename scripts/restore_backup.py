"""列出、校验或恢复 P0 每日备份。默认 restore 只演练，不写目标目录。"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.maintenance.backup_manifest import DB_NAMES, load_manifest, valid_backups  # noqa: E402


def _choose(root: Path, value: str | None) -> Path:
    if value:
        folder = Path(value).resolve()
    else:
        items = valid_backups(root)
        if not items:
            raise ValueError("没有可用的成功备份")
        folder = items[-1][0].resolve()
    load_manifest(folder)
    return folder


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-root", type=Path, default=ROOT / "data" / "backups")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("snapshot", nargs="?")
    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("snapshot", nargs="?")
    restore_parser.add_argument("--target", type=Path, required=True)
    restore_parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    root = args.backup_root.resolve()
    try:
        if args.command == "list":
            for folder, manifest in valid_backups(root):
                print(f"{folder}\t{manifest['completed_at']}\t{len(manifest['files'])} files")
            return 0
        folder = _choose(root, args.snapshot)
        if args.command == "verify":
            manifest = load_manifest(folder, verify_files=True)
            print(f"OK {folder} ({len(manifest['files'])} files)")
            return 0
        plan = restore(folder, args.target.resolve(), apply=args.apply)
        mode = "RESTORED" if args.apply else "DRY-RUN"
        print(f"{mode} {folder} -> {args.target.resolve()} ({len(plan)} files)")
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
