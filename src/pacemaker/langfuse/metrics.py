#!/usr/bin/env python3
"""
Langfuse metrics tracking module.

Provides functionality to track Langfuse usage metrics in 15-minute buckets:
- Sessions created
- Traces created
- Spans created

Implements:
- Bucket alignment to 15-minute boundaries
- Counter increment with upsert
- Cleanup of stale buckets (older than 24 hours)
- 24-hour metrics query
- Langfuse enabled check

Story #34: Langfuse Integration Status and Metrics Display
"""

import sqlite3
import time
from typing import Dict, Any


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


def increment_metric(metric_type: str, db_path: str) -> None:
    """
    Increment metric counter for current 15-minute bucket.

    Creates new bucket if it doesn't exist, or increments existing bucket.
    Automatically calls cleanup_stale_buckets() after increment.

    Args:
        metric_type: Type of metric ('sessions', 'traces', or 'spans')
        db_path: Path to SQLite database

    Raises:
        ValueError: If metric_type is not valid
        sqlite3.Error: If database operation fails
    """
    valid_metrics = ("sessions", "traces", "spans")
    if metric_type not in valid_metrics:
        raise ValueError(
            f"Invalid metric_type: {metric_type}. Must be one of {valid_metrics}"
        )

    bucket = align_to_bucket(time.time())
    column = f"{metric_type}_count"

    conn = sqlite3.connect(db_path)
    try:
        # Upsert: insert new bucket or increment existing
        conn.execute(
            f"""
            INSERT INTO langfuse_metrics (bucket_timestamp, {column})
            VALUES (?, 1)
            ON CONFLICT(bucket_timestamp) DO UPDATE SET {column} = {column} + 1
            """,
            (bucket,),
        )
        conn.commit()
    finally:
        conn.close()

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

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "DELETE FROM langfuse_metrics WHERE bucket_timestamp < ?",
            (cutoff,),
        )
        conn.commit()
    finally:
        conn.close()


def get_24h_metrics(db_path: str) -> Dict[str, int]:
    """
    Get Langfuse metrics for the last 24 hours.

    Sums all buckets within the 24-hour window and returns totals.

    Args:
        db_path: Path to SQLite database

    Returns:
        Dictionary with keys:
        - sessions: Total sessions created in last 24h
        - traces: Total traces created in last 24h
        - spans: Total spans created in last 24h
        - total: Sum of all three metrics

    Example:
        >>> get_24h_metrics("/path/to/db")
        {'sessions': 10, 'traces': 50, 'spans': 200, 'total': 260}
    """
    cutoff = time.time() - 86400  # 24 hours

    conn = sqlite3.connect(db_path)
    try:
        result = conn.execute(
            """
            SELECT COALESCE(SUM(sessions_count), 0),
                   COALESCE(SUM(traces_count), 0),
                   COALESCE(SUM(spans_count), 0)
            FROM langfuse_metrics
            WHERE bucket_timestamp >= ?
            """,
            (cutoff,),
        ).fetchone()
    finally:
        conn.close()

    sessions = int(result[0])
    traces = int(result[1])
    spans = int(result[2])
    total = sessions + traces + spans

    return {
        "sessions": sessions,
        "traces": traces,
        "spans": spans,
        "total": total,
    }


def is_langfuse_enabled(config: Dict[str, Any]) -> bool:
    """
    Check if Langfuse integration is enabled and properly configured.

    Langfuse is considered enabled if ALL conditions are met:
    1. langfuse_enabled flag is True
    2. langfuse_public_key is present and non-empty
    3. langfuse_secret_key is present and non-empty

    Args:
        config: Configuration dictionary

    Returns:
        True if Langfuse is enabled and configured, False otherwise

    Example:
        >>> config = {
        ...     "langfuse_enabled": True,
        ...     "langfuse_public_key": "pk-lf-123",
        ...     "langfuse_secret_key": "sk-lf-456"
        ... }
        >>> is_langfuse_enabled(config)
        True
    """
    # Check enabled flag
    if not config.get("langfuse_enabled", False):
        return False

    # Check public key
    public_key = config.get("langfuse_public_key")
    if not public_key or (isinstance(public_key, str) and not public_key.strip()):
        return False

    # Check secret key
    secret_key = config.get("langfuse_secret_key")
    if not secret_key or (isinstance(secret_key, str) and not secret_key.strip()):
        return False

    return True
