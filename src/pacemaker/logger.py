#!/usr/bin/env python3
"""
Centralized logging for Pace Maker with daily log rotation.

Log levels:
  0 = OFF      - No logging
  1 = ERROR    - Errors only
  2 = WARNING  - Warnings + Errors (default)
  3 = INFO     - Info + Warnings + Errors
  4 = DEBUG    - All messages

Log rotation:
  - One file per day: pace-maker-YYYY-MM-DD.log
  - Keeps 15 days of logs
  - Automatic cleanup of old files
"""

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List
import glob

from .constants import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_LOG_DIR,
    LOG_FILE_PREFIX,
    LOG_FILE_SUFFIX,
    LOG_RETENTION_DAYS,
    LOG_LEVEL_OFF,
    LOG_LEVEL_ERROR,
    LOG_LEVEL_WARNING,
    LOG_LEVEL_INFO,
    LOG_LEVEL_DEBUG,
)


def _get_log_level() -> int:
    """Get current log level from config."""
    import json

    try:
        if os.path.exists(DEFAULT_CONFIG_PATH):
            with open(DEFAULT_CONFIG_PATH) as f:
                config = json.load(f)
            return config.get("log_level", LOG_LEVEL_WARNING)
    except Exception:
        pass
    return LOG_LEVEL_WARNING


def _ensure_log_dir(log_dir: Optional[str] = None):
    """Ensure log directory exists.

    Args:
        log_dir: Directory path (default: DEFAULT_LOG_DIR)
    """
    if log_dir is None:
        log_dir = DEFAULT_LOG_DIR
    Path(log_dir).mkdir(parents=True, exist_ok=True)


def get_log_path_for_date(date: datetime = None, log_dir: Optional[str] = None) -> str:
    """Get log file path for a specific date.

    Args:
        date: Date for log file (default: today)
        log_dir: Directory path (default: DEFAULT_LOG_DIR)

    Returns:
        Path like ~/.claude-pace-maker/pace-maker-2026-02-06.log
    """
    if date is None:
        date = datetime.now()
    if log_dir is None:
        log_dir = DEFAULT_LOG_DIR
    date_str = date.strftime("%Y-%m-%d")
    return str(Path(log_dir) / f"{LOG_FILE_PREFIX}{date_str}{LOG_FILE_SUFFIX}")


def get_current_log_path() -> str:
    """Get today's log file path."""
    return get_log_path_for_date(datetime.now())


def get_recent_log_paths(days: int = 2, log_dir: Optional[str] = None) -> List[str]:
    """Get log file paths for the last N days.

    Args:
        days: Number of days to include (default: 2 for 24-hour coverage)
        log_dir: Directory path (default: DEFAULT_LOG_DIR)

    Returns:
        List of existing log file paths, most recent first
    """
    paths = []
    for i in range(days):
        date = datetime.now() - timedelta(days=i)
        path = get_log_path_for_date(date, log_dir=log_dir)
        if os.path.exists(path):
            paths.append(path)
    return paths


def cleanup_old_logs(log_dir: Optional[str] = None):
    """Remove log files older than LOG_RETENTION_DAYS.

    Args:
        log_dir: Directory path (default: DEFAULT_LOG_DIR)
    """
    try:
        if log_dir is None:
            log_dir = DEFAULT_LOG_DIR
        _ensure_log_dir(log_dir)
        pattern = str(Path(log_dir) / f"{LOG_FILE_PREFIX}*{LOG_FILE_SUFFIX}")
        cutoff_date = datetime.now() - timedelta(days=LOG_RETENTION_DAYS)

        for log_file in glob.glob(pattern):
            filename = os.path.basename(log_file)
            # Extract date from filename: pace-maker-YYYY-MM-DD.log
            try:
                date_str = filename[len(LOG_FILE_PREFIX) : -len(LOG_FILE_SUFFIX)]
                file_date = datetime.strptime(date_str, "%Y-%m-%d")
                if file_date < cutoff_date:
                    os.remove(log_file)
            except (ValueError, OSError):
                continue  # Skip files that don't match expected format
    except Exception:
        pass  # Cleanup should never crash the application


def log(level: int, component: str, message: str, exc: Optional[Exception] = None):
    """
    Write log entry to today's log file.

    Args:
        level: Log level (1=ERROR, 2=WARNING, 3=INFO, 4=DEBUG)
        component: Component name (e.g., "hook", "api_client")
        message: Log message
        exc: Optional exception to include
    """
    current_level = _get_log_level()

    if current_level == LOG_LEVEL_OFF or level > current_level:
        return

    level_names = {
        LOG_LEVEL_ERROR: "ERROR",
        LOG_LEVEL_WARNING: "WARNING",
        LOG_LEVEL_INFO: "INFO",
        LOG_LEVEL_DEBUG: "DEBUG",
    }
    level_str = level_names.get(level, "UNKNOWN")

    try:
        _ensure_log_dir()

        # Periodically cleanup old logs (on first log of the day)
        log_path = get_current_log_path()
        if not os.path.exists(log_path):
            cleanup_old_logs()

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] [{level_str}] [{component}] {message}"

        if exc:
            log_line += f" | Exception: {type(exc).__name__}: {exc}"

        with open(log_path, "a") as f:
            f.write(log_line + "\n")

    except Exception:
        pass  # Logging should never crash the application


def log_error(component: str, message: str, exc: Optional[Exception] = None):
    """Log error message."""
    log(LOG_LEVEL_ERROR, component, message, exc)


def log_warning(component: str, message: str, exc: Optional[Exception] = None):
    """Log warning message."""
    log(LOG_LEVEL_WARNING, component, message, exc)


def log_info(component: str, message: str):
    """Log info message."""
    log(LOG_LEVEL_INFO, component, message)


def log_debug(component: str, message: str):
    """Log debug message."""
    log(LOG_LEVEL_DEBUG, component, message)
