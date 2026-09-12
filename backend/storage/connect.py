"""Single database connection boundary and SQLCipher migration proof helpers.

Runtime databases remain plaintext until the later P3-04 key-broker and migration
slices are complete.  Every backend caller nevertheless enters through this
module now, so enabling encryption later cannot leave maintenance or reset paths
silently opening a different kind of connection.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

SQLITE_HEADER = b"SQLite format 3\x00"
RAW_KEY_BYTES = 32


class SQLCipherUnavailable(RuntimeError):
    """Raised when encrypted storage is requested without a SQLCipher driver."""


def _sqlcipher_driver():
    try:
        from sqlcipher3 import dbapi2 as driver
    except (ImportError, OSError) as exc:
        raise SQLCipherUnavailable(
            "SQLCipher driver is unavailable; encrypted storage cannot be opened"
        ) from exc
    return driver


def _raw_key_literal(key: bytes) -> str:
    if not isinstance(key, bytes) or len(key) != RAW_KEY_BYTES:
        raise ValueError("SQLCipher raw key must contain exactly 32 bytes")
    # The only interpolated bytes are validated binary key material encoded as hex.
    return f'"x\'{key.hex()}\'"'


def _path_literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def connect_database(
    database: str | Path,
    *,
    encrypted_key: bytes | None = None,
    timeout: float = 5.0,
    check_same_thread: bool = True,
    row_factory: bool = False,
):
    """Open a plaintext SQLite or keyed SQLCipher connection.

    A raw 256-bit key is accepted only through memory.  It is never read from an
    environment variable or written to logs.  For encrypted connections the key
    and cipher status are checked before any schema access.
    """
    driver = sqlite3 if encrypted_key is None else _sqlcipher_driver()
    conn = driver.connect(
        str(database), timeout=timeout, check_same_thread=check_same_thread
    )
    try:
        if encrypted_key is not None:
            # The community Windows build otherwise emits low-level HMAC errors
            # directly to stderr for a wrong key.  Keep failure details out of
            # user-visible logs; callers receive one generic DB-API exception.
            conn.execute("PRAGMA cipher_log_level = NONE")
            conn.execute(f"PRAGMA key = {_raw_key_literal(encrypted_key)}")
            version = conn.execute("PRAGMA cipher_version").fetchone()
            status = conn.execute("PRAGMA cipher_status").fetchone()
            if not version or not version[0] or not status or int(status[0]) != 1:
                raise SQLCipherUnavailable("database handle is not using SQLCipher")
            # Force the deferred key check before returning the handle.
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        if row_factory:
            conn.row_factory = driver.Row
        return conn
    except Exception:
        conn.close()
        raise


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def database_manifest(conn, *, sample_rows: int = 20) -> dict[str, Any]:
    """Return content-free structural/count/sample-hash evidence for a database."""
    schema_rows = conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    ).fetchall()
    schema_payload = [tuple(_json_value(value) for value in row) for row in schema_rows]
    schema_hash = hashlib.sha256(
        json.dumps(schema_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    tables: dict[str, dict[str, Any]] = {}
    table_names = sorted(str(row[1]) for row in schema_rows if str(row[0]) == "table")
    for table in table_names:
        identifier = _quoted_identifier(table)
        count = int(conn.execute(f"SELECT count(*) FROM {identifier}").fetchone()[0])
        try:
            rows = conn.execute(
                f"SELECT * FROM {identifier} ORDER BY rowid LIMIT ?", (int(sample_rows),)
            ).fetchall()
        except Exception:
            rows = conn.execute(
                f"SELECT * FROM {identifier} LIMIT ?", (int(sample_rows),)
            ).fetchall()
        payload = [[_json_value(value) for value in row] for row in rows]
        tables[table] = {
            "rows": count,
            "sample_hash": hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        }

    version_row = conn.execute("PRAGMA user_version").fetchone()
    return {
        "user_version": int(version_row[0]) if version_row else 0,
        "schema_hash": schema_hash,
        "tables": tables,
    }


def verify_encrypted_database(database: str | Path, key: bytes) -> dict[str, Any]:
    """Open an encrypted copy, verify both cipher HMACs and SQLite structure."""
    conn = connect_database(database, encrypted_key=key)
    try:
        cipher_errors = conn.execute("PRAGMA cipher_integrity_check").fetchall()
        if cipher_errors:
            raise ValueError("SQLCipher integrity check failed")
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if not row or str(row[0]).lower() != "ok":
            raise ValueError("SQLite integrity check failed")
        version = conn.execute("PRAGMA cipher_version").fetchone()
        return {
            "cipher_version": str(version[0]),
            "manifest": database_manifest(conn),
        }
    finally:
        conn.close()


def create_encrypted_copy(source: str | Path, target: str | Path, key: bytes) -> dict[str, Any]:
    """Export one plaintext database to a new encrypted file without overwriting.

    This is the P3-04 PoC primitive.  Production migration will add the global
    persistence gate, directory switch, resumable state machine, and key broker.
    """
    source, target = Path(source), Path(target)
    if not source.is_file():
        raise FileNotFoundError(source)
    _raw_key_literal(key)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        # 独占创建既消除 exists()→ATTACH 的竞态，也明确了失败时只删除本函数
        # 自己保留的目标文件。SQLCipher 可以在这个空文件上初始化加密数据库。
        with target.open("xb"):
            pass
    except FileExistsError:
        raise FileExistsError(target) from None

    try:
        plain = connect_database(source)
        try:
            source_manifest = database_manifest(plain)
        finally:
            plain.close()

        driver = _sqlcipher_driver()
        conn = driver.connect(str(source), timeout=10)
        attached = False
        try:
            cipher_version = conn.execute("PRAGMA cipher_version").fetchone()
            if not cipher_version or not cipher_version[0]:
                raise SQLCipherUnavailable("sqlcipher_export is unavailable")
            conn.execute(
                f"ATTACH DATABASE {_path_literal(target)} AS encrypted "
                f"KEY {_raw_key_literal(key)}"
            )
            attached = True
            conn.execute("SELECT sqlcipher_export('encrypted')")
            conn.execute(f"PRAGMA encrypted.user_version = {source_manifest['user_version']}")
            conn.commit()
            conn.execute("DETACH DATABASE encrypted")
            attached = False
        except Exception:
            if attached:
                try:
                    conn.execute("DETACH DATABASE encrypted")
                except Exception:
                    pass
            raise
        finally:
            conn.close()

        with target.open("rb") as handle:
            header = handle.read(len(SQLITE_HEADER))
        if header == SQLITE_HEADER:
            raise ValueError("SQLCipher export produced a plaintext SQLite header")
        verified = verify_encrypted_database(target, key)
        if verified["manifest"] != source_manifest:
            raise ValueError("encrypted copy does not match the source manifest")
    except Exception:
        # 包括导出后的 HMAC/SQLite 完整性检查失败；不得留下看似可用的残件。
        target.unlink(missing_ok=True)
        raise

    return {
        "cipher_version": verified["cipher_version"],
        "source_manifest": source_manifest,
        "target_manifest": verified["manifest"],
    }


def operational_errors() -> tuple[type[Exception], ...]:
    """sqlite3 与 sqlcipher3 两个驱动的 OperationalError 元组。

    P3-04 E：SQLCipher 驱动的异常与 sqlite3 是平行体系（互不继承），
    幂等兼容 DDL（「列已存在则跳过」类）必须同时捕获两者。模块级常量
    ``OPERATIONAL_ERRORS`` 供 except 子句直接引用。
    """
    return OPERATIONAL_ERRORS


def _operational_errors() -> tuple[type[Exception], ...]:
    errors: list[type[Exception]] = [sqlite3.OperationalError]
    try:
        from sqlcipher3 import dbapi2 as _sqlcipher

        errors.append(_sqlcipher.OperationalError)
    except Exception:
        pass
    return tuple(errors)


OPERATIONAL_ERRORS = _operational_errors()
