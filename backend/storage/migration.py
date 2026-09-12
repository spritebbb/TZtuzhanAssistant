# -*- coding: utf-8 -*-
"""P3-04 E 片：数据目录加密迁移引擎——状态机 + 隔离进程校验 + 原子目录切换。

状态机（docs/Zcode技术指导.md §21.1，与 ADR P3-04 一致）：
    unencrypted → preparing → verified → switched → cleanup_pending → encrypted
    任一步失败 → failed（暂存区清理，原始数据目录分毫不动）

流程：
- preparing：三库各自先做 SQLite backup API 一致性快照（迁移集内备份），再
  逐库 ``create_encrypted_copy``（B 片原语，内含 manifest 等价校验）到同卷暂存目录；
- verified：**隔离子进程**校验——错误 key 必须被拒、正确 key 必须通过
  cipher 完整性与 manifest 等价；随后媒体/附件/人格资产按 C 片容器加密；
- switched：同卷两次 rename 完成原子目录切换（先挪走明文目录再挪入暂存，
  第二次失败立即挪回）；cleanup_pending 表示切换完成、明文目录仍保留；
- encrypted：``finish_cleanup`` 在用户确认迁移成功后删除明文目录。

边界与纪律：
- MK 只经 stdin 传给隔离子进程，不进命令行/环境变量/磁盘；
- 明文目录的删除永远不在本引擎自动发生，必须显式调用 finish_cleanup；
- chroma/日志/telemetry/tts_cache 等可重建产物不迁移，journal 记录 skip 理由；
- 本引擎是纯库：假定调用方已停应用（运行时接线片负责优雅停机与 persistence
  gate），对一次性数据目录运行；E 片不触碰真实 ``data/``。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from .connect import (
    SQLITE_HEADER,
    connect_database,
    create_encrypted_copy,
    verify_encrypted_database,
)

JOURNAL_NAME = "encryption-migration.json"
STATES = (
    "unencrypted", "preparing", "verified", "switched",
    "cleanup_pending", "encrypted", "failed",
)
# 三个运行时库（与 maintenance/loop.py 的 checkpoint 集合一致）
DATABASE_NAMES = ("bot.db", "sessions.db", "agent_tasks.db")
# 资产目录 → C 片分域；可重建产物（chroma/tts_cache/日志/telemetry）不迁移
ASSET_DOMAINS: dict[str, str] = {
    "imgs": "media",
    "screenshots": "media",
    "documents": "attachment",
    "personas": "persona",
}
STAGING_SUFFIX = ".encrypted-staging"


class MigrationError(RuntimeError):
    """迁移状态机拒绝执行或某一步失败（journal 会记录原因）。"""


def read_journal(data_root: str | Path) -> dict | None:
    journal = Path(data_root).parent / JOURNAL_NAME
    if not journal.is_file():
        return None
    try:
        return json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_journal(data_root: Path, journal: dict) -> None:
    journal["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    target = data_root.parent / JOURNAL_NAME
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)


def _checkpoint_source(path: Path) -> None:
    """WAL 收敛：让导出读到完整已提交数据（应用已停的语义由运行时接线保证）。"""
    conn = connect_database(path, timeout=10)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def _snapshot_backup(source: Path, backup_dir: Path) -> Path:
    """迁移集内一致性备份（SQLite backup API；P0-02 日常备份之外的独立副本）。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / source.name
    src = connect_database(source, timeout=10)
    try:
        dst = connect_database(target, timeout=10)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return target


def _run_verify_child(db_path: Path, key: bytes, expect: str) -> dict:
    """隔离子进程校验：key 经 stdin 传入（不进 argv/env/磁盘）。

    expect="accept"：正确 key 必须打开并读出 manifest；
    expect="reject"：错误 key 必须被 SQLCipher 拒绝。
    """
    cmd = [
        sys.executable, "-X", "utf8", "-m", "backend.storage.migration",
        "--verify-child", str(db_path), expect,
    ]
    proc = subprocess.run(
        cmd, input=key, capture_output=True, timeout=180,
        cwd=Path(__file__).resolve().parents[2],
    )
    try:
        result = json.loads(proc.stdout.decode("utf-8", errors="replace").strip() or "{}")
    except ValueError:
        result = {}
    if proc.returncode != 0 or not result.get("ok"):
        detail = (proc.stderr.decode("utf-8", errors="replace")[-300:]).strip()
        raise MigrationError(f"隔离进程校验失败（expect={expect}）：{result.get('error') or detail}")
    if bool(result.get("verified")) != (expect == "accept"):
        raise MigrationError(f"隔离进程校验结果与预期不符：{result}")
    return result


def _verify_child_main(db_path: str, expect: str) -> int:
    key = sys.stdin.buffer.read(32)
    if len(key) != 32:
        print(json.dumps({"ok": True, "verified": False, "error": "key 长度异常"}))
        return 0
    try:
        info = verify_encrypted_database(db_path, key)
        print(json.dumps({"ok": True, "verified": True, "cipher_version": info.get("cipher_version", "")}))
        return 0
    except Exception as exc:
        # 错误 key 被拒是预期结果；其他失败如实上报
        rejected = (
            (expect == "reject" and "Key" in type(exc).__name__)
            or ("file is not a database" in str(exc).lower())
        )
        print(json.dumps({"ok": True, "verified": False, "error": str(exc)[:200],
                          "rejected": rejected}))
        return 0


def _encrypt_assets(data_root: Path, staging: Path, master_key: bytes, key_id: str,
                    journal: dict, progress: Callable[[str], None] | None) -> None:
    from .file_container import encrypt_file

    index: dict[str, dict] = {}
    assets_dir = staging / "assets"
    for rel_dir, domain in ASSET_DOMAINS.items():
        source_dir = data_root / rel_dir
        if not source_dir.is_dir():
            continue
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(data_root).as_posix()
            container, info = encrypt_file(path, assets_dir, master_key,
                                           key_id=key_id, domain=domain)
            index[rel] = {
                "container": container.name, "domain": domain,
                "sha256": info.plaintext_sha256, "size": info.plaintext_size,
            }
            if progress:
                progress(f"asset:{rel}")
    (staging / "asset-index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    journal["asset_count"] = len(index)


def migrate_data_root(
    data_root: str | Path,
    master_key: bytes,
    *,
    progress: Callable[[str], None] | None = None,
    verify_process: bool = True,
) -> dict:
    """把一个明文数据目录迁移为加密数据目录（一次性数据；调用方须已停应用）。

    成功结束时返回 journal（state=cleanup_pending）：新 data_root 已是加密目录，
    原明文目录完整保留在 ``<name>.plaintext-<stamp>``，等用户确认后调
    ``finish_cleanup`` 收尾。任何失败：journal=failed、暂存区清理、原目录不动。
    """
    from ..core.keyslots import master_key_id

    data_root = Path(data_root).resolve()
    if not data_root.is_dir():
        raise MigrationError(f"数据目录不存在：{data_root}")
    for name in DATABASE_NAMES:
        if not (data_root / name).is_file():
            raise MigrationError(f"缺少运行时库 {name}，不是可迁移的数据目录")

    existing = read_journal(data_root)
    if existing and existing.get("state") not in ("failed", "encrypted"):
        raise MigrationError(f"已有未完结的迁移记录（state={existing.get('state')}），先处理它")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    staging = data_root.parent / f"{data_root.name}{STAGING_SUFFIX}-{stamp}"
    plaintext_keep = data_root.parent / f"{data_root.name}.plaintext-{stamp}"
    key_id = master_key_id(master_key)
    journal: dict = {
        "state": "preparing", "started_at": stamp,
        "data_root": str(data_root), "staging": str(staging),
        "plaintext_keep": str(plaintext_keep), "key_id": key_id,
        "databases": list(DATABASE_NAMES), "asset_count": 0,
        "skipped": {
            "chroma": "可重建向量索引（C 片 vector_embeddings 接管）",
            "chroma_mem0": "可重建向量索引",
            "tts_cache": "可重建语音缓存",
            "telemetry.db": "可重建日聚合（Q3）",
            "backups": "历史明文备份留在明文目录；加密备份由 F 片接管",
            "*.log / tool_log.jsonl": "派生日志，不进加密集",
        },
    }
    _write_journal(data_root, journal)

    def fail(message: str) -> MigrationError:
        journal["state"] = "failed"
        journal["error"] = message[:400]
        _write_journal(data_root, journal)
        shutil.rmtree(staging, ignore_errors=True)
        return MigrationError(message)

    try:
        # ---- preparing：一致性快照 + 逐库加密导出 ----
        for name in DATABASE_NAMES:
            source = data_root / name
            _checkpoint_source(source)
            _snapshot_backup(source, staging / "premigration-backup")
            if progress:
                progress(f"backup:{name}")
        for name in DATABASE_NAMES:
            create_encrypted_copy(data_root / name, staging / name, master_key)
            if progress:
                progress(f"export:{name}")
        for name in DATABASE_NAMES:
            evidence = verify_encrypted_database(staging / name, master_key)
            if evidence["manifest"]["user_version"] is None:
                raise MigrationError(f"{name} 加密副本缺少 user_version")
        journal["state"] = "preparing-done"
        _write_journal(data_root, journal)

        # ---- verified：隔离进程双 key 校验 + 资产加密 ----
        if verify_process:
            for name in DATABASE_NAMES:
                _run_verify_child(staging / name, master_key, "accept")
                wrong = bytes(32)  # 全零 key：合法长度但必非本库密钥
                if wrong == master_key:
                    wrong = b"\x01" * 32
                _run_verify_child(staging / name, wrong, "reject")
        if progress:
            progress("verify:process")
        _encrypt_assets(data_root, staging, master_key, key_id, journal, progress)
        journal["state"] = "verified"
        _write_journal(data_root, journal)

        # ---- switched：原子目录切换（失败立即回滚 rename） ----
        os.rename(data_root, plaintext_keep)
        try:
            os.rename(staging, data_root)
        except OSError as exc:
            os.rename(plaintext_keep, data_root)  # 回滚：明文目录归位
            raise fail(f"目录切换失败已回滚：{exc}") from exc
        journal["state"] = "switched"
        journal["state"] = "cleanup_pending"
        _write_journal(data_root, journal)
        return journal
    except MigrationError as exc:
        # 业务失败同样必须落 journal（此前直接 raise 会把状态卡在中间态）
        raise fail(str(exc)) from exc
    except Exception as exc:
        raise fail(f"{type(exc).__name__}: {exc}") from exc


def finish_cleanup(data_root: str | Path) -> dict:
    """用户确认迁移成功后的收尾：删除明文目录，journal → encrypted。

    删除是显式动作，永不随迁移自动发生（普通 SSD 上的覆盖删除不等于物理
    擦除——ADR 边界声明）。
    """
    data_root = Path(data_root).resolve()
    journal = read_journal(data_root)
    if not journal:
        raise MigrationError("找不到迁移 journal")
    if journal.get("state") != "cleanup_pending":
        raise MigrationError(f"当前状态 {journal.get('state')} 不允许清理明文目录")
    plaintext = Path(journal["plaintext_keep"])
    if plaintext.is_dir():
        shutil.rmtree(plaintext)
    journal["state"] = "encrypted"
    journal["plaintext_removed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _write_journal(data_root, journal)
    return journal


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - 子进程入口
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 3 and argv[0] == "--verify-child":
        return _verify_child_main(argv[1], argv[2])
    print(json.dumps({"ok": False, "error": "未知子命令"}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
