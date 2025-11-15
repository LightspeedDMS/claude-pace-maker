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
        print(f"Error initializing database: {e}")
        return False


def insert_usage_snapshot(
    db_path: str,
    timestamp: datetime,
    five_hour_util: float,
    five_hour_resets_at: Optional[datetime],
    seven_day_util: float,
    seven_day_resets_at: Optional[datetime],
    session_id: str
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

        cursor.execute("""
            INSERT INTO usage_snapshots (
                timestamp,
                five_hour_util,
                five_hour_resets_at,
                seven_day_util,
                seven_day_resets_at,
                session_id
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            int(timestamp.timestamp()),
            five_hour_util,
            five_hour_resets_at.isoformat() if five_hour_resets_at else None,
            seven_day_util,
            seven_day_resets_at.isoformat() if seven_day_resets_at else None,
            session_id
        ))

        conn.commit()
        conn.close()

        return True

    except Exception as e:
        print(f"Error inserting usage snapshot: {e}")
        return False


def query_recent_snapshots(
    db_path: str,
    minutes: int = 60
) -> List[Dict]:
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

        cursor.execute("""
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
        """, (cutoff_time.isoformat(),))

        rows = cursor.fetchall()
        conn.close()

        # Convert to list of dicts
        snapshots = [dict(row) for row in rows]

        return snapshots

    except Exception as e:
        print(f"Error querying recent snapshots: {e}")
        return []


def cleanup_old_snapshots(
    db_path: str,
    retention_days: int = 60
) -> int:
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
        cursor.execute("""
            DELETE FROM usage_snapshots
            WHERE timestamp < ?
        """, (cutoff_time.isoformat(),))

        deleted_count = cursor.rowcount

        conn.commit()
        conn.close()

        return deleted_count

    except Exception as e:
        print(f"Error cleaning up old snapshots: {e}")
        return -1
