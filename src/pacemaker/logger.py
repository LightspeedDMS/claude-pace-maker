#!/usr/bin/env python3
"""
Centralized logging for Pace Maker.

Log levels:
  0 = OFF      - No logging
  1 = ERROR    - Errors only
  2 = WARNING  - Warnings + Errors (default)
  3 = INFO     - Info + Warnings + Errors
  4 = DEBUG    - All messages
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from .constants import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_LOG_PATH,
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


def _ensure_log_dir():
    """Ensure log directory exists."""
    Path(DEFAULT_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)


def log(level: int, component: str, message: str, exc: Optional[Exception] = None):
    """
    Write log entry to pace-maker.log file.

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

        timestamp = datetime.now().isoformat(sep=" ", timespec="seconds")
        log_line = f"[{timestamp}] [{level_str}] [{component}] {message}"

        if exc:
            log_line += f" | Exception: {type(exc).__name__}: {exc}"

        with open(DEFAULT_LOG_PATH, "a") as f:
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
