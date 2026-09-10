"""P3-04 C: encrypted file container and process-local vector index."""
from __future__ import annotations

import json
import shutil
import struct
import sys
import tempfile
from pathlib import Path

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.storage.connect import SQLITE_HEADER
from backend.storage.file_container import (
    FORMAT_VERSION,
    MAGIC,
    FileContainerError,
    decrypt_file,
    decrypt_to_bytes,
    derive_domain_key,
    encrypt_file,
)
from backend.storage.vector_embeddings import EncryptedVectorStore


MASTER_KEY = bytes(range(32))
KEY_ID = "test-key-v1"


def test_domain_keys_are_stable_and_separated() -> None:
    media = derive_domain_key(MASTER_KEY, "media")
    assert media == derive_domain_key(MASTER_KEY, "media")
    assert media != derive_domain_key(MASTER_KEY, "backup")
    assert len(media) == 32


def test_multichunk_file_roundtrip_uses_random_object_name(root: Path) -> None:
    original_name = "private-profile-and-photo.txt"
    plaintext = ("不应出现在密文里的内容。" * 50).encode("utf-8")
    source = root / original_name
    source.write_bytes(plaintext)

    container, encrypted = encrypt_file(
        source,
        root / "objects",
        MASTER_KEY,
        key_id=KEY_ID,
        domain="persona",
        chunk_size=37,
    )

    raw = container.read_bytes()
    assert container.name != original_name and container.suffix == ".tzenc"
    assert original_name.encode() not in raw and plaintext[:40] not in raw
    assert encrypted.chunks > 1 and encrypted.plaintext_size == len(plaintext)
    offset = len(MAGIC)
    global_size = struct.unpack(">I", raw[offset : offset + 4])[0]
    offset += 4
    global_header = json.loads(raw[offset : offset + global_size])
    offset += global_size
    record_size = struct.unpack(">I", raw[offset : offset + 4])[0]
    offset += 4
    record_header = json.loads(raw[offset : offset + record_size])
    assert global_header == {
        "chunk_size": 37,
        "domain": "persona",
        "format_version": FORMAT_VERSION,
        "key_id": KEY_ID,
        "object_id": encrypted.object_id,
    }
    assert set(record_header) == {
        "chunk_index", "final", "format_version", "key_id", "nonce"
    }
    assert record_header["chunk_index"] == 0 and record_header["key_id"] == KEY_ID

    restored, decrypted = decrypt_to_bytes(
        container,
        MASTER_KEY,
        expected_key_id=KEY_ID,
        expected_domain="persona",
    )
    assert restored == plaintext
    assert decrypted.plaintext_sha256 == encrypted.plaintext_sha256
    with pytest.raises(FileContainerError, match="key id"):
        decrypt_to_bytes(container, MASTER_KEY, expected_key_id="another-key")
    with pytest.raises(FileContainerError, match="domain"):
        decrypt_to_bytes(container, MASTER_KEY, expected_domain="media")
    with pytest.raises(FileContainerError, match="plaintext limit"):
        decrypt_to_bytes(container, MASTER_KEY, max_plaintext_bytes=len(plaintext) - 1)

    target = root / "restored" / original_name
    file_info = decrypt_file(container, target, MASTER_KEY)
    assert target.read_bytes() == plaintext and file_info == decrypted


def test_wrong_key_tamper_and_truncation_leave_no_plaintext(root: Path) -> None:
    source = root / "secret.bin"
    source.write_bytes(b"private-payload-" * 100)
    container, _ = encrypt_file(
        source, root / "objects", MASTER_KEY, key_id=KEY_ID, domain="media", chunk_size=64
    )

    with pytest.raises(FileContainerError):
        decrypt_to_bytes(container, b"x" * 32)

    variants: list[Path] = []
    tampered = root / "tampered.tzenc"
    tampered_raw = bytearray(container.read_bytes())
    tampered_raw[-1] ^= 1
    tampered.write_bytes(tampered_raw)
    variants.append(tampered)
    truncated = root / "truncated.tzenc"
    truncated.write_bytes(container.read_bytes()[:-7])
    variants.append(truncated)

    for index, broken in enumerate(variants):
        target = root / f"must-not-remain-{index}.bin"
        with pytest.raises(FileContainerError):
            decrypt_file(broken, target, MASTER_KEY)
        assert not target.exists()


def test_decrypt_refuses_to_overwrite_existing_file(root: Path) -> None:
    source = root / "source.bin"
    source.write_bytes(b"payload")
    container, _ = encrypt_file(
        source, root / "objects", MASTER_KEY, key_id=KEY_ID, domain="attachment"
    )
    target = root / "existing.bin"
    target.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        decrypt_file(container, target, MASTER_KEY)
    assert target.read_bytes() == b"keep"


def test_encrypted_vectors_rebuild_and_search_in_memory(root: Path) -> None:
    database = root / "vectors.db"
    with EncryptedVectorStore(database, MASTER_KEY, key_id=KEY_ID) as store:
        store.upsert("fact:1", "chunk:1", "model-a", [1.0, 0.0, 0.0], "hash-1")
        store.upsert("fact:2", "chunk:1", "model-a", [0.0, 1.0, 0.0], "hash-2")
        store.upsert("fact:3", "chunk:1", "model-b", [1.0, 0.0, 0.0], "hash-3")
        with pytest.raises(ValueError, match="dimension"):
            store.upsert("fact:4", "chunk:1", "model-a", [1.0, 0.0], "hash-4")
        assert store.count() == 3
        index = store.load_memory_index(model_id="model-a")
        assert index.size == 2
        hits = index.search([0.9, 0.1, 0.0], limit=2)
        assert [hit.record.source_id for hit in hits] == ["fact:1", "fact:2"]
        assert hits[0].score > hits[1].score
        assert store.delete_source("fact:2") == 1

    raw = database.read_bytes()
    assert raw[: len(SQLITE_HEADER)] != SQLITE_HEADER
    for forbidden in (b"fact:1", b"model-a", b"hash-1", b"vector_embeddings"):
        assert forbidden not in raw

    with EncryptedVectorStore(database, MASTER_KEY, key_id=KEY_ID) as reopened:
        assert reopened.count() == 2
        assert reopened.load_memory_index().size == 2
    with pytest.raises(sqlcipher.DatabaseError):
        EncryptedVectorStore(database, b"x" * 32, key_id=KEY_ID)
    with pytest.raises(ValueError, match="key id"):
        EncryptedVectorStore(database, MASTER_KEY, key_id="different-key-id")


def main() -> None:
    workspace_tmp = ROOT / ".tmp"
    workspace_tmp.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="encrypted-storage-", dir=workspace_tmp))
    try:
        test_domain_keys_are_stable_and_separated()
        for name, test in (
            ("roundtrip", test_multichunk_file_roundtrip_uses_random_object_name),
            ("integrity", test_wrong_key_tamper_and_truncation_leave_no_plaintext),
            ("overwrite", test_decrypt_refuses_to_overwrite_existing_file),
            ("vectors", test_encrypted_vectors_rebuild_and_search_in_memory),
        ):
            case = root / name
            case.mkdir()
            test(case)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("[OK] encrypted file container + in-memory vector index")


if __name__ == "__main__":
    main()
