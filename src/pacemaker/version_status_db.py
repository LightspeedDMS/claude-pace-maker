"""
Version status SQLite database layer for Story #66.

Stores the result of the Claude Code version check performed at SessionStart.
The claude-usage monitor reads this DB to display version status.

Public API:
- resolve_db_path() -> str
    Return DB file path from env var (non-empty, must end in .db) or production
    default. Raises RuntimeError in test mode when env var is absent.

- record_status(current, minimum, blocked, reason) -> None
    Upsert the single status row (id=1).

- read_status() -> dict | None
    Read the single status row.
    Returns None when DB does not exist OR on any read/resolution failure.
    All failures logged at DEBUG level (canonical cross-process reader pattern
    per CLAUDE.md). Intentional fail-open: version status must never block hooks.
"""

import os
import sqlite3
import time
from typing import Optional

from .logger import log_debug

# ── Environment variable names ────────────────────────────────────────────────
_ENV_STATUS_PATH = "PACEMAKER_VERSION_STATUS_PATH"
_ENV_TEST_MODE = "PACEMAKER_TEST_MODE"

# ── Production DB path components ─────────────────────────────────────────────
_PROD_DB_DIR = ".claude-pace-maker"
_PROD_DB_FILE = "version_status.db"

# ── PRAGMA / connection values ────────────────────────────────────────────────
_BUSY_TIMEOUT_MS = 2000
_READ_TIMEOUT_SECONDS = 5.0
_JOURNAL_MODE = "WAL"
_SYNCHRONOUS = "NORMAL"

# ── DDL ───────────────────────────────────────────────────────────────────────
_DDL_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS version_status (
    id              INTEGER PRIMARY KEY,
    current_version TEXT,
    min_version     TEXT NOT NULL,
    blocked         INTEGER NOT NULL DEFAULT 0,
    reason          TEXT NOT NULL,
    checked_at      REAL NOT NULL
)
"""


def _production_db_path() -> str:
    """Compute the production DB path from the current home directory at call time."""
    return os.path.join(os.path.expanduser("~"), _PROD_DB_DIR, _PROD_DB_FILE)


def resolve_db_path() -> str:
    """Return the DB file path to use.

    Resolution order:
    1. PACEMAKER_VERSION_STATUS_PATH env var — validated non-empty and must end
       with '.db' (ensuring it is a file path, not a directory).
    2. Production default (~/.claude-pace-maker/version_status.db).

    Test-mode enforcement: if PACEMAKER_TEST_MODE=1 and
    PACEMAKER_VERSION_STATUS_PATH is not set, raises RuntimeError so tests
    never accidentally write to the production DB.

    Raises:
        RuntimeError: in test mode when env var is absent.
        ValueError: when env var is set but empty/whitespace-only or lacks .db suffix.
    """
    env_path = os.environ.get(_ENV_STATUS_PATH)
    if env_path is not None:
        stripped = env_path.strip()
        if not stripped:
            raise ValueError(f"{_ENV_STATUS_PATH} is set but contains only whitespace")
        normalized = os.path.normpath(stripped)
        if not normalized.endswith(".db"):
            raise ValueError(
                f"{_ENV_STATUS_PATH} must be a path ending in '.db', got: {normalized!r}"
            )
        return normalized

    if os.environ.get(_ENV_TEST_MODE) == "1":
        raise RuntimeError(
            "PACEMAKER_VERSION_STATUS_PATH must be set in test mode — "
            "conftest.py must provide a tmp path via monkeypatch.setenv"
        )

    return _production_db_path()


def _open_write_connection(db_path: str) -> sqlite3.Connection:
    """Open a WAL write connection with schema applied; caller closes it."""
    conn = sqlite3.connect(db_path, timeout=_READ_TIMEOUT_SECONDS)
    conn.execute(f"PRAGMA journal_mode={_JOURNAL_MODE}")
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    conn.execute(f"PRAGMA synchronous={_SYNCHRONOUS}")
    conn.commit()
    conn.execute(_DDL_CREATE_TABLE)
    conn.commit()
    return conn


def record_status(
    current: Optional[str],
    minimum: str,
    blocked: bool,
    reason: str,
) -> None:
    """Upsert the single version status row (id=1).

    Args:
        current:  Current installed version string, or None if probe failed.
        minimum:  Configured minimum version string.
        blocked:  True when the version check caused a hard block.
        reason:   One of: 'ok', 'below_minimum', 'probe_failed', 'parse_failed'.
    """
    db_path = resolve_db_path()
    parent_dir = os.path.dirname(db_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    conn = _open_write_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO version_status
                (id, current_version, min_version, blocked, reason, checked_at)
            VALUES (1, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                current_version = excluded.current_version,
                min_version     = excluded.min_version,
                blocked         = excluded.blocked,
                reason          = excluded.reason,
                checked_at      = excluded.checked_at
            """,
            (current, minimum, 1 if blocked else 0, reason, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def read_status() -> Optional[dict]:
    """Read the single version status row (fully fail-open).

    Returns None when:
    - resolve_db_path() raises (e.g. invalid env var, test-mode misconfiguration).
    - DB file does not exist (fresh install, version check not yet run).
    - Any sqlite3, OS, or filesystem error during the read.

    All failure modes logged at DEBUG level per the canonical cross-process
    reader contract (CLAUDE.md). Callers must treat None as "status unknown"
    and proceed normally.
    """
    try:
        db_path = resolve_db_path()
    except (RuntimeError, ValueError) as e:
        log_debug("version_status_db", f"resolve_db_path failed: {e}")
        return None

    try:
        if not os.path.exists(db_path):
            return None

        conn = sqlite3.connect(db_path, timeout=_READ_TIMEOUT_SECONDS)
        conn.execute(f"PRAGMA journal_mode={_JOURNAL_MODE}")
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT current_version, min_version, blocked, reason, checked_at "
                "FROM version_status WHERE id = 1"
            )
            row = cursor.fetchone()
        finally:
            conn.close()

        if row is None:
            return None

        return {
            "current_version": row["current_version"],
            "min_version": row["min_version"],
            "blocked": row["blocked"],
            "reason": row["reason"],
            "checked_at": row["checked_at"],
        }
    except (sqlite3.Error, OSError) as e:
        # Fail-open: log at DEBUG per cross-process reader contract (CLAUDE.md).
        log_debug("version_status_db", f"Failed to read version status: {e}")
        return None
