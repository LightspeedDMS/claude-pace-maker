"""
Codex GPT-5 usage tracking.

Reads rate-limit data from Codex session JSONL files and persists
it to the pace-maker SQLite database for display in the usage monitor.

Functions:
    get_latest_codex_usage(sessions_dir=None) -> dict | None
    write_codex_usage(db_path, usage_data) -> None
    read_codex_usage(db_path) -> dict | None
"""

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional, Dict, Any

from .database import execute_with_retry
from .logger import log_debug, log_warning


# Singleton row id: codex_usage table holds exactly one record at all times.
SINGLETON_ID = 1

# Required keys that must be present in usage_data passed to write_codex_usage.
_REQUIRED_KEYS = ("primary_used_pct", "secondary_used_pct", "timestamp")


def get_latest_codex_usage(
    sessions_dir: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Find the most recently modified Codex session file and extract rate limits.

    Searches today's date directory (YYYY/MM/DD) under sessions_dir first.
    If today's directory does not exist or contains no JSONL files, walks all
    available date directories in reverse-sorted order to find the most recent
    one with JSONL files. Within that directory, picks the single most recently
    modified JSONL file and returns the last token_count event with rate_limits.

    Args:
        sessions_dir: Path to Codex sessions root (default: ~/.codex/sessions).

    Returns:
        Dict with keys: primary_used_pct, secondary_used_pct,
        primary_resets_at, secondary_resets_at, plan_type, timestamp.
        Returns None if no suitable event is found or sessions_dir missing.
    """
    if sessions_dir is None:
        sessions_dir = os.path.expanduser("~/.codex/sessions")

    root = Path(sessions_dir)
    if not root.exists():
        return None

    date_dir = _find_most_recent_date_dir(root)
    if date_dir is None:
        return None

    jsonl_file = _most_recently_modified_jsonl(date_dir)
    return _parse_last_token_count(jsonl_file)


def _find_most_recent_date_dir(root: Path) -> Optional[Path]:
    """
    Find the most recent YYYY/MM/DD directory under root that has JSONL files.

    Tries today first, then walks all year/month/day dirs in reverse-sorted
    order.

    Args:
        root: The sessions root directory.

    Returns:
        Path to the most recent date directory containing at least one JSONL
        file, or None if none found.
    """
    today = time.strftime("%Y/%m/%d")
    today_dir = root / today
    if today_dir.exists() and list(today_dir.glob("*.jsonl")):
        return today_dir

    date_dirs = sorted(root.glob("*/*/*"), reverse=True)
    for candidate in date_dirs:
        if candidate.is_dir() and list(candidate.glob("*.jsonl")):
            return candidate

    return None


def _most_recently_modified_jsonl(date_dir: Path) -> Path:
    """
    Return the most recently modified .jsonl file in date_dir.

    Precondition: date_dir contains at least one .jsonl file (guaranteed by
    _find_most_recent_date_dir which only returns dirs with JSONL files).

    Args:
        date_dir: Directory containing one or more .jsonl files.

    Returns:
        Path to the most recently modified .jsonl file.
    """
    jsonl_files = list(date_dir.glob("*.jsonl"))
    assert jsonl_files, f"_most_recently_modified_jsonl called on empty dir: {date_dir}"
    return max(jsonl_files, key=lambda p: p.stat().st_mtime)


def _parse_last_token_count(jsonl_file: Path) -> Optional[Dict[str, Any]]:
    """
    Parse a JSONL file and return the last token_count event with rate_limits.

    Reads all lines, skips malformed JSON and events without rate_limits,
    and returns the last matching event as a usage dict.

    Args:
        jsonl_file: Path to the .jsonl session file.

    Returns:
        Usage dict or None if no suitable event found.
    """
    last_rate_limits = None
    try:
        with open(jsonl_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                payload = event.get("payload", {})
                if payload.get("type") != "token_count":
                    continue

                rate_limits = payload.get("rate_limits")
                if not rate_limits:
                    continue

                last_rate_limits = rate_limits
    except OSError:
        return None

    if last_rate_limits is None:
        return None

    primary = last_rate_limits.get("primary") or {}
    secondary = last_rate_limits.get("secondary") or {}

    return {
        "primary_used_pct": float(primary.get("used_percent", 0.0)),
        "secondary_used_pct": float(secondary.get("used_percent", 0.0)),
        "primary_resets_at": primary.get("resets_at"),
        "secondary_resets_at": secondary.get("resets_at"),
        "plan_type": last_rate_limits.get("plan_type"),
        "limit_id": last_rate_limits.get("limit_id"),
        "timestamp": time.time(),
    }


def migrate_codex_usage_schema(db_path: str) -> None:
    """Add limit_id column to codex_usage table if it does not already exist.

    Safe to call multiple times — OperationalError from duplicate column is
    caught and ignored. Creates the DB file and table if missing.

    Args:
        db_path: Path to the SQLite database.
    """

    def operation(conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ALTER TABLE codex_usage ADD COLUMN limit_id TEXT")
        except sqlite3.OperationalError:
            # Column already exists — idempotent, no action needed.
            pass

    try:
        execute_with_retry(db_path, operation)
    except Exception as e:
        log_warning("codex_usage", "Failed to migrate codex_usage schema", e)


def write_codex_usage(db_path: str, usage_data: Dict[str, Any]) -> None:
    """
    Write Codex usage data to the codex_usage table (single deterministic record).

    Uses INSERT OR REPLACE to maintain exactly one row (id=SINGLETON_ID).

    Args:
        db_path: Path to the SQLite database.
        usage_data: Dict with required keys: primary_used_pct, secondary_used_pct,
                    timestamp. Optional: primary_resets_at, secondary_resets_at,
                    plan_type.

    Raises:
        ValueError: If any required key is missing from usage_data.
    """
    missing = [k for k in _REQUIRED_KEYS if k not in usage_data]
    if missing:
        raise ValueError(f"write_codex_usage: missing required keys: {missing}")

    def operation(conn: sqlite3.Connection) -> None:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO codex_usage
                (id, primary_used_pct, secondary_used_pct,
                 primary_resets_at, secondary_resets_at, plan_type, limit_id,
                 timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                SINGLETON_ID,
                usage_data["primary_used_pct"],
                usage_data["secondary_used_pct"],
                usage_data.get("primary_resets_at"),
                usage_data.get("secondary_resets_at"),
                usage_data.get("plan_type"),
                usage_data.get("limit_id"),
                usage_data["timestamp"],
            ),
        )

    try:
        execute_with_retry(db_path, operation)
        log_debug(
            "codex_usage",
            f"Wrote codex usage: primary={usage_data['primary_used_pct']}%"
            f", secondary={usage_data['secondary_used_pct']}%",
        )
    except Exception as e:
        log_warning("codex_usage", "Failed to write codex usage to DB", e)


def read_codex_usage(db_path: str) -> Optional[Dict[str, Any]]:
    """
    Read the single Codex usage record from the database.

    Args:
        db_path: Path to the SQLite database.

    Returns:
        Dict with codex usage fields, or None if no record exists or on error.
    """

    def operation(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM codex_usage WHERE id = ?", (SINGLETON_ID,))
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "primary_used_pct": row["primary_used_pct"],
            "secondary_used_pct": row["secondary_used_pct"],
            "primary_resets_at": row["primary_resets_at"],
            "secondary_resets_at": row["secondary_resets_at"],
            "plan_type": row["plan_type"],
            "limit_id": row["limit_id"] if "limit_id" in row.keys() else None,
            "timestamp": row["timestamp"],
        }

    try:
        return execute_with_retry(db_path, operation, readonly=True)
    except Exception as e:
        log_warning("codex_usage", "Failed to read codex usage from DB", e)
        return None
