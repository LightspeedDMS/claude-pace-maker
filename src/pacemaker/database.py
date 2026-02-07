#!/usr/bin/env python3
"""
Database operations for Credit-Aware Adaptive Throttling.

Manages SQLite database for storing usage snapshots and calculating
consumption rates over time.
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
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
"""


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

    Args:
        db_path: Path to SQLite database file

    Returns:
        True if successful, False otherwise
    """
    try:
        # Ensure parent directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            # Execute schema creation
            cursor.executescript(SCHEMA)

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
        cutoff_time = datetime.utcnow() - timedelta(minutes=minutes)

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
        cutoff_time = datetime.utcnow() - timedelta(days=retention_days)

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


def get_last_pacing_decision(db_path: str, session_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve the most recent pacing decision for a session.

    Args:
        db_path: Path to SQLite database file
        session_id: Unique identifier for this session

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
                WHERE session_id = ?
                ORDER BY timestamp DESC
                LIMIT 1
            """,
                (session_id,),
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
