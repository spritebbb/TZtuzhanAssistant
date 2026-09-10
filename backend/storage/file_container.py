"""Chunked authenticated-encryption container for private file assets.

The caller supplies an in-memory master key.  This module derives a distinct
AES-256-GCM key for each data domain and never writes plaintext temporary files.
Production key creation and unlock are deliberately left to P3-04 D.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import struct
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


MAGIC = b"TZFC\x00\x01\r\n"
FORMAT_VERSION = 1
DEFAULT_CHUNK_SIZE = 1024 * 1024
MAX_CHUNK_SIZE = 8 * 1024 * 1024
MAX_HEADER_SIZE = 4096
MASTER_KEY_BYTES = 32
NONCE_BYTES = 12
TAG_BYTES = 16
ALLOWED_DOMAINS = frozenset(
    {"media", "attachment", "persona", "log", "backup", "temporary", "vector"}
)

_U32 = struct.Struct(">I")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_KDF_SALT = b"tztuzhan:p3-04:file-container:v1"


class FileContainerError(ValueError):
    """The encrypted container is invalid or cannot be authenticated."""


@dataclass(frozen=True)
class ContainerInfo:
    object_id: str
    key_id: str
    domain: str
    plaintext_size: int
    chunks: int
    plaintext_sha256: str


def _validate_master_key(master_key: bytes) -> None:
    if not isinstance(master_key, bytes) or len(master_key) != MASTER_KEY_BYTES:
        raise ValueError("master key must contain exactly 32 bytes")


def _validate_label(value: str, label: str) -> str:
    value = str(value or "")
    if not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{label} must use 1-64 safe identifier characters")
    return value


def derive_domain_key(master_key: bytes, domain: str) -> bytes:
    """Derive one 256-bit data key without persisting the master key."""
    _validate_master_key(master_key)
    domain = _validate_label(domain, "domain")
    if domain not in ALLOWED_DOMAINS:
        raise ValueError(f"unsupported encrypted data domain: {domain}")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_KDF_SALT,
        info=f"tztuzhan:{domain}:v1".encode("ascii"),
    ).derive(master_key)


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_exact(source: BinaryIO, size: int) -> bytes:
    if size < 0:
        raise FileContainerError("invalid encrypted container length")
    data = source.read(size)
    if not isinstance(data, bytes) or len(data) != size:
        raise FileContainerError("encrypted container is truncated")
    return data


def _read_json_header(source: BinaryIO) -> tuple[dict, bytes]:
    size = _U32.unpack(_read_exact(source, _U32.size))[0]
    if not 1 <= size <= MAX_HEADER_SIZE:
        raise FileContainerError("invalid encrypted container header")
    raw = _read_exact(source, size)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FileContainerError("invalid encrypted container header") from exc
    if not isinstance(value, dict):
        raise FileContainerError("invalid encrypted container header")
    return value, raw


def _global_header(*, object_id: str, key_id: str, domain: str, chunk_size: int) -> dict:
    return {
        "chunk_size": chunk_size,
        "domain": domain,
        "format_version": FORMAT_VERSION,
        "key_id": key_id,
        "object_id": object_id,
    }


def encrypt_stream(
    source: BinaryIO,
    sink: BinaryIO,
    master_key: bytes,
    *,
    key_id: str,
    domain: str,
    object_id: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> ContainerInfo:
    """Encrypt a binary stream as independently authenticated ordered chunks."""
    key_id = _validate_label(key_id, "key_id")
    domain = _validate_label(domain, "domain")
    object_id = _validate_label(object_id or uuid.uuid4().hex, "object_id")
    if domain not in ALLOWED_DOMAINS:
        raise ValueError(f"unsupported encrypted data domain: {domain}")
    if not isinstance(chunk_size, int) or not 1 <= chunk_size <= MAX_CHUNK_SIZE:
        raise ValueError(f"chunk_size must be between 1 and {MAX_CHUNK_SIZE}")

    data_key = derive_domain_key(master_key, domain)
    cipher = AESGCM(data_key)
    global_raw = _json_bytes(
        _global_header(
            object_id=object_id, key_id=key_id, domain=domain, chunk_size=chunk_size
        )
    )
    sink.write(MAGIC)
    sink.write(_U32.pack(len(global_raw)))
    sink.write(global_raw)

    digest = hashlib.sha256()
    total = 0
    index = 0
    current = source.read(chunk_size)
    if not isinstance(current, bytes):
        raise TypeError("encrypted file source must be opened in binary mode")

    while True:
        following = source.read(chunk_size) if current else b""
        if not isinstance(following, bytes):
            raise TypeError("encrypted file source must be opened in binary mode")
        final = not following
        nonce = os.urandom(NONCE_BYTES)
        record = {
            "chunk_index": index,
            "final": final,
            "format_version": FORMAT_VERSION,
            "key_id": key_id,
            "nonce": base64.b64encode(nonce).decode("ascii"),
        }
        record_raw = _json_bytes(record)
        aad = MAGIC + global_raw + record_raw
        encrypted = cipher.encrypt(nonce, current, aad)
        sink.write(_U32.pack(len(record_raw)))
        sink.write(record_raw)
        sink.write(_U32.pack(len(encrypted)))
        sink.write(encrypted)
        digest.update(current)
        total += len(current)
        index += 1
        if final:
            break
        current = following

    return ContainerInfo(
        object_id=object_id,
        key_id=key_id,
        domain=domain,
        plaintext_size=total,
        chunks=index,
        plaintext_sha256=digest.hexdigest(),
    )


def _decrypt_stream(
    source: BinaryIO,
    sink: BinaryIO,
    master_key: bytes,
    *,
    expected_key_id: str | None = None,
    expected_domain: str | None = None,
    max_plaintext_bytes: int | None = None,
) -> ContainerInfo:
    if _read_exact(source, len(MAGIC)) != MAGIC:
        raise FileContainerError("unsupported encrypted container format")
    header, global_raw = _read_json_header(source)
    try:
        if int(header["format_version"]) != FORMAT_VERSION:
            raise FileContainerError("unsupported encrypted container version")
        object_id = _validate_label(header["object_id"], "object_id")
        key_id = _validate_label(header["key_id"], "key_id")
        domain = _validate_label(header["domain"], "domain")
        chunk_size = int(header["chunk_size"])
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, FileContainerError):
            raise
        raise FileContainerError("invalid encrypted container header") from exc
    if domain not in ALLOWED_DOMAINS or not 1 <= chunk_size <= MAX_CHUNK_SIZE:
        raise FileContainerError("invalid encrypted container header")
    if expected_key_id is not None and key_id != expected_key_id:
        raise FileContainerError("encrypted container key id does not match")
    if expected_domain is not None and domain != expected_domain:
        raise FileContainerError("encrypted container domain does not match")
    if max_plaintext_bytes is not None and max_plaintext_bytes < 0:
        raise ValueError("max_plaintext_bytes cannot be negative")

    cipher = AESGCM(derive_domain_key(master_key, domain))
    digest = hashlib.sha256()
    total = 0
    index = 0
    while True:
        record, record_raw = _read_json_header(source)
        try:
            nonce = base64.b64decode(record["nonce"], validate=True)
            record_index = int(record["chunk_index"])
            final = record["final"]
            record_version = int(record["format_version"])
            record_key_id = str(record["key_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FileContainerError("invalid encrypted chunk header") from exc
        if (
            len(nonce) != NONCE_BYTES
            or record_index != index
            or not isinstance(final, bool)
            or record_version != FORMAT_VERSION
            or record_key_id != key_id
        ):
            raise FileContainerError("invalid encrypted chunk sequence")
        encrypted_size = _U32.unpack(_read_exact(source, _U32.size))[0]
        if not TAG_BYTES <= encrypted_size <= chunk_size + TAG_BYTES:
            raise FileContainerError("invalid encrypted chunk length")
        encrypted = _read_exact(source, encrypted_size)
        try:
            plain = cipher.decrypt(nonce, encrypted, MAGIC + global_raw + record_raw)
        except InvalidTag as exc:
            raise FileContainerError("encrypted container authentication failed") from exc
        if max_plaintext_bytes is not None and total + len(plain) > max_plaintext_bytes:
            raise FileContainerError("encrypted container exceeds plaintext limit")
        sink.write(plain)
        digest.update(plain)
        total += len(plain)
        index += 1
        if final:
            if source.read(1):
                raise FileContainerError("encrypted container has trailing data")
            break

    return ContainerInfo(
        object_id=object_id,
        key_id=key_id,
        domain=domain,
        plaintext_size=total,
        chunks=index,
        plaintext_sha256=digest.hexdigest(),
    )


def encrypt_file(
    source: str | Path,
    target_dir: str | Path,
    master_key: bytes,
    *,
    key_id: str,
    domain: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> tuple[Path, ContainerInfo]:
    """Encrypt a file under a random object name; remove partial output on failure."""
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    writer = None
    for _ in range(8):
        object_id = uuid.uuid4().hex
        target = target_dir / f"{object_id}.tzenc"
        try:
            writer = target.open("xb")
            break
        except FileExistsError:
            continue
    if writer is None:
        raise FileExistsError("could not reserve a random encrypted object name")
    try:
        with source.open("rb") as reader, writer:
            info = encrypt_stream(
                reader,
                writer,
                master_key,
                key_id=key_id,
                domain=domain,
                object_id=object_id,
                chunk_size=chunk_size,
            )
            writer.flush()
            os.fsync(writer.fileno())
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target, info


def decrypt_file(
    container: str | Path,
    target: str | Path,
    master_key: bytes,
    *,
    expected_key_id: str | None = None,
    expected_domain: str | None = None,
    max_plaintext_bytes: int | None = None,
) -> ContainerInfo:
    """Decrypt to a new file and remove all partial plaintext after any failure."""
    container, target = Path(container), Path(target)
    if not container.is_file():
        raise FileNotFoundError(container)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        writer = target.open("xb")
    except FileExistsError:
        raise FileExistsError(target) from None
    try:
        with container.open("rb") as reader, writer:
            info = _decrypt_stream(
                reader,
                writer,
                master_key,
                expected_key_id=expected_key_id,
                expected_domain=expected_domain,
                max_plaintext_bytes=max_plaintext_bytes,
            )
            writer.flush()
            os.fsync(writer.fileno())
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return info


def decrypt_to_bytes(
    container: str | Path,
    master_key: bytes,
    *,
    expected_key_id: str | None = None,
    expected_domain: str | None = None,
    max_plaintext_bytes: int = 32 * 1024 * 1024,
) -> tuple[bytes, ContainerInfo]:
    """Authenticate a bounded container fully before returning plaintext to callers."""
    import io

    output = io.BytesIO()
    with Path(container).open("rb") as reader:
        info = _decrypt_stream(
            reader,
            output,
            master_key,
            expected_key_id=expected_key_id,
            expected_domain=expected_domain,
            max_plaintext_bytes=max_plaintext_bytes,
        )
    return output.getvalue(), info
