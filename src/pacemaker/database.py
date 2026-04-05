#!/usr/bin/env python3
"""
Database operations for Credit-Aware Adaptive Throttling.

Manages SQLite database for storing usage snapshots and calculating
consumption rates over time.
"""

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Callable, TypeVar, Generator
from pathlib import Path

from .logger import log_error, log_warning
from .constants import BLOCKAGE_CATEGORIES

# Type variable for generic return type
T = TypeVar("T")


# Constants for concurrency handling
DB_TIMEOUT = 5.0  # Wait up to 5 seconds for lock
MAX_RETRIES = 3  # Retry up to 3 times on lock
RETRY_DELAY = 0.1  # Initial delay between retries (100ms)


# Cache of db_paths that have already been initialized to avoid repeated
# executescript() calls which require an exclusive lock and cause contention.
_initialized_dbs: set = set()


def reset_initialized_dbs() -> None:
    """Clear the initialization cache.

    Used by tests to ensure clean state between test runs. Must be called
    before each test that relies on a fresh DB initialization.
    """
    _initialized_dbs.clear()


# Database schema
SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    five_hour_util REAL NOT NULL,
    five_hour_resets_at TEXT,
    seven_day_util REAL NOT NULL,
    seven_day_resets_at TEXT,
    session_id TEXT NOT NULL,
    created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_timestamp ON usage_snapshots(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_session ON usage_snapshots(session_id);

CREATE TABLE IF NOT EXISTS pacing_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    should_throttle INTEGER NOT NULL,
    delay_seconds INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_pacing_timestamp ON pacing_decisions(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_pacing_session ON pacing_decisions(session_id);

CREATE TABLE IF NOT EXISTS blockage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    category TEXT NOT NULL,
    reason TEXT NOT NULL,
    hook_type TEXT NOT NULL,
    session_id TEXT NOT NULL,
    details TEXT,
    created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_blockage_timestamp ON blockage_events(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_blockage_category ON blockage_events(category);

CREATE TABLE IF NOT EXISTS langfuse_metrics (
    bucket_timestamp INTEGER PRIMARY KEY,
    sessions_count INTEGER DEFAULT 0,
    traces_count INTEGER DEFAULT 0,
    spans_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS secrets_metrics (
    bucket_timestamp INTEGER PRIMARY KEY,
    secrets_masked_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS api_cache (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    timestamp REAL NOT NULL,
    five_hour_util REAL NOT NULL,
    five_hour_resets_at TEXT,
    seven_day_util REAL NOT NULL,
    seven_day_resets_at TEXT,
    raw_response TEXT
);

CREATE TABLE IF NOT EXISTS fallback_state_v2 (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    state TEXT NOT NULL DEFAULT 'normal',
    baseline_5h REAL DEFAULT 0.0,
    baseline_7d REAL DEFAULT 0.0,
    resets_at_5h TEXT,
    resets_at_7d TEXT,
    tier TEXT DEFAULT '5x',
    entered_at REAL,
    rollover_cost_5h REAL,
    rollover_cost_7d REAL,
    last_rollover_resets_5h TEXT,
    last_rollover_resets_7d TEXT
);

CREATE TABLE IF NOT EXISTS accumulated_costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    session_id TEXT NOT NULL,
    cost_dollars REAL NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_creation_tokens INTEGER,
    model_family TEXT
);

CREATE INDEX IF NOT EXISTS idx_accum_costs_ts ON accumulated_costs(timestamp);

CREATE TABLE IF NOT EXISTS backoff_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    consecutive_429s INTEGER NOT NULL DEFAULT 0,
    backoff_until REAL,
    last_success_time REAL
);

CREATE TABLE IF NOT EXISTS profile_cache (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    timestamp REAL NOT NULL,
    profile_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calibrated_coefficients (
    tier TEXT PRIMARY KEY,
    coefficient_5h REAL NOT NULL,
    coefficient_7d REAL NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    last_calibrated REAL
);

CREATE TABLE IF NOT EXISTS global_poll_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_poll_time REAL NOT NULL DEFAULT 0,
    last_poll_session TEXT
);

CREATE TABLE IF NOT EXISTS activity_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    event_code TEXT NOT NULL,
    status TEXT NOT NULL,
    session_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity_events(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_activity_code ON activity_events(event_code);

CREATE TABLE IF NOT EXISTS governance_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    event_type TEXT NOT NULL,
    project_name TEXT NOT NULL,
    session_id TEXT NOT NULL,
    feedback_text TEXT NOT NULL,
    created_at INTEGER DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_governance_timestamp ON governance_events(timestamp DESC);

CREATE TABLE IF NOT EXISTS codex_usage (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    primary_used_pct REAL NOT NULL,
    secondary_used_pct REAL NOT NULL,
    primary_resets_at INTEGER,
    secondary_resets_at INTEGER,
    plan_type TEXT,
    limit_id TEXT,
    timestamp REAL NOT NULL
);
"""


def record_activity_event(
    db_path: str,
    event_code: str,
    status: str,
    session_id: str,
) -> bool:
    """
    Record a pace-maker activity event in the database.

    Used for real-time activity visualization in the usage monitor.
    Records events from all hook types (PreToolUse, Stop, PostToolUse,
    UserPromptSubmit, SessionStart, SubagentStart, SubagentStop).

    Args:
        db_path: Path to SQLite database file
        event_code: 2-letter event code (IV, TD, CC, ST, CX, PA, PL, LF,
                    SS, SM, SE, SA, UP)
        status: Event status ('green', 'red', or 'blue')
        session_id: Unique identifier for the current session

    Returns:
        True if successful, False otherwise
    """
    try:
        timestamp = time.time()

        def operation(conn: sqlite3.Connection) -> bool:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO activity_events (timestamp, event_code, status, session_id)
                VALUES (?, ?, ?, ?)
                """,
                (timestamp, event_code, status, session_id),
            )
            return True

        return execute_with_retry(db_path, operation)

    except Exception as e:
        log_error("database", "Failed to record activity event", e)
        return False


def record_governance_event(
    db_path: str,
    event_type: str,
    project_name: str,
    session_id: str,
    feedback_text: str,
) -> bool:
    """
    Record a governance event (IV/TD/CC rejection) in the database.

    Used for real-time governance event feed in the usage monitor.

    Args:
        db_path: Path to SQLite database file
        event_type: Event type code ("IV", "TD", or "CC")
        project_name: Project name extracted from working directory
        session_id: Unique identifier for the current session
        feedback_text: Full rejection/feedback message

    Returns:
        True if successful, False otherwise
    """
    try:
        timestamp = time.time()

        def operation(conn: sqlite3.Connection) -> bool:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO governance_events
                    (timestamp, event_type, project_name, session_id, feedback_text)
                VALUES (?, ?, ?, ?, ?)
                """,
                (timestamp, event_type, project_name, session_id, feedback_text),
            )
            return True

        return execute_with_retry(db_path, operation)

    except Exception as e:
        log_error("database", "Failed to record governance event", e)
        return False


def get_recent_activity(
    db_path: str,
    window_seconds: int = 10,
) -> List[Dict[str, Any]]:
    """
    Get the most recent activity event per event_code within the time window.

    Returns one entry per event_code, selecting the most recent occurrence
    across all sessions. Used by the usage monitor to render the activity line.

    Args:
        db_path: Path to SQLite database file
        window_seconds: How many seconds back to look (default 10)

    Returns:
        List of dicts with 'event_code' and 'status' keys,
        one entry per unique event_code. Returns [] on error.
    """
    try:
        cutoff = time.time() - window_seconds

        def operation(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT event_code, status
                FROM activity_events
                WHERE timestamp > ?
                  AND id IN (
                      SELECT id FROM activity_events ae2
                      WHERE ae2.event_code = activity_events.event_code
                        AND ae2.timestamp > ?
                      ORDER BY ae2.timestamp DESC
                      LIMIT 1
                  )
                GROUP BY event_code
                """,
                (cutoff, cutoff),
            )
            rows = cursor.fetchall()
            return [{"event_code": row[0], "status": row[1]} for row in rows]

        return execute_with_retry(db_path, operation, readonly=True)

    except Exception as e:
        log_warning("database", "Failed to get recent activity", e)
        return []


def cleanup_old_activity(
    db_path: str,
    max_age_seconds: int = 60,
) -> int:
    """
    Delete activity events older than max_age_seconds.

    Called periodically to prevent unbounded table growth.
    Events within the time window are preserved.

    Args:
        db_path: Path to SQLite database file
        max_age_seconds: Delete events older than this (default 60)

    Returns:
        Number of records deleted, or -1 on error
    """
    try:
        cutoff = time.time() - max_age_seconds

        def operation(conn: sqlite3.Connection) -> int:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM activity_events WHERE timestamp < ?",
                (cutoff,),
            )
            return cursor.rowcount

        return execute_with_retry(db_path, operation)

    except Exception as e:
        log_error("database", "Failed to cleanup old activity events", e)
        return -1


def cleanup_old_governance_events(
    db_path: str,
    max_age_seconds: int = 86400,
) -> int:
    """
    Delete governance events older than max_age_seconds.

    Called periodically (e.g., from SessionStart) to prevent unbounded
    table growth. Default retention is 24 hours.

    Args:
        db_path: Path to SQLite database file
        max_age_seconds: Delete events older than this (default 86400 = 24h)

    Returns:
        Number of records deleted, or -1 on error
    """
    try:
        cutoff = time.time() - max_age_seconds

        def operation(conn: sqlite3.Connection) -> int:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM governance_events WHERE timestamp < ?",
                (cutoff,),
            )
            return cursor.rowcount

        return execute_with_retry(db_path, operation)

    except Exception as e:
        log_error("database", "Failed to cleanup old governance events", e)
        return -1


@contextmanager
def get_db_connection(
    db_path: str, readonly: bool = False
) -> Generator[sqlite3.Connection, None, None]:
    """
    Get database connection with proper concurrency handling.

    Uses timeout to avoid indefinite blocking, enables WAL mode for better
    concurrent access, and ensures connection is properly closed even on errors.

    Args:
        db_path: Path to SQLite database file
        readonly: If True, optimize for read-only access

    Yields:
        sqlite3.Connection: Database connection with proper settings
    """
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(db_path, timeout=DB_TIMEOUT)
        conn.execute("PRAGMA journal_mode=WAL")
        # In test mode, skip fsync for 20x faster schema creation
        if os.environ.get("PACEMAKER_TEST_MODE"):
            conn.execute("PRAGMA synchronous=OFF")
        if readonly:
            conn.execute("PRAGMA read_uncommitted=1")
        yield conn
        if not readonly:
            conn.commit()
    finally:
        if conn:
            conn.close()


def execute_with_retry(
    db_path: str,
    operation: Callable[[sqlite3.Connection], T],
    readonly: bool = False,
    max_retries: int = MAX_RETRIES,
) -> T:
    """
    Execute database operation with retry on lock.

    Retries the operation if a database lock error occurs, with exponential
    backoff between attempts.

    Args:
        db_path: Path to SQLite database file
        operation: Function that takes a connection and performs the operation
        readonly: If True, use read-only optimizations
        max_retries: Maximum number of retry attempts

    Returns:
        Result from the operation function

    Raises:
        Exception: Re-raises the last exception if all retries fail
    """
    last_error: Optional[sqlite3.OperationalError] = None
    for attempt in range(max_retries):
        try:
            with get_db_connection(db_path, readonly=readonly) as conn:
                return operation(conn)
        except sqlite3.OperationalError as e:
            last_error = e
            if "locked" in str(e).lower() and attempt < max_retries - 1:
                # Exponential backoff: 0.1s, 0.2s, 0.4s, etc.
                delay = RETRY_DELAY * (2**attempt)
                time.sleep(delay)
                continue
            raise
    if last_error:
        raise last_error
    # This should never be reached, but satisfies mypy
    raise RuntimeError("Retry loop completed without returning or raising")


def initialize_database(db_path: str) -> bool:
    """
    Initialize database with required schema.

    Uses an in-memory cache (_initialized_dbs) to avoid calling
    executescript() more than once per db_path. executescript() requires
    an exclusive lock; repeated calls on the same path cause WAL-mode
    contention that can hang tests indefinitely.

    Args:
        db_path: Path to SQLite database file

    Returns:
        True if successful, False otherwise
    """
    if db_path in _initialized_dbs:
        return True

    try:
        # Ensure parent directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            # Execute schema creation
            cursor.executescript(SCHEMA)

        _initialized_dbs.add(db_path)
        return True

    except Exception as e:
        log_error("database", f"Failed to initialize database: {db_path}", e)
        return False


def insert_usage_snapshot(
    db_path: str,
    timestamp: datetime,
    five_hour_util: float,
    five_hour_resets_at: Optional[datetime],
    seven_day_util: float,
    seven_day_resets_at: Optional[datetime],
    session_id: str,
) -> bool:
    """
    Insert a usage snapshot into the database.

    Args:
        db_path: Path to SQLite database file
        timestamp: When this snapshot was taken
        five_hour_util: 5-hour window utilization (%)
        five_hour_resets_at: When 5-hour window resets (or None if inactive)
        seven_day_util: 7-day window utilization (%)
        seven_day_resets_at: When 7-day window resets (or None if inactive)
        session_id: Unique identifier for this session

    Returns:
        True if successful, False otherwise
    """
    try:

        def operation(conn: sqlite3.Connection) -> bool:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO usage_snapshots (
                    timestamp,
                    five_hour_util,
                    five_hour_resets_at,
                    seven_day_util,
                    seven_day_resets_at,
                    session_id
                ) VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    int(timestamp.timestamp()),
                    five_hour_util,
                    five_hour_resets_at.isoformat() if five_hour_resets_at else None,
                    seven_day_util,
                    seven_day_resets_at.isoformat() if seven_day_resets_at else None,
                    session_id,
                ),
            )
            return True

        return execute_with_retry(db_path, operation)

    except Exception as e:
        log_error("database", "Failed to insert usage snapshot", e)
        return False


def query_recent_snapshots(db_path: str, minutes: int = 60) -> List[Dict[str, Any]]:
    """
    Query usage snapshots from the last N minutes.

    Args:
        db_path: Path to SQLite database file
        minutes: How many minutes back to query (default 60)

    Returns:
        List of snapshot dictionaries, ordered by timestamp DESC
    """
    try:
        cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=minutes)

        def operation(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
            conn.row_factory = sqlite3.Row  # Enable dict-like access
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT
                    id,
                    timestamp,
                    five_hour_util,
                    five_hour_resets_at,
                    seven_day_util,
                    seven_day_resets_at,
                    session_id,
                    created_at
                FROM usage_snapshots
                WHERE timestamp >= ?
                ORDER BY timestamp DESC
            """,
                (int(cutoff_time.timestamp()),),
            )

            rows = cursor.fetchall()
            # Convert to list of dicts
            return [dict(row) for row in rows]

        return execute_with_retry(db_path, operation, readonly=True)

    except Exception as e:
        log_warning("database", "Failed to query recent snapshots", e)
        return []


def cleanup_old_snapshots(db_path: str, retention_days: int = 60) -> int:
    """
    Delete usage snapshots older than retention_days.

    Args:
        db_path: Path to SQLite database file
        retention_days: Keep snapshots from last N days (default 60 = 2 months)

    Returns:
        Number of records deleted, or -1 on error
    """
    try:
        cutoff_time = datetime.now(timezone.utc) - timedelta(days=retention_days)

        def operation(conn: sqlite3.Connection) -> int:
            cursor = conn.cursor()
            # Delete old records
            cursor.execute(
                """
                DELETE FROM usage_snapshots
                WHERE timestamp < ?
            """,
                (int(cutoff_time.timestamp()),),
            )
            return cursor.rowcount

        return execute_with_retry(db_path, operation)

    except Exception as e:
        log_error("database", "Failed to cleanup old snapshots", e)
        return -1


def insert_pacing_decision(
    db_path: str,
    timestamp: datetime,
    should_throttle: bool,
    delay_seconds: int,
    session_id: str,
) -> bool:
    """
    Insert a pacing decision into the database.

    Args:
        db_path: Path to SQLite database file
        timestamp: When this decision was made
        should_throttle: Whether throttling should occur
        delay_seconds: How many seconds to delay (0 if no throttle)
        session_id: Unique identifier for this session

    Returns:
        True if successful, False otherwise
    """
    try:

        def operation(conn: sqlite3.Connection) -> bool:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO pacing_decisions (
                    timestamp,
                    should_throttle,
                    delay_seconds,
                    session_id
                ) VALUES (?, ?, ?, ?)
            """,
                (
                    int(timestamp.timestamp()),
                    1 if should_throttle else 0,
                    delay_seconds,
                    session_id,
                ),
            )
            return True

        return execute_with_retry(db_path, operation)

    except Exception as e:
        log_error("database", "Failed to insert pacing decision", e)
        return False


def get_last_pacing_decision(db_path: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve the most recent pacing decision globally (any session).

    Args:
        db_path: Path to SQLite database file

    Returns:
        Dict with decision details, or None if no decision found
    """
    try:

        def operation(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT
                    timestamp,
                    should_throttle,
                    delay_seconds,
                    session_id
                FROM pacing_decisions
                ORDER BY timestamp DESC
                LIMIT 1
            """
            )

            row = cursor.fetchone()

            if row:
                return {
                    "timestamp": row["timestamp"],
                    "should_throttle": bool(row["should_throttle"]),
                    "delay_seconds": row["delay_seconds"],
                    "session_id": row["session_id"],
                }

            return None

        return execute_with_retry(db_path, operation, readonly=True)

    except Exception as e:
        log_warning("database", "Failed to get last pacing decision", e)
        return None


def should_poll_globally(
    db_path: str, poll_interval: int = 300, session_id: str = ""
) -> bool:
    """Atomically check and claim the global API poll slot.

    Uses SQLite BEGIN IMMEDIATE for mutual exclusion across concurrent
    sessions/processes. Only one session can claim the poll slot per interval.

    Args:
        db_path: Path to SQLite database file
        poll_interval: Minimum seconds between polls (default 300)
        session_id: Identifier of the requesting session

    Returns:
        True if this session should poll the API, False if another session
        polled recently. Returns True on error (fail-open for availability).
    """
    # NOTE: This function does NOT use execute_with_retry() because the entire
    # check-and-claim operation must execute as a single BEGIN IMMEDIATE transaction
    # for cross-process atomicity. Splitting it across retries would break mutual exclusion.
    now = time.time()
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=DB_TIMEOUT)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT last_poll_time FROM global_poll_state WHERE id = 1"
        ).fetchone()

        if row is None:
            # First ever poll: insert initial row
            conn.execute(
                "INSERT INTO global_poll_state "
                "(id, last_poll_time, last_poll_session) VALUES (1, ?, ?)",
                (now, session_id),
            )
            conn.commit()
            return True

        elapsed = now - row[0]
        if elapsed >= poll_interval:
            # Enough time passed: claim this poll slot
            conn.execute(
                "UPDATE global_poll_state "
                "SET last_poll_time = ?, last_poll_session = ? WHERE id = 1",
                (now, session_id),
            )
            conn.commit()
            return True
        else:
            # No writes needed: release lock without committing
            conn.rollback()
            return False
    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        # Fail-open: allow poll on error to avoid blocking pacing entirely
        return True
    finally:
        if conn:
            conn.close()


def record_blockage(
    db_path: str,
    category: str,
    reason: str,
    hook_type: str,
    session_id: str,
    details: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Record a blockage event in the database.

    Args:
        db_path: Path to SQLite database file
        category: Blockage category (validated against BLOCKAGE_CATEGORIES)
        reason: Description of why the blockage occurred
        hook_type: Type of hook that triggered the blockage (e.g., pre_tool_use, stop)
        session_id: Unique identifier for this session
        details: Optional dict with additional context (converted to JSON)

    Returns:
        True if successful, False otherwise
    """
    try:
        # Validate category - use 'other' if invalid
        if category not in BLOCKAGE_CATEGORIES:
            log_warning(
                "database",
                f"Invalid blockage category '{category}' - using 'other'",
                None,
            )
            category = "other"

        # Convert details dict to JSON string if provided
        details_json = json.dumps(details) if details is not None else None

        # Get current timestamp
        timestamp = int(time.time())

        def operation(conn: sqlite3.Connection) -> bool:
            cursor = conn.cursor()
            # Use parameterized query to prevent SQL injection
            cursor.execute(
                """
                INSERT INTO blockage_events (
                    timestamp,
                    category,
                    reason,
                    hook_type,
                    session_id,
                    details
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (timestamp, category, reason, hook_type, session_id, details_json),
            )
            return True

        result: bool = execute_with_retry(db_path, operation)
        return result

    except Exception as e:
        log_error("database", "Failed to record blockage event", e)
        return False


def get_hourly_blockage_stats(db_path: str) -> Dict[str, int]:
    """
    Get blockage counts per category for the last 60 minutes.

    Args:
        db_path: Path to SQLite database file

    Returns:
        Dict mapping each category to its count (zero-filled for missing categories)
    """
    # Initialize result with all categories set to 0
    result = {category: 0 for category in BLOCKAGE_CATEGORIES}

    try:
        # Calculate cutoff timestamp (60 minutes ago)
        cutoff_timestamp = int(time.time()) - 3600

        def operation(conn: sqlite3.Connection) -> Dict[str, int]:
            cursor = conn.cursor()

            # Query counts grouped by category, using index on timestamp
            cursor.execute(
                """
                SELECT category, COUNT(*) as count
                FROM blockage_events
                WHERE timestamp >= ?
                GROUP BY category
                """,
                (cutoff_timestamp,),
            )

            # Update result with actual counts
            local_result = dict(result)
            for row in cursor.fetchall():
                category, count = row
                if category in local_result:
                    local_result[category] = count

            return local_result

        stats: Dict[str, int] = execute_with_retry(db_path, operation, readonly=True)
        return stats

    except Exception as e:
        log_warning("database", "Failed to get hourly blockage stats", e)
        # Return all zeros on error
        return result
