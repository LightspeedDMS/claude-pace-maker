"""
Secrets database management module.

Provides CRUD operations for storing and retrieving secrets in a local SQLite database.
Database location: ~/.pace-maker/secrets.db
File permissions: 0600 (owner read/write only)
"""

import os
import sqlite3
from typing import List, Dict, Any, Optional


def _init_database(db_path: str) -> None:
    """
    Initialize the secrets database with schema.

    Creates the secrets table, metrics table, and indexes if they don't exist.
    Sets file permissions to 0600 (owner read/write only).

    Args:
        db_path: Path to the SQLite database file
    """
    # Create database file if it doesn't exist
    conn = sqlite3.connect(db_path)

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
    conn.close()

    # Set secure file permissions (owner read/write only)
    os.chmod(db_path, 0o600)


def _find_existing_secret(db_path: str, secret_type: str, value: str) -> Optional[int]:
    """
    Find existing secret with same type and value.

    Args:
        db_path: Path to the SQLite database file
        secret_type: Type of secret ("text" or "file")
        value: The secret value to search for

    Returns:
        The ID of the existing secret, or None if not found
    """
    # If database doesn't exist yet, no secrets can exist
    if not os.path.exists(db_path):
        return None

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id FROM secrets WHERE type = ? AND value = ?", (secret_type, value)
    )
    result = cursor.fetchone()
    conn.close()

    return result[0] if result else None


def create_secret(db_path: str, secret_type: str, value: str) -> int:
    """
    Create a new secret in the database, or return existing ID if duplicate.

    Args:
        db_path: Path to the SQLite database file
        secret_type: Type of secret ("text" or "file")
        value: The secret value to store

    Returns:
        The ID of the newly created secret, or existing secret ID if duplicate
    """
    # Initialize database if needed
    _init_database(db_path)

    # Check if this exact secret already exists
    existing_id = _find_existing_secret(db_path, secret_type, value)
    if existing_id is not None:
        return existing_id

    # Create new secret
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute(
        "INSERT INTO secrets (type, value) VALUES (?, ?)", (secret_type, value)
    )

    secret_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return secret_id


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

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT id, type, value, created_at FROM secrets ORDER BY id")
    rows = cursor.fetchall()

    conn.close()

    # Convert rows to dictionaries
    return [dict(row) for row in rows]


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

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT value FROM secrets")
    rows = cursor.fetchall()

    conn.close()

    # Extract just the values
    return [row[0] for row in rows]


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

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("DELETE FROM secrets WHERE id = ?", (secret_id,))
    deleted_count = cursor.rowcount

    conn.commit()
    conn.close()

    return deleted_count > 0


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

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("DELETE FROM secrets")
    deleted_count = cursor.rowcount

    conn.commit()
    conn.close()

    return deleted_count


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

    conn = sqlite3.connect(db_path)
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
    conn.close()

    return deleted_count
