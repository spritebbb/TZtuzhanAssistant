"""可验证的每日备份：在线 SQLite 快照、资源文件、清单与轮转。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

from ..storage.connect import connect_database

FORMAT_VERSION = 1
DB_NAMES = ("bot.db", "sessions.db", "agent_tasks.db")
RESOURCE_DIRS = ("imgs", "screenshots", "personas", "documents")
PERIODIC_PREFIX = "periodic-"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _integrity(path: Path) -> int:
    conn = connect_database(path, timeout=10)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if not row or str(row[0]).lower() != "ok":
            raise sqlite3.DatabaseError(f"integrity_check failed: {path.name}: {row}")
        version = conn.execute("PRAGMA user_version").fetchone()
        return int(version[0]) if version else 0
    finally:
        conn.close()


def _online_backup(source: Path, target: Path) -> int:
    source_conn = connect_database(source, timeout=10)
    target_conn = connect_database(target)
    try:
        source_conn.execute("PRAGMA busy_timeout = 10000")
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()
    return _integrity(target)


def _entry(path: Path, root: Path, *, kind: str, schema_version: int | None = None) -> dict:
    item = {
        "path": path.relative_to(root).as_posix(),
        "kind": kind,
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }
    if schema_version is not None:
        item["schema_version"] = schema_version
    return item


def _copy_tree(source: Path, target: Path, root: Path, files: list[dict], kind: str) -> int:
    if not source.is_dir():
        return 0
    count = 0
    for src in sorted(p for p in source.rglob("*") if p.is_file()):
        rel = src.relative_to(source)
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        files.append(_entry(dst, root, kind=kind))
        count += 1
    return count


def load_manifest(folder: Path, *, verify_files: bool = False) -> dict:
    folder = Path(folder)
    manifest_path = folder / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if data.get("format_version") != FORMAT_VERSION or data.get("status") != "success":
        raise ValueError("backup manifest is not a successful supported snapshot")
    entries = data.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("backup manifest has no files")
    if verify_files:
        for item in entries:
            path = (folder / str(item["path"])).resolve()
            if folder.resolve() not in path.parents or not path.is_file():
                raise ValueError(f"missing or unsafe backup file: {item.get('path')}")
            if path.stat().st_size != int(item["size"]) or _sha256(path) != item["sha256"]:
                raise ValueError(f"backup checksum mismatch: {item['path']}")
            if item.get("kind") == "sqlite":
                _integrity(path)
    return data


def valid_backups(backup_root: Path) -> list[tuple[Path, dict]]:
    results: list[tuple[Path, dict]] = []
    if not Path(backup_root).is_dir():
        return results
    for folder in Path(backup_root).iterdir():
        if not folder.is_dir() or not folder.name.startswith(PERIODIC_PREFIX):
            continue
        try:
            results.append((folder, load_manifest(folder)))
        except (OSError, ValueError, json.JSONDecodeError, KeyError):
            continue
    return sorted(results, key=lambda pair: str(pair[1].get("completed_at", "")))


def backup_due(backup_root: Path, *, now: float | None = None, interval_sec: int = 86400) -> bool:
    """明文或加密备份任一新于间隔即视为已覆盖，避免加密态重复备份。"""
    backups = valid_backups(backup_root)
    try:
        from .encrypted_backup import valid_encrypted_backups

        backups.extend(valid_encrypted_backups(backup_root))
    except ImportError:
        pass
    if not backups:
        return True
    completed = max(float(item.get("completed_at_epoch", 0)) for _folder, item in backups)
    return (time.time() if now is None else now) - completed >= interval_sec


def create_periodic_backup(
    data_dir: Path,
    backup_root: Path,
    *,
    persona_file: Path | None = None,
    keep: int = 7,
    now: float | None = None,
) -> Path:
    """创建一份完整成功后才出现的快照。

    三个数据库各自通过 SQLite 在线备份取得一致快照；它们之间不是同一时刻的
    原子快照，这一限制会写进 manifest。任一必需数据库失败都会删除临时目录。
    """
    data_dir, backup_root = Path(data_dir), Path(backup_root)
    started = time.time() if now is None else now
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(started))
    final = backup_root / f"{PERIODIC_PREFIX}{stamp}-{uuid.uuid4().hex[:8]}"
    partial = backup_root / f".partial-{uuid.uuid4().hex}"
    files: list[dict] = []
    counts: dict[str, int] = {}
    try:
        partial.mkdir(parents=True)
        for name in DB_NAMES:
            src = data_dir / name
            if not src.is_file():
                raise FileNotFoundError(f"required database missing: {src}")
            dst = partial / name
            version = _online_backup(src, dst)
            files.append(_entry(dst, partial, kind="sqlite", schema_version=version))

        for dirname in RESOURCE_DIRS:
            counts[dirname] = _copy_tree(
                data_dir / dirname, partial / dirname, partial, files, dirname
            )
        if persona_file and Path(persona_file).is_file():
            dst = partial / "project" / Path(persona_file).name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(persona_file, dst)
            files.append(_entry(dst, partial, kind="persona_source"))
            counts["persona_source"] = 1
        else:
            counts["persona_source"] = 0

        completed = time.time() if now is None else started
        manifest = {
            "format_version": FORMAT_VERSION,
            "status": "success",
            "started_at_epoch": started,
            "completed_at_epoch": completed,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(completed)),
            "scope": {
                "databases": list(DB_NAMES),
                "resource_dirs": list(RESOURCE_DIRS),
                "chroma": "excluded_rebuildable",
                "consistency": "each SQLite file is internally consistent; the three databases are not a cross-database atomic snapshot",
                "counts": counts,
            },
            "files": files,
        }
        manifest_tmp = partial / "manifest.json.tmp"
        manifest_tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(manifest_tmp, partial / "manifest.json")
        os.replace(partial, final)
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise

    snapshots = valid_backups(backup_root)
    for old, _manifest in snapshots[:-max(1, int(keep))]:
        shutil.rmtree(old, ignore_errors=False)
    return final
