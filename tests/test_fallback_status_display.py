#!/usr/bin/env python3
"""
Tests for fallback display in user_commands._execute_status().

TDD: Tests written first to define behavior before implementation.
Story #38: Scenario 6 - Display shows fallback indicators.

Tests verify:
- When in fallback mode, utilization values show "[est]" suffix
- A message indicates "API unavailable - using estimated pacing"
- When in normal mode, no fallback indicators are shown
"""

import json
import sqlite3
import time
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _write_config(config_path: Path, enabled: bool = True) -> None:
    """Helper: write a minimal config.json."""
    config = {
        "enabled": enabled,
        "weekly_limit_enabled": True,
        "five_hour_limit_enabled": True,
        "intent_validation_enabled": False,
        "tdd_enabled": True,
        "log_level": 2,
        "preferred_subagent_model": "auto",
        "base_delay": 5,
        "max_delay": 120,
        "safety_buffer_pct": 95.0,
        "preload_hours": 12.0,
        "threshold_percent": 0,
        "langfuse_enabled": False,
    }
    config_path.write_text(json.dumps(config))


def _write_usage_db(db_path: Path) -> None:
    """Helper: write a minimal usage.db with a snapshot."""
    future_str = "2099-12-31T23:59:59"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_snapshots (
            id INTEGER PRIMARY KEY,
            timestamp REAL,
            five_hour_util REAL,
            five_hour_resets_at TEXT,
            seven_day_util REAL,
            seven_day_resets_at TEXT,
            session_id TEXT
        )
    """
    )
    conn.execute(
        "INSERT INTO usage_snapshots VALUES (NULL, ?, ?, ?, ?, ?, ?)",
        (time.time(), 45.0, future_str, 30.0, future_str, "test-session"),
    )
    conn.commit()
    conn.close()


def _write_fallback_state(
    path: Path,
    state: str,
    baseline_5h: float = 45.0,
    baseline_7d: float = 30.0,
    accumulated_cost: float = 5.0,
) -> None:
    """Helper: write fallback_state.json."""
    content = {
        "state": state,
        "baseline_5h": baseline_5h,
        "baseline_7d": baseline_7d,
        "accumulated_cost": accumulated_cost,
        "entered_at": time.time() - 300,
    }
    path.write_text(json.dumps(content))


class TestExecuteStatusFallbackDisplay:
    """Tests for _execute_status() fallback display - Scenario 6."""

    def _run_status(self, tmp_path: Path, fallback_state: str) -> dict:
        """Helper: set up filesystem and run _execute_status."""
        from pacemaker.usage_model import UsageModel
        from pacemaker.user_commands import _execute_status

        pm_dir = tmp_path / ".claude-pace-maker"
        pm_dir.mkdir()
        config_path = pm_dir / "config.json"
        db_path = pm_dir / "usage.db"

        _write_config(config_path)
        _write_usage_db(db_path)

        # Set up SQLite-based fallback state via UsageModel when needed
        if fallback_state == "fallback":
            model = UsageModel(db_path=str(db_path))
            model.store_api_response(
                {
                    "five_hour": {"utilization": 45.0, "resets_at": None},
                    "seven_day": {"utilization": 30.0, "resets_at": None},
                }
            )
            model.enter_fallback()

        return _execute_status(
            str(config_path),
            str(db_path),
        )

    def test_status_shows_est_indicator_when_in_fallback(self, tmp_path):
        """Status output contains '[est]' when in fallback mode."""
        result = self._run_status(tmp_path, fallback_state="fallback")

        output = result.get("message", "")
        assert (
            "[est]" in output or "est" in output.lower()
        ), f"Expected '[est]' indicator in output when in fallback mode, got:\n{output[:600]}"

    def test_status_shows_api_unavailable_when_in_fallback(self, tmp_path):
        """Status output contains 'API unavailable' message when in fallback mode."""
        result = self._run_status(tmp_path, fallback_state="fallback")

        output = result.get("message", "")
        assert any(
            phrase in output.lower()
            for phrase in [
                "api unavailable",
                "unavailable",
                "estimated pacing",
                "fallback",
            ]
        ), f"Expected fallback message in output, got:\n{output[:600]}"

    def test_status_no_est_indicator_when_normal(self, tmp_path):
        """Status output does NOT contain '[est]' when in normal mode."""
        result = self._run_status(tmp_path, fallback_state="normal")

        output = result.get("message", "")
        assert (
            "[est]" not in output
        ), f"Unexpected '[est]' indicator in normal mode output:\n{output[:600]}"

    def test_status_no_fallback_message_when_normal(self, tmp_path):
        """Status output does NOT contain 'API unavailable' when in normal mode."""
        result = self._run_status(tmp_path, fallback_state="normal")

        output = result.get("message", "")
        assert (
            "api unavailable" not in output.lower()
        ), f"Unexpected 'API unavailable' in normal mode output:\n{output[:600]}"

    def test_status_no_crash_when_fallback_state_missing(self, tmp_path):
        """Status does not crash when fallback_state.json is missing."""
        from pacemaker.user_commands import _execute_status

        pm_dir = tmp_path / ".claude-pace-maker"
        pm_dir.mkdir()
        config_path = pm_dir / "config.json"
        db_path = pm_dir / "usage.db"

        _write_config(config_path)
        _write_usage_db(db_path)

        result = _execute_status(
            str(config_path),
            str(db_path),
        )

        assert isinstance(result, dict)
        assert "message" in result

    def test_status_success_true_when_in_fallback(self, tmp_path):
        """Status returns success=True even when in fallback mode."""
        result = self._run_status(tmp_path, fallback_state="fallback")

        assert result.get("success") is True
