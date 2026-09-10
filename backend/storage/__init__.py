"""Shared persistence primitives."""

from .connect import (
    SQLCipherUnavailable,
    connect_database,
    create_encrypted_copy,
    database_manifest,
    verify_encrypted_database,
)

__all__ = [
    "SQLCipherUnavailable",
    "connect_database",
    "create_encrypted_copy",
    "database_manifest",
    "verify_encrypted_database",
]
