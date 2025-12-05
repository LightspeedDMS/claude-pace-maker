#!/usr/bin/env python3
"""
Database operations for Credit-Aware Adaptive Throttling.

Manages SQLite database for storing usage snapshots and calculating
consumption rates over time.
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from pathlib import Path

from .logger import log_error, log_warning


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
"""


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

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Execute schema creation
        cursor.executescript(SCHEMA)

        conn.commit()
        conn.close()

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
        conn = sqlite3.connect(db_path)
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

        conn.commit()
        conn.close()

        return True

    except Exception as e:
        log_error("database", "Failed to insert usage snapshot", e)
        return False


def query_recent_snapshots(db_path: str, minutes: int = 60) -> List[Dict]:
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

        conn = sqlite3.connect(db_path)
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
        conn.close()

        # Convert to list of dicts
        snapshots = [dict(row) for row in rows]

        return snapshots

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

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Delete old records
        cursor.execute(
            """
            DELETE FROM usage_snapshots
            WHERE timestamp < ?
        """,
            (int(cutoff_time.timestamp()),),
        )

        deleted_count = cursor.rowcount

        conn.commit()
        conn.close()

        return deleted_count

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
        conn = sqlite3.connect(db_path)
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

        conn.commit()
        conn.close()

        return True

    except Exception as e:
        log_error("database", "Failed to insert pacing decision", e)
        return False


def get_last_pacing_decision(db_path: str, session_id: str) -> Optional[Dict]:
    """
    Retrieve the most recent pacing decision for a session.

    Args:
        db_path: Path to SQLite database file
        session_id: Unique identifier for this session

    Returns:
        Dict with decision details, or None if no decision found
    """
    try:
        conn = sqlite3.connect(db_path)
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
        conn.close()

        if row:
            return {
                "timestamp": row["timestamp"],
                "should_throttle": bool(row["should_throttle"]),
                "delay_seconds": row["delay_seconds"],
                "session_id": row["session_id"],
            }

        return None

    except Exception as e:
        log_warning("database", "Failed to get last pacing decision", e)
        return None
