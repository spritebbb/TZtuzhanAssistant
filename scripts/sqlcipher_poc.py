"""Run the P3-04 SQLCipher proof only against disposable temporary data."""
from __future__ import annotations

import json
import secrets
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.storage.connect import connect_database, create_encrypted_copy


def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="tztuzhan-sqlcipher-poc-") as raw:
        root = Path(raw)
        source = root / "plain.db"
        target = root / "encrypted.db"
        conn = connect_database(source)
        conn.execute("CREATE TABLE proof(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO proof(value) VALUES ('throwaway-data')")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()
        evidence = create_encrypted_copy(source, target, secrets.token_bytes(32))
        return {
            "ok": True,
            "cipher_version": evidence["cipher_version"],
            "schema_preserved": evidence["source_manifest"] == evidence["target_manifest"],
            "temporary_only": True,
        }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
