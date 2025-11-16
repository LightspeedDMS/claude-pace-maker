#!/usr/bin/env python3
"""
Shared constants for Pace Maker.

Centralizes default configuration values to eliminate duplication
and ensure consistency across modules.
"""

from pathlib import Path
from typing import Dict, Any

# Default configuration values
DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "base_delay": 5,
    "max_delay": 350,
    "threshold_percent": 0,
    "poll_interval": 60,
    "safety_buffer_pct": 95.0,
    "preload_hours": 12.0,
    "api_timeout_seconds": 10,
    "cleanup_interval_hours": 24,
    "retention_days": 60,
    "weekly_limit_enabled": True,
    "tempo_enabled": True,
}

# Default file paths
DEFAULT_DB_PATH = str(Path.home() / ".claude-pace-maker" / "usage.db")
DEFAULT_CONFIG_PATH = str(Path.home() / ".claude-pace-maker" / "config.json")
DEFAULT_STATE_PATH = str(Path.home() / ".claude-pace-maker" / "state.json")

# Throttling thresholds
PROMPT_INJECTION_THRESHOLD_SECONDS = 30
MAX_DELAY_SECONDS = 350  # 360s timeout - 10s safety margin
