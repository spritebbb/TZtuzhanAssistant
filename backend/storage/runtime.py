# -*- coding: utf-8 -*-
"""P3-04 E 运行时接线：加密模式判定与密钥分发（存储侧统一入口）。

职责：
- ``encrypted_mode()``：数据目录是否已处于加密态（迁移 journal 的
  cleanup_pending/encrypted 态）。plaintext 部署（E 之前的一切现状）恒为
  False，全部调用方零行为变化；
- ``database_key_or_none()``：加密模式下从 key broker 取 MK；锁定态抛
  ``DatabaseLockedError``（调用方——后台循环/惰性开库——据此跳过或拒绝）；
- ``close_all_databases()``：应用锁锁定时由锁端点调用，关闭已打开的库连接
  （忘钥匙的存储侧落实；解锁后由惰性开库按需重连）。

导入纪律：本模块对 core（key broker/userdb/telemetry）的引用全部放在函数
体内，避免 storage ↔ core 的导入环。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

from .migration import read_journal

_ENCRYPTED_STATES = {"cleanup_pending", "encrypted"}

# SQLite 明文文件头；SQLCipher 加密库没有它（头部是随机盐）
_SQLITE_MAGIC = b"SQLite format 3\x00"

_cache_lock = threading.Lock()
_cache: dict[str, object] = {"key": None, "encrypted": None}


class DatabaseLockedError(RuntimeError):
    """加密模式下应用处于锁定态（MK 不在内存），库无法打开。"""


def _journal_state() -> str | None:
    journal = read_journal(Path(config_data_dir()))
    return str(journal.get("state")) if journal else None


def _db_header_encrypted(data_dir: Path) -> bool:
    """P3-2：库文件自描述兜底——SQLCipher 加密库没有 SQLite 明文魔数。

    journal 被删/丢失/搬迁时，只靠 journal 会把加密库误判成明文，进而拿明文
    方式去开它（「file is not a database」，错误不指向真实原因）。文件头是库
    自身的、不依赖任何外部文件的判据。
    """
    try:
        with (data_dir / "bot.db").open("rb") as fh:
            head = fh.read(len(_SQLITE_MAGIC))
    except OSError:
        return False
    return len(head) == len(_SQLITE_MAGIC) and head != _SQLITE_MAGIC


def config_data_dir() -> Path:
    from ..core.config import config

    return config.data_dir


def encrypted_mode() -> bool:
    """数据目录是否已迁移为加密态。结果按 journal 指纹缓存。"""
    data_dir = config_data_dir()
    journal_path = data_dir.parent / f"encryption-migration.{data_dir.name}.json"
    try:
        stat = journal_path.stat()
        fingerprint = f"{journal_path}:{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        # journal 缺失：把库文件也纳入指纹，让 P3-2 的自描述兜底随库变化刷新
        try:
            db_stat = (data_dir / "bot.db").stat()
            fingerprint = f"{journal_path}:missing:{db_stat.st_mtime_ns}:{db_stat.st_size}"
        except OSError:
            fingerprint = f"{journal_path}:missing"
    with _cache_lock:
        if _cache["key"] != fingerprint:
            state = _journal_state()
            if state is None:
                # journal 不存在（被删/搬迁）：以库文件自描述为准（P3-2）
                encrypted = _db_header_encrypted(data_dir)
            else:
                encrypted = state in _ENCRYPTED_STATES
            _cache["key"] = fingerprint
            _cache["encrypted"] = encrypted
    return bool(_cache["encrypted"])


def reset_cache() -> None:
    """测试隔离：清空加密态缓存。"""
    with _cache_lock:
        _cache["key"] = None
        _cache["encrypted"] = None


def locked() -> bool:
    """应用锁是否处于锁定态（含未解锁的加密模式）。"""
    from ..core.key_broker import broker

    return broker().status()["state"] == "locked"


def database_key_or_none() -> bytes:
    """明文模式 → None；加密模式 → 已解锁返回 MK，锁定态抛 DatabaseLockedError。"""
    if not encrypted_mode():
        return None  # type: ignore[return-value]
    from ..core.key_broker import KeyBrokerError, broker

    try:
        return broker().database_key()
    except KeyBrokerError as exc:
        raise DatabaseLockedError(str(exc)) from exc


def close_all_databases() -> None:
    """应用锁锁定时关闭所有已打开的库连接（解锁后由惰性开库重连）。

    P3-5：close 失败不再静默——SQLCipher 连接一旦打开，后续读写不再需要
    MK；若 close 失败且被吞掉，broker 已忘钥匙但旧连接仍可完整读写，
    「锁=忘钥匙」模型形同虚设且无任何线索。
    """
    from ..core.log import logger

    try:
        from ..core import userdb

        userdb.db.close()
    except Exception as exc:
        logger.error("[应用锁] userdb 关闭失败（锁定态可能不完整）：{}", exc)
    try:
        from ..core import telemetry

        telemetry.close()
    except Exception as exc:
        logger.error("[应用锁] telemetry 关闭失败（锁定态可能不完整）：{}", exc)


# ---- 持久化门（P3-04 E/F）：迁移或一致性备份期间暂停写入 ----
# 使用计数式可重入门：迁移流程显式 engage/release，备份流程用
# ``persistence_gate()`` 在短窗口内复用同一状态。这样嵌套调用不会提前放门，
# 且保持原有布尔查询接口完全兼容。
_gate_lock = threading.RLock()
_gate_depth = 0
_gate_engaged = False


def engage_migration_gate() -> None:
    """启用持久化门：后台循环（initiative/维护/agent/tick）据此跳过一轮。"""
    global _gate_depth, _gate_engaged
    with _gate_lock:
        _gate_depth += 1
        _gate_engaged = True


def release_migration_gate() -> None:
    """释放一次持久化门；只有最后一层释放后才真正放行。"""
    global _gate_depth, _gate_engaged
    with _gate_lock:
        if _gate_depth > 0:
            _gate_depth -= 1
        if _gate_depth == 0:
            _gate_engaged = False


@contextmanager
def persistence_gate():
    """短窗口持久化门，供备份等需要一致快照的维护任务使用。"""
    engage_migration_gate()
    try:
        yield
    finally:
        release_migration_gate()


def migration_gate_engaged() -> bool:
    """兼容旧名：查询持久化门是否启用（迁移或备份窗口）。"""
    return _gate_engaged
