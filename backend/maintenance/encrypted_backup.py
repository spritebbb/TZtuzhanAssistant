# -*- coding: utf-8 -*-
"""P3-04 F：同一 generation 的加密备份与空目录恢复。

设计边界：
- 三个 SQLCipher 库经各自的在线 backup API 复制成新的加密库，绝不再导出明文；
- imgs/screenshots/documents 下的加密对象原样复制，逐文件记录 hash/size/key_id；
- personas 与少量运行配置保持明文复制（迁移契约本来就是明文随迁）；
- 仅携带恢复槽 ``keyslots/recovery.json``，绝不复制机器绑定的 ``local.dpapi``；
- manifest 不包含 MK、口令或 KEK；恢复必须先取得 MK（本机槽或恢复口令）。
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import time
import uuid
from pathlib import Path
from typing import Any

from ..core.keyslots import master_key_id, write_local_slot
from ..storage.connect import connect_database, verify_encrypted_database
from ..storage.file_container import ALLOWED_DOMAINS, MAGIC
from .backup_manifest import DB_NAMES, _entry, _sha256

FORMAT_VERSION = 2
BACKUP_KIND = "tz-encrypted-backup"
ENCRYPTED_PREFIX = "encrypted-periodic-"
ENCRYPTED_RESOURCE_DIRS = {
    "imgs": "media",
    "screenshots": "media",
    "documents": "attachment",
}
PLAINTEXT_RESOURCE_DIRS = ("personas",)
PLAINTEXT_CONFIG_FILES = ("feature_flags.json", "mcp_servers.json", "memes.json")
RECOVERY_SLOT_REL = Path("keyslots") / "recovery.json"


class EncryptedBackupError(ValueError):
    """加密备份格式、密钥绑定或恢复目标不符合契约。"""


def _inspect_container(path: Path, *, key_id: str, domain: str) -> dict[str, Any]:
    """只读取并校验容器 header，不解密内容；内容 hash 由 manifest 保证。"""
    try:
        with Path(path).open("rb") as handle:
            magic = handle.read(len(MAGIC))
            if magic != MAGIC:
                raise EncryptedBackupError(f"加密对象不是 TZFC 容器: {path.name}")
            size_raw = handle.read(4)
            if len(size_raw) != 4:
                raise EncryptedBackupError(f"加密对象 header 截断: {path.name}")
            size = struct.unpack(">I", size_raw)[0]
            if not 1 <= size <= 4096:
                raise EncryptedBackupError(f"加密对象 header 长度非法: {path.name}")
            payload = json.loads(handle.read(size).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, EncryptedBackupError):
            raise
        raise EncryptedBackupError(f"加密对象 header 无法读取: {path.name}") from exc
    if not isinstance(payload, dict):
        raise EncryptedBackupError(f"加密对象 header 非法: {path.name}")
    if payload.get("format_version") != 1:
        raise EncryptedBackupError(f"加密对象格式版本不支持: {path.name}")
    if str(payload.get("key_id")) != key_id:
        raise EncryptedBackupError(f"加密对象 key_id 与备份不一致: {path.name}")
    if str(payload.get("domain")) != domain or domain not in ALLOWED_DOMAINS:
        raise EncryptedBackupError(f"加密对象 domain 与目录不一致: {path.name}")
    return {
        "container_object_id": str(payload.get("object_id") or ""),
        "container_domain": domain,
    }


def _collect_encrypted_tree(
    source: Path, target: Path, root: Path, *, key_id: str, domain: str
) -> list[dict]:
    if not source.is_dir():
        return []
    files: list[dict] = []
    for src in sorted(path for path in source.rglob("*") if path.is_file()):
        rel = src.relative_to(source)
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        item = _entry(dst, root, kind="encrypted_object")
        item.update({"key_id": key_id, **_inspect_container(dst, key_id=key_id, domain=domain)})
        files.append(item)
    return files


def _copy_plain_tree(source: Path, target: Path, root: Path) -> list[dict]:
    if not source.is_dir():
        return []
    files: list[dict] = []
    for src in sorted(path for path in source.rglob("*") if path.is_file()):
        rel = src.relative_to(source)
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        files.append(_entry(dst, root, kind="plaintext_resource"))
    return files


def _encrypted_online_backup(source: Path, target: Path, *, master_key: bytes) -> dict[str, Any]:
    source_conn = connect_database(source, encrypted_key=master_key, timeout=10)
    target_conn = connect_database(target, encrypted_key=master_key, timeout=10)
    try:
        source_conn.execute("PRAGMA busy_timeout = 10000")
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()
    verified = verify_encrypted_database(target, master_key)
    return {
        "schema_version": int(verified["manifest"]["user_version"]),
        "schema_hash": str(verified["manifest"]["schema_hash"]),
        "database_manifest": verified["manifest"],
    }


def load_encrypted_manifest(folder: Path, *, verify_files: bool = False) -> dict:
    folder = Path(folder)
    try:
        data = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EncryptedBackupError("加密备份 manifest 无法读取") from exc
    if (
        data.get("format_version") != FORMAT_VERSION
        or data.get("kind") != BACKUP_KIND
        or data.get("status") != "success"
    ):
        raise EncryptedBackupError("加密备份 manifest 不是受支持的完整快照")
    if not data.get("generation") or not data.get("key_id"):
        raise EncryptedBackupError("加密备份 manifest 缺少 generation/key_id")
    entries = data.get("files")
    if not isinstance(entries, list) or not entries:
        raise EncryptedBackupError("加密备份 manifest 没有文件")
    scope = data.get("scope")
    recovery = scope.get("recovery") if isinstance(scope, dict) else None
    if (
        not isinstance(recovery, dict)
        or recovery.get("required") is not True
        or recovery.get("slot_in_backup") is not False
        or str(recovery.get("slot_key_id") or "") != str(data.get("key_id"))
        or not str(recovery.get("slot_sha256") or "")
    ):
        raise EncryptedBackupError("加密备份没有合格的独立恢复槽证据")
    generation = str(data["generation"])
    database_entries = []
    for item in entries:
        if not isinstance(item, dict) or str(item.get("generation") or "") != generation:
            raise EncryptedBackupError("加密备份条目 generation 不一致")
        if item.get("kind") == "sqlcipher":
            database_entries.append(item)
    if {str(item.get("path") or "") for item in database_entries} != set(DB_NAMES):
        raise EncryptedBackupError("加密备份数据库清单不完整")
    if verify_files:
        resolved_root = folder.resolve()
        for item in entries:
            try:
                rel = Path(str(item["path"]))
                path = (folder / rel).resolve()
                if resolved_root not in path.parents or not path.is_file():
                    raise EncryptedBackupError(f"备份文件缺失或路径不安全: {item.get('path')}")
                if path.stat().st_size != int(item["size"]) or _sha256(path) != item["sha256"]:
                    raise EncryptedBackupError(f"备份校验和不匹配: {item['path']}")
                if item.get("kind") == "encrypted_object":
                    _inspect_container(
                        path,
                        key_id=str(data["key_id"]),
                        domain=str(item["container_domain"]),
                    )
            except (KeyError, TypeError, ValueError) as exc:
                if isinstance(exc, EncryptedBackupError):
                    raise
                raise EncryptedBackupError(f"备份条目字段非法: {item!r}") from exc
    return data


def valid_encrypted_backups(backup_root: Path) -> list[tuple[Path, dict]]:
    results: list[tuple[Path, dict]] = []
    root = Path(backup_root)
    if not root.is_dir():
        return results
    for folder in root.iterdir():
        if not folder.is_dir() or not folder.name.startswith(ENCRYPTED_PREFIX):
            continue
        try:
            results.append((folder, load_encrypted_manifest(folder)))
        except (OSError, ValueError, json.JSONDecodeError, KeyError):
            continue
    return sorted(
        results,
        key=lambda pair: (
            float(pair[1].get("completed_at_epoch", 0)),
            str(pair[1].get("completed_at", "")),
        ),
    )


def create_encrypted_periodic_backup(
    data_dir: Path,
    backup_root: Path,
    *,
    master_key: bytes,
    persona_file: Path | None = None,
    keep: int = 7,
    now: float | None = None,
) -> Path:
    """创建加密备份；完整成功后才原子改名为最终 generation。"""
    data_dir, backup_root = Path(data_dir), Path(backup_root)
    key_id = master_key_id(master_key)
    started = time.time() if now is None else now
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(started))
    generation = f"{stamp}-{uuid.uuid4().hex[:8]}"
    final = backup_root / f"{ENCRYPTED_PREFIX}{generation}"
    partial = backup_root / f".partial-encrypted-{uuid.uuid4().hex}"
    files: list[dict] = []
    counts: dict[str, int] = {}
    backup_root.mkdir(parents=True, exist_ok=True)
    try:
        partial.mkdir(parents=True)
        for name in DB_NAMES:
            src = data_dir / name
            if not src.is_file():
                raise FileNotFoundError(f"required encrypted database missing: {src}")
            dst = partial / name
            evidence = _encrypted_online_backup(src, dst, master_key=master_key)
            item = _entry(dst, partial, kind="sqlcipher", schema_version=evidence["schema_version"])
            item.update({
                "key_id": key_id,
                "generation": generation,
                "schema_hash": evidence["schema_hash"],
                "database_manifest": evidence["database_manifest"],
            })
            files.append(item)

        for dirname, domain in ENCRYPTED_RESOURCE_DIRS.items():
            copied = _collect_encrypted_tree(
                data_dir / dirname, partial / dirname, partial, key_id=key_id, domain=domain
            )
            for item in copied:
                item["generation"] = generation
            files.extend(copied)
            counts[dirname] = len(copied)

        for dirname in PLAINTEXT_RESOURCE_DIRS:
            copied = _copy_plain_tree(data_dir / dirname, partial / dirname, partial)
            for item in copied:
                item["generation"] = generation
            files.extend(copied)
            counts[dirname] = len(copied)

        for name in PLAINTEXT_CONFIG_FILES:
            src = data_dir / name
            if src.is_file():
                dst = partial / name
                shutil.copy2(src, dst)
                item = _entry(dst, partial, kind="plaintext_config")
                item["generation"] = generation
                files.append(item)
                counts[name] = 1

        # 恢复槽是独立的密钥恢复材料，绝不与密文备份同包携带。这里只验证
        # 源机具备同 key 的换机恢复路径，并把非秘密指纹写进 manifest，恢复时
        # 必须由用户另行提供该槽。没有任何有效恢复槽时拒绝写出“不可恢复的
        # 成功备份”。
        recovery_slot = data_dir / RECOVERY_SLOT_REL
        if not recovery_slot.is_file():
            raise EncryptedBackupError(
                "缺少独立恢复槽，无法保证换机恢复；拒绝创建不可恢复的加密备份"
            )
        try:
            slot_payload = json.loads(recovery_slot.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EncryptedBackupError("恢复槽损坏，拒绝创建不可恢复的加密备份") from exc
        if str(slot_payload.get("key_id") or "") != key_id:
            raise EncryptedBackupError("恢复槽 key_id 与当前主密钥不一致")
        recovery_evidence = {
            "required": True,
            "slot_in_backup": False,
            "slot_key_id": key_id,
            "slot_sha256": _sha256(recovery_slot),
            "instruction": "restore 时另行提供 keyslots/recovery.json 与恢复口令",
        }
        counts["recovery_slot_external"] = 1

        if persona_file and Path(persona_file).is_file():
            dst = partial / "project" / Path(persona_file).name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(persona_file, dst)
            item = _entry(dst, partial, kind="persona_source")
            item["generation"] = generation
            files.append(item)
            counts["persona_source"] = 1
        else:
            counts["persona_source"] = 0

        completed = time.time() if now is None else started
        manifest = {
            "format_version": FORMAT_VERSION,
            "kind": BACKUP_KIND,
            "status": "success",
            "generation": generation,
            "key_id": key_id,
            "started_at_epoch": started,
            "completed_at_epoch": completed,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(completed)),
            "scope": {
                "databases": list(DB_NAMES),
                "encrypted_resource_dirs": list(ENCRYPTED_RESOURCE_DIRS),
                "plaintext_resource_dirs": list(PLAINTEXT_RESOURCE_DIRS),
                "plaintext_config_files": list(PLAINTEXT_CONFIG_FILES),
                "consistency": "each SQLCipher database is copied with the online backup API under a persistence gate; the three databases are not a cross-database atomic snapshot",
                "keys": "manifest contains no MK, passphrase, KEK, local DPAPI slot or recovery slot",
                "recovery": recovery_evidence,
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

    snapshots = valid_encrypted_backups(backup_root)
    for old, _manifest in snapshots[:-max(1, int(keep))]:
        shutil.rmtree(old, ignore_errors=False)
    return final


def restore_encrypted_backup(
    folder: Path,
    target: Path,
    *,
    master_key: bytes,
    rebuild_local_slot: bool = False,
    apply: bool = False,
) -> dict[str, Any]:
    """校验并恢复到空 data root；apply=False 时只做 dry-run 与密钥校验。"""
    folder = Path(folder)
    target = Path(target)
    manifest = load_encrypted_manifest(folder, verify_files=True)
    expected_key_id = str(manifest["key_id"])
    actual_key_id = master_key_id(master_key)
    if actual_key_id != expected_key_id:
        raise EncryptedBackupError("提供的主密钥与备份 key_id 不一致")

    plan: list[tuple[Path, Path]] = []
    for item in manifest["files"]:
        rel = Path(str(item["path"]))
        source = folder / rel
        if item.get("kind") == "sqlcipher":
            verified = verify_encrypted_database(source, master_key)
            if verified["manifest"] != item.get("database_manifest"):
                raise EncryptedBackupError(f"加密库结构/内容与 manifest 不一致: {item['path']}")
        elif item.get("kind") == "encrypted_object":
            _inspect_container(
                source,
                key_id=expected_key_id,
                domain=str(item["container_domain"]),
            )
        plan.append((source, target / rel))

    result = {
        "backup": str(folder),
        "target": str(target),
        "generation": manifest["generation"],
        "key_id": expected_key_id,
        "files": len(plan),
        "rebuild_local_slot": bool(rebuild_local_slot),
        "applied": bool(apply),
    }
    if not apply:
        return result

    if target.exists() and not target.is_dir():
        raise EncryptedBackupError("恢复目标必须是目录")
    if target.exists() and any(target.iterdir()):
        raise EncryptedBackupError("恢复目标必须不存在或为空目录")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".restore-{uuid.uuid4().hex}"
    try:
        staging.mkdir(parents=True)
        for source, destination in plan:
            rel = destination.relative_to(target)
            staged = staging / rel
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, staged)
        for item in manifest["files"]:
            if item.get("kind") != "sqlcipher":
                continue
            restored = staging / Path(str(item["path"]))
            verified = verify_encrypted_database(restored, master_key)
            if verified["manifest"] != item.get("database_manifest"):
                raise EncryptedBackupError(f"恢复副本校验失败: {item['path']}")
        if rebuild_local_slot:
            slot_root = staging / "keyslots"
            slot_root.mkdir(parents=True, exist_ok=True)
            write_local_slot(slot_root, master_key)
        if target.exists():
            try:
                target.rmdir()
            except OSError as exc:
                raise EncryptedBackupError("恢复目标在切换前变为非空，拒绝覆盖") from exc
        os.replace(staging, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return result


__all__ = [
    "BACKUP_KIND",
    "ENCRYPTED_PREFIX",
    "EncryptedBackupError",
    "create_encrypted_periodic_backup",
    "load_encrypted_manifest",
    "restore_encrypted_backup",
    "valid_encrypted_backups",
]
