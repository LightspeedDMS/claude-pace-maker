"""
Secrets database management module.

Provides CRUD operations for storing and retrieving secrets in a local SQLite database.
Database location: configured via db_path parameter (typically ~/.claude-pace-maker/usage.db)
File permissions: 0600 (owner read/write only)
"""

import os
import sqlite3
from typing import List, Dict, Any

_initialized_dbs: set = set()


def _init_database(db_path: str) -> None:
    """
    Initialize the secrets database with schema.

    Creates the secrets table, metrics table, and indexes if they don't exist.
    Sets file permissions to 0600 (owner read/write only).

    Args:
        db_path: Path to the SQLite database file
    """
    if db_path in _initialized_dbs:
        return

    # Create database file if it doesn't exist
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        # Create secrets table
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS secrets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
            )
        """
        )

        # Create index on type column for performance
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_secrets_type ON secrets(type)
        """
        )

        # Deduplicate existing rows before adding UNIQUE constraint
        # (required for migration of databases with pre-existing duplicates)
        conn.execute(
            """
            DELETE FROM secrets
            WHERE id NOT IN (
                SELECT MIN(id) FROM secrets GROUP BY type, value
            )
        """
        )

        # Add unique constraint on (type, value) to prevent duplicates at DB level
        # Use CREATE UNIQUE INDEX IF NOT EXISTS for idempotent schema migration
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_secrets_type_value ON secrets(type, value)
        """
        )

        # Create secrets metrics table for tracking masking statistics
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS secrets_metrics (
                bucket_timestamp INTEGER PRIMARY KEY,
                secrets_masked_count INTEGER DEFAULT 0
            )
        """
        )

        conn.commit()
    finally:
        conn.close()

    # Set secure file permissions (owner read/write only)
    os.chmod(db_path, 0o600)

    _initialized_dbs.add(db_path)


def create_secret(db_path: str, secret_type: str, value: str) -> int:
    """
    Create a new secret in the database, or return existing ID if duplicate.

    Uses INSERT OR IGNORE with a UNIQUE constraint on (type, value) to
    atomically prevent duplicates without TOCTOU race conditions.

    Args:
        db_path: Path to the SQLite database file
        secret_type: Type of secret ("text" or "file")
        value: The secret value to store

    Returns:
        The ID of the newly created secret, or existing secret ID if duplicate
    """
    _init_database(db_path)

    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO secrets (type, value) VALUES (?, ?)",
            (secret_type, value),
        )
        if cursor.rowcount == 0:
            # Already existed — fetch the existing ID
            cursor.execute(
                "SELECT id FROM secrets WHERE type = ? AND value = ?",
                (secret_type, value),
            )
            row = cursor.fetchone()
            secret_id = row[0] if row else -1
        else:
            secret_id = cursor.lastrowid
        conn.commit()
        return secret_id
    finally:
        conn.close()


def list_secrets(db_path: str) -> List[Dict[str, Any]]:
    """
    List all secrets with their metadata.

    Args:
        db_path: Path to the SQLite database file

    Returns:
        List of dictionaries containing id, type, value, and created_at
    """
    # Initialize database if needed
    _init_database(db_path)

    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, type, value, created_at FROM secrets ORDER BY id")
        rows = cursor.fetchall()
        # Convert rows to dictionaries
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_all_secrets(db_path: str) -> List[str]:
    """
    Get all secret values (without metadata).

    Args:
        db_path: Path to the SQLite database file

    Returns:
        List of secret values as strings
    """
    # Initialize database if needed
    _init_database(db_path)

    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM secrets")
        rows = cursor.fetchall()
        # Extract just the values
        return [row[0] for row in rows]
    finally:
        conn.close()


def remove_secret(db_path: str, secret_id: int) -> bool:
    """
    Remove a secret by ID.

    Args:
        db_path: Path to the SQLite database file
        secret_id: The ID of the secret to remove

    Returns:
        True if a secret was deleted, False if no matching ID was found
    """
    # Initialize database if needed
    _init_database(db_path)

    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM secrets WHERE id = ?", (secret_id,))
        deleted_count = cursor.rowcount
        conn.commit()
        return deleted_count > 0
    finally:
        conn.close()


def clear_all_secrets(db_path: str) -> int:
    """
    Clear all secrets from the database.

    Args:
        db_path: Path to the SQLite database file

    Returns:
        The number of secrets deleted
    """
    # Initialize database if needed
    _init_database(db_path)

    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM secrets")
        deleted_count = cursor.rowcount
        conn.commit()
        return deleted_count
    finally:
        conn.close()


def deduplicate_secrets(db_path: str) -> int:
    """
    Remove duplicate secrets, keeping the lowest ID for each (type, value) pair.

    Args:
        db_path: Path to the SQLite database file

    Returns:
        The number of duplicate secrets deleted
    """
    # Initialize database if needed
    _init_database(db_path)

    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        cursor = conn.cursor()
        # Delete all but the minimum ID for each (type, value) pair
        cursor.execute(
            """
            DELETE FROM secrets
            WHERE id NOT IN (
                SELECT MIN(id) FROM secrets GROUP BY type, value
            )
        """
        )
        deleted_count = cursor.rowcount
        conn.commit()
        return deleted_count
    finally:
        conn.close()
