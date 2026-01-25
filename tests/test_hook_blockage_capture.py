#!/usr/bin/env python3
"""
Tests for hook blockage capture integration.

Story #21: Blockage Telemetry Capture and Storage

Tests organized by acceptance criteria:
- AC4: Intent validation blockage capture in hooks
- AC5: Pacing blockage capture in hooks
"""

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add src to path for imports
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ==============================================================================
# AC4: Intent Validation Blockage Capture Tests
# ==============================================================================


class TestIntentValidationBlockageCapture:
    """AC4: Intent validation failures must be captured in the database."""

    def setup_method(self):
        """Create temporary database and config for each test."""
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

        self.temp_config = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w"
        )
        self.config_path = self.temp_config.name
        json.dump(
            {
                "enabled": True,
                "intent_validation_enabled": True,
            },
            self.temp_config,
        )
        self.temp_config.close()

        # Initialize database
        from pacemaker import database

        database.initialize_database(self.db_path)

    def teardown_method(self):
        """Clean up temporary files."""
        Path(self.db_path).unlink(missing_ok=True)
        Path(self.config_path).unlink(missing_ok=True)

    def test_intent_validation_failure_records_blockage(self):
        """Intent validation failure should record a blockage event."""
        from pacemaker.hook import run_pre_tool_hook

        # Create a mock transcript
        transcript_file = tempfile.NamedTemporaryFile(
            suffix=".jsonl", delete=False, mode="w"
        )
        transcript_path = transcript_file.name
        # Write a message without INTENT: marker
        transcript_file.write(
            json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "I will write some code."}
                        ],
                    }
                }
            )
            + "\n"
        )
        transcript_file.close()

        try:
            # Mock stdin with Write tool data
            hook_data = {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/src/example.py",
                    "content": "print('hello')",
                },
                "session_id": "test-session-ac4",
                "transcript_path": transcript_path,
            }

            # Mock the config path and db path
            with (
                patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path),
                patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
                patch("sys.stdin.read", return_value=json.dumps(hook_data)),
                patch(
                    "pacemaker.intent_validator.validate_intent_and_code"
                ) as mock_validate,
            ):
                # Simulate validation failure
                mock_validate.return_value = {
                    "approved": False,
                    "feedback": "Missing INTENT: marker in message",
                }

                run_pre_tool_hook()

            # Check that blockage was recorded
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT category, reason, hook_type FROM blockage_events")
            rows = cursor.fetchall()
            conn.close()

            assert len(rows) >= 1, "Expected at least one blockage event"
            # Find the intent validation blockage
            intent_blockages = [r for r in rows if r[0] == "intent_validation"]
            assert len(intent_blockages) >= 1, "Expected intent_validation blockage"

        finally:
            Path(transcript_path).unlink(missing_ok=True)

    def test_intent_validation_tdd_failure_records_correct_category(self):
        """TDD validation failure should use intent_validation_tdd category."""
        from pacemaker.hook import run_pre_tool_hook

        # Create a mock transcript
        transcript_file = tempfile.NamedTemporaryFile(
            suffix=".jsonl", delete=False, mode="w"
        )
        transcript_path = transcript_file.name
        transcript_file.write(
            json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "INTENT: modify src/core.py"}
                        ],
                    }
                }
            )
            + "\n"
        )
        transcript_file.close()

        try:
            hook_data = {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/src/core.py",  # Core path requiring TDD
                    "content": "def foo(): pass",
                },
                "session_id": "test-session-tdd",
                "transcript_path": transcript_path,
            }

            with (
                patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path),
                patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
                patch("sys.stdin.read", return_value=json.dumps(hook_data)),
                patch(
                    "pacemaker.intent_validator.validate_intent_and_code"
                ) as mock_validate,
            ):
                # Simulate TDD validation failure
                mock_validate.return_value = {
                    "approved": False,
                    "feedback": "TDD declaration missing for core code",
                    "tdd_failure": True,  # Flag indicating TDD-specific failure
                }

                run_pre_tool_hook()

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT category, reason FROM blockage_events")
            rows = cursor.fetchall()
            conn.close()

            # Should have a TDD blockage
            tdd_blockages = [r for r in rows if r[0] == "intent_validation_tdd"]
            assert len(tdd_blockages) >= 1, "Expected intent_validation_tdd blockage"

        finally:
            Path(transcript_path).unlink(missing_ok=True)

    def test_intent_validation_blockage_includes_details(self):
        """Blockage should include tool name and file path in details."""
        from pacemaker.hook import run_pre_tool_hook

        transcript_file = tempfile.NamedTemporaryFile(
            suffix=".jsonl", delete=False, mode="w"
        )
        transcript_path = transcript_file.name
        transcript_file.write(
            json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Writing code now."}],
                    }
                }
            )
            + "\n"
        )
        transcript_file.close()

        try:
            hook_data = {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "/path/to/file.py",
                    "old_string": "foo",
                    "new_string": "bar",
                },
                "session_id": "test-session-details",
                "transcript_path": transcript_path,
            }

            with (
                patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path),
                patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
                patch("sys.stdin.read", return_value=json.dumps(hook_data)),
                patch(
                    "pacemaker.intent_validator.validate_intent_and_code"
                ) as mock_validate,
            ):
                mock_validate.return_value = {
                    "approved": False,
                    "feedback": "Validation failed",
                }

                run_pre_tool_hook()

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT details FROM blockage_events")
            row = cursor.fetchone()
            conn.close()

            assert row is not None, "Expected a blockage event"
            assert row[0] is not None, "Expected blockage to have details"
            details = json.loads(row[0])
            assert details.get("tool") == "Edit"
            assert details.get("file_path") == "/path/to/file.py"

        finally:
            Path(transcript_path).unlink(missing_ok=True)


# ==============================================================================
# AC5: Pacing Blockage Capture Tests
# ==============================================================================


class TestPacingBlockageCapture:
    """AC5: Pacing throttle delays and tempo failures must be captured."""

    def setup_method(self):
        """Create temporary database and config for each test."""
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

        self.temp_config = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w"
        )
        self.config_path = self.temp_config.name
        json.dump(
            {
                "enabled": True,
                "tempo_mode": "on",
            },
            self.temp_config,
        )
        self.temp_config.close()

        self.temp_state = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w"
        )
        self.state_path = self.temp_state.name
        json.dump(
            {
                "session_id": "test-session-pacing",
                "subagent_counter": 0,
                "in_subagent": False,
            },
            self.temp_state,
        )
        self.temp_state.close()

        # Initialize database
        from pacemaker import database

        database.initialize_database(self.db_path)

    def teardown_method(self):
        """Clean up temporary files."""
        Path(self.db_path).unlink(missing_ok=True)
        Path(self.config_path).unlink(missing_ok=True)
        Path(self.state_path).unlink(missing_ok=True)

    def test_pacing_throttle_records_blockage(self):
        """Throttle delay should record a pacing_quota blockage."""
        from pacemaker.hook import run_hook

        with (
            patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path),
            patch("sys.stdin.read", return_value=json.dumps({"tool_name": "Read"})),
            patch("pacemaker.pacing_engine.run_pacing_check") as mock_pacing,
            patch("pacemaker.hook.execute_delay"),
        ):  # Don't actually sleep
            # Simulate throttle decision
            mock_pacing.return_value = {
                "polled": True,
                "poll_time": None,
                "decision": {
                    "should_throttle": True,
                    "delay_seconds": 30,
                    "strategy": {"delay_seconds": 30},
                },
            }

            run_hook()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT category, reason FROM blockage_events WHERE category = 'pacing_quota'"
        )
        rows = cursor.fetchall()
        conn.close()

        assert len(rows) >= 1, "Expected a pacing_quota blockage event"

    def test_tempo_validation_failure_records_blockage(self):
        """Tempo validation failure should record a pacing_tempo blockage."""
        from pacemaker.hook import run_stop_hook

        # Create transcript
        transcript_file = tempfile.NamedTemporaryFile(
            suffix=".jsonl", delete=False, mode="w"
        )
        transcript_path = transcript_file.name
        transcript_file.write(
            json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Let me check..."}],
                    }
                }
            )
            + "\n"
        )
        transcript_file.close()

        try:
            hook_data = {
                "session_id": "test-session-tempo",
                "transcript_path": transcript_path,
            }

            with (
                patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path),
                patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
                patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path),
                patch("sys.stdin.read", return_value=json.dumps(hook_data)),
                patch("pacemaker.intent_validator.validate_intent") as mock_validate,
            ):
                # Simulate tempo validation failure (blocked exit)
                mock_validate.return_value = {
                    "decision": "block",
                    "reason": "Work appears incomplete",
                }

                run_stop_hook()

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT category, reason FROM blockage_events WHERE category = 'pacing_tempo'"
            )
            rows = cursor.fetchall()
            conn.close()

            assert len(rows) >= 1, "Expected a pacing_tempo blockage event"

        finally:
            Path(transcript_path).unlink(missing_ok=True)

    def test_pacing_blockage_includes_delay_in_details(self):
        """Pacing blockage should include delay duration in details."""
        from pacemaker.hook import run_hook

        with (
            patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path),
            patch("sys.stdin.read", return_value=json.dumps({"tool_name": "Read"})),
            patch("pacemaker.pacing_engine.run_pacing_check") as mock_pacing,
            patch("pacemaker.hook.execute_delay"),
        ):
            mock_pacing.return_value = {
                "polled": True,
                "poll_time": None,
                "decision": {
                    "should_throttle": True,
                    "delay_seconds": 45,
                    "strategy": {"delay_seconds": 45},
                },
            }

            run_hook()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT details FROM blockage_events WHERE category = 'pacing_quota'"
        )
        row = cursor.fetchone()
        conn.close()

        assert row is not None, "Expected a pacing_quota blockage event"
        assert row[0] is not None, "Expected blockage to have details"
        details = json.loads(row[0])
        assert "delay_seconds" in details
        assert details["delay_seconds"] == 45


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
