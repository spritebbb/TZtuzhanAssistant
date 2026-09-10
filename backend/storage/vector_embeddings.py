"""Encrypted vector persistence with an in-memory linear search index.

Only embeddings and stable source identifiers are stored.  Source text remains
authoritative in the encrypted application databases and is never copied here.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .connect import connect_database
from .file_container import derive_domain_key


VECTOR_FORMAT_VERSION = 1
_DIMENSION = struct.Struct(">I")


@dataclass(frozen=True)
class VectorRecord:
    source_id: str
    chunk_id: str
    model_id: str
    vector: tuple[float, ...]
    content_hash: str
    version: int


@dataclass(frozen=True)
class VectorHit:
    record: VectorRecord
    score: float


def _clean_identifier(value: str, label: str) -> str:
    value = str(value or "").strip()
    if not value or len(value) > 240 or "\x00" in value:
        raise ValueError(f"{label} must contain 1-240 characters")
    return value


def _clean_vector(vector: Sequence[float]) -> tuple[float, ...]:
    values = tuple(float(item) for item in vector)
    if not values or len(values) > 65536 or not all(math.isfinite(item) for item in values):
        raise ValueError("vector must contain finite values and a valid dimension")
    return values


def _pack_vector(vector: Sequence[float]) -> bytes:
    values = _clean_vector(vector)
    return _DIMENSION.pack(len(values)) + struct.pack(f">{len(values)}f", *values)


def _unpack_vector(blob: bytes) -> tuple[float, ...]:
    if len(blob) < _DIMENSION.size:
        raise ValueError("stored vector is truncated")
    dimension = _DIMENSION.unpack(blob[: _DIMENSION.size])[0]
    if not 1 <= dimension <= 65536 or len(blob) != _DIMENSION.size + dimension * 4:
        raise ValueError("stored vector has an invalid dimension")
    return tuple(struct.unpack(f">{dimension}f", blob[_DIMENSION.size :]))


class MemoryVectorIndex:
    """Process-local cosine search; closing the process discards the index."""

    def __init__(self, records: Iterable[VectorRecord]) -> None:
        self._records = tuple(records)

    @property
    def size(self) -> int:
        return len(self._records)

    def search(self, query: Sequence[float], *, limit: int = 5) -> list[VectorHit]:
        values = _clean_vector(query)
        if limit <= 0:
            return []
        query_norm = math.sqrt(sum(value * value for value in values))
        hits: list[VectorHit] = []
        for record in self._records:
            if len(record.vector) != len(values):
                continue
            record_norm = math.sqrt(sum(value * value for value in record.vector))
            score = 0.0
            if query_norm and record_norm:
                score = sum(a * b for a, b in zip(values, record.vector)) / (
                    query_norm * record_norm
                )
            hits.append(VectorHit(record=record, score=score))
        hits.sort(
            key=lambda hit: (
                -hit.score,
                hit.record.source_id,
                hit.record.chunk_id,
                hit.record.model_id,
            )
        )
        return hits[:limit]


class EncryptedVectorStore:
    """SQLCipher-backed vector records that rebuild a linear index in memory."""

    def __init__(self, database: str | Path, master_key: bytes, *, key_id: str) -> None:
        self._database = Path(database)
        self._key_id = _clean_identifier(key_id, "key_id")
        self._database.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            with self._database.open("xb"):
                created = True
        except FileExistsError:
            pass
        try:
            self._conn = connect_database(
                self._database,
                encrypted_key=derive_domain_key(master_key, "vector"),
                row_factory=True,
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS storage_meta ("
                "key TEXT PRIMARY KEY,value TEXT NOT NULL)"
            )
            format_row = self._conn.execute(
                "SELECT value FROM storage_meta WHERE key='format_version'"
            ).fetchone()
            if format_row is not None and int(format_row[0]) != VECTOR_FORMAT_VERSION:
                raise ValueError("encrypted vector store format is unsupported")
            current = self._conn.execute(
                "SELECT value FROM storage_meta WHERE key='key_id'"
            ).fetchone()
            if current is not None and str(current[0]) != self._key_id:
                raise ValueError("encrypted vector store key id does not match")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS vector_embeddings (
                    source_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    vector_blob BLOB NOT NULL,
                    content_hash TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    PRIMARY KEY (source_id, chunk_id, model_id)
                );
                CREATE INDEX IF NOT EXISTS idx_vector_embeddings_model
                    ON vector_embeddings(model_id, source_id);
                CREATE TABLE IF NOT EXISTS vector_models (
                    model_id TEXT PRIMARY KEY,
                    dimension INTEGER NOT NULL,
                    version INTEGER NOT NULL
                );
                """
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO storage_meta(key,value) VALUES ('format_version',?)",
                (str(VECTOR_FORMAT_VERSION),),
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO storage_meta(key,value) VALUES ('key_id',?)",
                (self._key_id,),
            )
            self._conn.commit()
        except Exception:
            conn = getattr(self, "_conn", None)
            if conn is not None:
                try:
                    conn.rollback()
                finally:
                    conn.close()
            if created:
                self._database.unlink(missing_ok=True)
            raise

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "EncryptedVectorStore":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def upsert(
        self,
        source_id: str,
        chunk_id: str,
        model_id: str,
        vector: Sequence[float],
        content_hash: str,
        *,
        version: int = VECTOR_FORMAT_VERSION,
    ) -> None:
        source_id = _clean_identifier(source_id, "source_id")
        chunk_id = _clean_identifier(chunk_id, "chunk_id")
        model_id = _clean_identifier(model_id, "model_id")
        content_hash = _clean_identifier(content_hash, "content_hash")
        if not isinstance(version, int) or version < 1:
            raise ValueError("version must be a positive integer")
        values = _clean_vector(vector)
        dimension_row = self._conn.execute(
            "SELECT dimension FROM vector_models WHERE model_id=?", (model_id,)
        ).fetchone()
        if dimension_row is not None and int(dimension_row[0]) != len(values):
            raise ValueError("vector dimension does not match the stored model")
        try:
            self._conn.execute(
                "INSERT OR IGNORE INTO vector_models(model_id,dimension,version) VALUES (?,?,?)",
                (model_id, len(values), VECTOR_FORMAT_VERSION),
            )
            self._conn.execute(
                "INSERT INTO vector_embeddings "
                "(source_id,chunk_id,model_id,vector_blob,content_hash,version) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(source_id,chunk_id,model_id) DO UPDATE SET "
                "vector_blob=excluded.vector_blob,content_hash=excluded.content_hash,"
                "version=excluded.version",
                (
                    source_id,
                    chunk_id,
                    model_id,
                    _pack_vector(values),
                    content_hash,
                    version,
                ),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def delete_source(self, source_id: str) -> int:
        source_id = _clean_identifier(source_id, "source_id")
        cursor = self._conn.execute(
            "DELETE FROM vector_embeddings WHERE source_id=?", (source_id,)
        )
        self._conn.commit()
        return int(cursor.rowcount)

    def count(self) -> int:
        return int(self._conn.execute("SELECT count(*) FROM vector_embeddings").fetchone()[0])

    def load_memory_index(self, *, model_id: str | None = None) -> MemoryVectorIndex:
        params: tuple[str, ...] = ()
        where = ""
        if model_id is not None:
            model_id = _clean_identifier(model_id, "model_id")
            where = " WHERE model_id=?"
            params = (model_id,)
        rows = self._conn.execute(
            "SELECT source_id,chunk_id,model_id,vector_blob,content_hash,version "
            f"FROM vector_embeddings{where} ORDER BY source_id,chunk_id,model_id",
            params,
        ).fetchall()
        return MemoryVectorIndex(
            VectorRecord(
                source_id=str(row[0]),
                chunk_id=str(row[1]),
                model_id=str(row[2]),
                vector=_unpack_vector(bytes(row[3])),
                content_hash=str(row[4]),
                version=int(row[5]),
            )
            for row in rows
        )
