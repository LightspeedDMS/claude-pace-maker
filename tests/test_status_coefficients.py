#!/usr/bin/env python3
"""
Tests for coefficient display in pace-maker status output.

TDD: Tests written first to define behavior before implementation.
Task: Show fallback coefficients in `pace-maker status` output.

Tests verify:
1. Default coefficients shown when no calibration data exists
2. Calibrated coefficients shown when calibration data is available
3. Per-tier calibration override (calibrate only 5x, 20x uses default)
4. Line format fits within 46 chars
5. Status survives UsageModel failure (uses defaults)
"""

import json
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(config_path: Path, enabled: bool = True) -> None:
    """Write a minimal config.json for testing."""
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
    """Write a minimal usage.db with a snapshot."""
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


def _write_calibrated_coefficients(
    db_path: Path,
    tier: str,
    coeff_5h: float,
    coeff_7d: float,
) -> None:
    """Insert calibrated coefficients into usage.db."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calibrated_coefficients (
            tier TEXT PRIMARY KEY,
            coefficient_5h REAL NOT NULL,
            coefficient_7d REAL NOT NULL,
            sample_count INTEGER NOT NULL DEFAULT 1,
            last_updated REAL
        )
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO calibrated_coefficients
        (tier, coefficient_5h, coefficient_7d, sample_count, last_updated)
        VALUES (?, ?, ?, ?, ?)
        """,
        (tier, coeff_5h, coeff_7d, 5, time.time()),
    )
    conn.commit()
    conn.close()


def _run_status(tmp_path: Path) -> dict:
    """Set up filesystem and run _execute_status, return result dict."""
    from pacemaker.user_commands import _execute_status

    pm_dir = tmp_path / ".claude-pace-maker"
    pm_dir.mkdir()
    config_path = pm_dir / "config.json"
    db_path = pm_dir / "usage.db"

    _write_config(config_path)
    _write_usage_db(db_path)

    return _execute_status(str(config_path), str(db_path))


# Default coefficient values (from fallback.py _DEFAULT_TOKEN_COSTS)
DEFAULT_5H_5X = 0.0075
DEFAULT_5H_20X = 0.001875
DEFAULT_7D_5X = 0.0011
DEFAULT_7D_20X = 0.000275


class TestStatusCoefficients:
    """Tests for coefficient display in pace-maker status output."""

    def test_status_shows_default_coefficients(self, tmp_path):
        """When no calibrated values exist, status shows default coefficient values
        for both 5h and 7d lines, both tiers."""
        result = _run_status(tmp_path)
        output = result.get("message", "")

        # 5-Hour Limit line: both 5x and 20x default 5h coefficients
        assert f"{DEFAULT_5H_5X:.4f}" in output, (
            f"Expected default 5h/5x coefficient {DEFAULT_5H_5X:.4f} in output.\n"
            f"Output:\n{output}"
        )
        assert f"{DEFAULT_5H_20X:.4f}" in output, (
            f"Expected default 5h/20x coefficient {DEFAULT_5H_20X:.4f} in output.\n"
            f"Output:\n{output}"
        )
        # Weekly Limit line: both 5x and 20x default 7d coefficients
        assert f"{DEFAULT_7D_5X:.4f}" in output, (
            f"Expected default 7d/5x coefficient {DEFAULT_7D_5X:.4f} in output.\n"
            f"Output:\n{output}"
        )
        assert f"{DEFAULT_7D_20X:.4f}" in output, (
            f"Expected default 7d/20x coefficient {DEFAULT_7D_20X:.4f} in output.\n"
            f"Output:\n{output}"
        )

    def test_status_shows_calibrated_when_available(self, tmp_path):
        """When calibrated values exist, they appear instead of defaults."""
        from pacemaker.user_commands import _execute_status

        pm_dir = tmp_path / ".claude-pace-maker"
        pm_dir.mkdir()
        config_path = pm_dir / "config.json"
        db_path = pm_dir / "usage.db"

        _write_config(config_path)
        _write_usage_db(db_path)

        cal_5h_5x = 0.0090
        cal_7d_5x = 0.0015
        cal_5h_20x = 0.0022
        cal_7d_20x = 0.0004

        _write_calibrated_coefficients(db_path, "5x", cal_5h_5x, cal_7d_5x)
        _write_calibrated_coefficients(db_path, "20x", cal_5h_20x, cal_7d_20x)

        result = _execute_status(str(config_path), str(db_path))
        output = result.get("message", "")

        # Calibrated values must appear
        assert (
            f"{cal_5h_5x:.4f}" in output
        ), f"Expected calibrated 5h/5x {cal_5h_5x:.4f} in output.\nOutput:\n{output}"
        assert (
            f"{cal_5h_20x:.4f}" in output
        ), f"Expected calibrated 5h/20x {cal_5h_20x:.4f} in output.\nOutput:\n{output}"
        assert (
            f"{cal_7d_5x:.4f}" in output
        ), f"Expected calibrated 7d/5x {cal_7d_5x:.4f} in output.\nOutput:\n{output}"
        assert (
            f"{cal_7d_20x:.4f}" in output
        ), f"Expected calibrated 7d/20x {cal_7d_20x:.4f} in output.\nOutput:\n{output}"
        # Default 5x values must NOT appear (overridden by calibrated)
        assert f"{DEFAULT_5H_5X:.4f}" not in output, (
            f"Default 5h/5x {DEFAULT_5H_5X:.4f} should NOT appear when calibrated.\n"
            f"Output:\n{output}"
        )

    def test_status_calibrated_overrides_default_per_tier(self, tmp_path):
        """Calibrate only 5x; 20x should still show default values."""
        from pacemaker.user_commands import _execute_status

        pm_dir = tmp_path / ".claude-pace-maker"
        pm_dir.mkdir()
        config_path = pm_dir / "config.json"
        db_path = pm_dir / "usage.db"

        _write_config(config_path)
        _write_usage_db(db_path)

        cal_5h_5x = 0.0088
        cal_7d_5x = 0.0013
        _write_calibrated_coefficients(db_path, "5x", cal_5h_5x, cal_7d_5x)

        result = _execute_status(str(config_path), str(db_path))
        output = result.get("message", "")

        # Calibrated 5x values appear
        assert (
            f"{cal_5h_5x:.4f}" in output
        ), f"Expected calibrated 5h/5x {cal_5h_5x:.4f} in output.\nOutput:\n{output}"
        assert (
            f"{cal_7d_5x:.4f}" in output
        ), f"Expected calibrated 7d/5x {cal_7d_5x:.4f} in output.\nOutput:\n{output}"
        # Default 20x values still appear (not calibrated)
        assert f"{DEFAULT_5H_20X:.4f}" in output, (
            f"Expected default 5h/20x {DEFAULT_5H_20X:.4f} when 20x not calibrated.\n"
            f"Output:\n{output}"
        )
        assert f"{DEFAULT_7D_20X:.4f}" in output, (
            f"Expected default 7d/20x {DEFAULT_7D_20X:.4f} when 20x not calibrated.\n"
            f"Output:\n{output}"
        )

    def test_status_coefficient_format_fits_width(self, tmp_path):
        """Each limiter line with coefficients is <= 46 chars."""
        result = _run_status(tmp_path)
        output = result.get("message", "")

        lines = output.splitlines()

        five_hour_lines = [ln for ln in lines if "5-Hour Limit:" in ln]
        assert (
            len(five_hour_lines) == 1
        ), f"Expected exactly one '5-Hour Limit:' line, got: {five_hour_lines}"
        fh_line = five_hour_lines[0]
        assert (
            len(fh_line) <= 46
        ), f"5-Hour Limit line is {len(fh_line)} chars (max 46): {fh_line!r}"

        weekly_lines = [ln for ln in lines if "Weekly Limit:" in ln]
        assert (
            len(weekly_lines) == 1
        ), f"Expected exactly one 'Weekly Limit:' line, got: {weekly_lines}"
        wl_line = weekly_lines[0]
        assert (
            len(wl_line) <= 46
        ), f"Weekly Limit line is {len(wl_line)} chars (max 46): {wl_line!r}"

    def test_status_coefficients_survive_usage_model_failure(self, tmp_path):
        """When UsageModel raises an exception, status still works with defaults."""
        from pacemaker.user_commands import _execute_status

        pm_dir = tmp_path / ".claude-pace-maker"
        pm_dir.mkdir()
        config_path = pm_dir / "config.json"
        db_path = pm_dir / "usage.db"

        _write_config(config_path)
        _write_usage_db(db_path)

        with patch(
            "pacemaker.usage_model.UsageModel._get_calibrated_coefficients",
            side_effect=RuntimeError("calibration DB unavailable"),
        ):
            result = _execute_status(str(config_path), str(db_path))

        assert (
            result.get("success") is True
        ), f"Expected success=True even when UsageModel fails. Got: {result}"
        output = result.get("message", "")

        # Should fall back to default coefficients
        assert f"{DEFAULT_5H_5X:.4f}" in output, (
            f"Expected default 5h/5x {DEFAULT_5H_5X:.4f} when UsageModel fails.\n"
            f"Output:\n{output}"
        )
        assert f"{DEFAULT_7D_5X:.4f}" in output, (
            f"Expected default 7d/5x {DEFAULT_7D_5X:.4f} when UsageModel fails.\n"
            f"Output:\n{output}"
        )
