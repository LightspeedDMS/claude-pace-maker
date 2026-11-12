"""
Secrets database management module.

Provides CRUD operations for storing and retrieving secrets in a local SQLite database.
Database location: configured via db_path parameter (typically ~/.claude-pace-maker/usage.db)
File permissions: 0600 (owner read/write only)

Shared data directory (story #187): when the caller passes the usage.db that
lives in the configured shared dir, the secrets are NOT stored there. They go
to the per-user ``~/.claude-pace-maker/secrets.db`` (0600), and on first use
the secrets already held in the per-user ``usage.db`` are copied over (copy,
not move: the old file is left intact for rollback). Any other db_path is used
exactly as given, so default installs behave byte-for-byte as before.
"""

import os
import sqlite3
from typing import List, Dict, Any, Optional

from ..data_dir import (
    SharedDataDirError,
    is_shared_path,
    per_user_dir,
    per_user_secrets_db_path,
)
from ..logger import log_warning
from .masking import is_degenerate_secret

_initialized_dbs: set = set()

_SECRETS_DDL = """
    CREATE TABLE IF NOT EXISTS secrets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        value TEXT NOT NULL,
        created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
    )
"""


def _secure_per_user_file(path: str) -> None:
    """chmod 0600 a per-user secrets file. Never allowed on a shared file."""
    if is_shared_path(path):
        raise SharedDataDirError(
            f"pace-maker: refusing to chmod 0600 shared file '{path}'"
        )
    os.chmod(path, 0o600)


def _copy_legacy_secrets(secrets_db: str) -> None:
    """One-time copy of the per-user usage.db secrets into a fresh secrets.db.

    Runs only while ``PRAGMA user_version`` of secrets.db is 0, so secrets the
    user later removes do not come back. Missing legacy DB/table = nothing to copy.
    """
    conn = sqlite3.connect(secrets_db, timeout=5.0)
    try:
        if conn.execute("PRAGMA user_version").fetchone()[0] != 0:
            return
        conn.execute(_SECRETS_DDL)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_secrets_type_value "
            "ON secrets(type, value)"
        )
        legacy = os.path.join(per_user_dir(), "usage.db")
        if os.path.exists(legacy):
            src = sqlite3.connect(legacy, timeout=5.0)
            try:
                has_table = src.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='secrets'"
                ).fetchone()
                rows = (
                    src.execute(
                        "SELECT type, value, created_at FROM secrets"
                    ).fetchall()
                    if has_table
                    else []
                )
            finally:
                src.close()
            conn.executemany(
                "INSERT OR IGNORE INTO secrets (type, value, created_at) VALUES (?, ?, ?)",
                rows,
            )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    finally:
        conn.close()


def _create_private_file(path: str) -> None:
    """Make sure ``path`` exists and is 0600 BEFORE any secret is written to it."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)


def _secrets_from_former_shared_mode(requested: str, already: List[str]) -> List[str]:
    """Extra values to MASK when shared mode was used before and is now off.

    Secrets declared while shared mode was on live only in the per-user
    ``secrets.db``. Back in default mode, masking reads the per-user
    ``usage.db`` again and would silently stop masking them, so for exactly
    that file (and only if secrets.db exists, i.e. shared mode was once used)
    its values are added. Users who never enabled shared mode have no
    secrets.db, so nothing changes for them. list/remove still operate on
    usage.db only.
    """
    secrets_db = per_user_secrets_db_path()
    if (
        is_shared_path(requested)
        or os.path.abspath(requested) != os.path.join(per_user_dir(), "usage.db")
        or not os.path.exists(secrets_db)
    ):
        return []
    try:
        conn = sqlite3.connect(secrets_db, timeout=5.0)
        try:
            rows = conn.execute("SELECT value FROM secrets").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        log_warning("secrets", f"Cannot read {secrets_db} for masking", e)
        return []
    seen = set(already)
    return [r[0] for r in rows if r[0] not in seen and not is_degenerate_secret(r[0])]


def _init_database(db_path: str) -> str:
    """
    Initialize the secrets database with schema.

    Creates the secrets table, metrics table, and indexes if they don't exist.
    Sets file permissions to 0600 (owner read/write only).

    Args:
        db_path: Path to the SQLite database file

    Returns:
        The path actually holding the secrets: ``db_path`` itself, except in
        shared mode for the shared usage.db, where it is the per-user
        secrets.db.
    """
    if is_shared_path(db_path):
        db_path = per_user_secrets_db_path()
        if db_path not in _initialized_dbs:
            os.makedirs(per_user_dir(), exist_ok=True)
            _create_private_file(db_path)  # 0600 before the first secret lands
            _copy_legacy_secrets(db_path)
    if db_path in _initialized_dbs:
        return db_path

    # Create database file if it doesn't exist
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        # Create secrets table
        conn.execute(_SECRETS_DDL)

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
    _secure_per_user_file(db_path)

    _initialized_dbs.add(db_path)
    return db_path


def create_secret(db_path: str, secret_type: str, value: str) -> Optional[int]:
    """
    Create a new secret in the database, or return None if already exists.

    Uses INSERT OR IGNORE with a UNIQUE constraint on (type, value) to
    atomically prevent duplicates without TOCTOU race conditions.

    Args:
        db_path: Path to the SQLite database file
        secret_type: Type of secret ("text" or "file")
        value: The secret value to store

    Returns:
        The ID of the newly created secret, or None if the secret already existed
        (duplicate) OR was refused as degenerate (bug #160: empty, or a fragment
        of the mask marker). Callers must check for None to distinguish new vs
        existing. Never raises for a refused value; the value is never logged.
    """
    if is_degenerate_secret(value):
        log_warning(
            "secrets",
            f"Refused to store a degenerate {secret_type} secret (empty or a "
            "fragment of the mask marker); value not logged",
        )
        return None

    db_path = _init_database(db_path)

    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO secrets (type, value) VALUES (?, ?)",
            (secret_type, value),
        )
        if cursor.rowcount == 0:
            # Already existed — return None to signal duplicate (not newly stored)
            conn.commit()
            return None
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
    db_path = _init_database(db_path)

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
    Get all secret values (without metadata) for MASKING.

    Degenerate stored values (bug #160: empty, or a fragment of the mask
    marker) are excluded so they can never mask ordinary text or re-mask
    already-masked values. The rows themselves are untouched and still appear
    in ``list_secrets`` so the owner can remove them.

    Args:
        db_path: Path to the SQLite database file

    Returns:
        List of usable secret values as strings
    """
    # Initialize database if needed
    requested = db_path
    db_path = _init_database(db_path)

    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM secrets")
        rows = cursor.fetchall()
        # Extract just the values, skipping degenerate ones
        values = [row[0] for row in rows if not is_degenerate_secret(row[0])]
    finally:
        conn.close()
    return values + _secrets_from_former_shared_mode(requested, values)


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
    db_path = _init_database(db_path)

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
    db_path = _init_database(db_path)

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
    db_path = _init_database(db_path)

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
