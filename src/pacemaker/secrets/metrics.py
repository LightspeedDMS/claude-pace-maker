#!/usr/bin/env python3
"""
Secrets metrics tracking module.

Provides functionality to track secrets masking metrics in 15-minute buckets:
- Secrets masked count (number of times secrets were masked)

Implements:
- Bucket alignment to 15-minute boundaries
- Counter increment with upsert
- Cleanup of stale buckets (older than 24 hours)
- 24-hour metrics query

Follows the same pattern as langfuse/metrics.py for consistency.

Story #35: Secrets Management - Metrics Tracking
"""

import sqlite3
import time
from typing import Dict

from pacemaker.database import execute_with_retry


def align_to_bucket(timestamp: float) -> int:
    """
    Align timestamp to 15-minute bucket boundary.

    15 minutes = 900 seconds. This rounds down to the nearest 900-second boundary.

    Args:
        timestamp: Unix timestamp (seconds since epoch)

    Returns:
        Aligned timestamp (int)

    Example:
        >>> align_to_bucket(1738670850)  # 12:07:30
        1738670400  # 12:00:00
        >>> align_to_bucket(1738671299)  # 12:14:59
        1738670400  # 12:00:00
        >>> align_to_bucket(1738671300)  # 12:15:00
        1738671300  # 12:15:00
    """
    return int(timestamp // 900) * 900


def increment_secrets_masked(db_path: str, count: int = 1) -> None:
    """
    Increment secrets masked counter for current 15-minute bucket.

    Creates new bucket if it doesn't exist, or increments existing bucket.
    Automatically calls cleanup_stale_buckets() after increment.

    Args:
        db_path: Path to SQLite database
        count: Number to increment by (default 1)

    Raises:
        sqlite3.Error: If database operation fails
    """
    bucket = align_to_bucket(time.time())

    def operation(conn: sqlite3.Connection) -> None:
        # Upsert: insert new bucket or increment existing
        conn.execute(
            """
            INSERT INTO secrets_metrics (bucket_timestamp, secrets_masked_count)
            VALUES (?, ?)
            ON CONFLICT(bucket_timestamp) DO UPDATE SET
                secrets_masked_count = secrets_masked_count + ?
            """,
            (bucket, count, count),
        )

    # Use execute_with_retry for proper concurrency handling
    execute_with_retry(db_path, operation)

    # Cleanup stale buckets after every increment
    cleanup_stale_buckets(db_path)


def cleanup_stale_buckets(db_path: str) -> None:
    """
    Delete metric buckets older than 24 hours.

    This keeps the database size manageable by removing old data.

    Args:
        db_path: Path to SQLite database
    """
    cutoff = time.time() - 86400  # 24 hours in seconds

    def operation(conn: sqlite3.Connection) -> None:
        conn.execute(
            "DELETE FROM secrets_metrics WHERE bucket_timestamp < ?",
            (cutoff,),
        )

    # Use execute_with_retry for proper concurrency handling
    execute_with_retry(db_path, operation)


def get_24h_secrets_metrics(db_path: str) -> Dict[str, int]:
    """
    Get secrets metrics for the last 24 hours.

    Sums all buckets within the 24-hour window and returns total.

    Args:
        db_path: Path to SQLite database

    Returns:
        Dictionary with key:
        - secrets_masked: Total number of secrets masked in last 24h

    Example:
        >>> get_24h_secrets_metrics("/path/to/db")
        {'secrets_masked': 150}
    """
    cutoff = time.time() - 86400  # 24 hours

    def operation(conn: sqlite3.Connection) -> int:
        result = conn.execute(
            """
            SELECT COALESCE(SUM(secrets_masked_count), 0)
            FROM secrets_metrics
            WHERE bucket_timestamp >= ?
            """,
            (cutoff,),
        ).fetchone()
        if result is None:
            return 0
        # Convert to int explicitly to satisfy type checker
        return int(result[0])

    # Use execute_with_retry for proper concurrency handling (readonly=True)
    secrets_masked = execute_with_retry(db_path, operation, readonly=True)

    return {"secrets_masked": secrets_masked}
