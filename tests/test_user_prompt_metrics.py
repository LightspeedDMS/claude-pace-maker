#!/usr/bin/env python3
"""
Test metrics tracking in handle_user_prompt_submit() for Story #34.

Verifies that handle_user_prompt_submit() properly increments:
- sessions counter (first trace in new session)
- traces counter (every trace created)
"""

import json
import tempfile
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from pacemaker.langfuse.orchestrator import handle_user_prompt_submit
from pacemaker.database import initialize_database


class TestUserPromptSubmitMetrics:
    """Test metrics tracking in handle_user_prompt_submit()."""

    @pytest.fixture
    def mock_config(self):
        """Mock configuration with Langfuse enabled."""
        return {
            "langfuse_enabled": True,
            "langfuse_base_url": "http://192.168.68.42:3000",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

    @pytest.fixture
    def test_db(self):
        """Create temporary database for testing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".db", delete=False) as f:
            db_path = f.name

        initialize_database(db_path)
        yield db_path

        Path(db_path).unlink()

    @pytest.fixture
    def transcript_with_user_prompt(self):
        """Create transcript with user prompt."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Session start
            f.write(
                json.dumps(
                    {
                        "type": "session_start",
                        "session_id": "test-123",
                        "model": "claude-sonnet-4-5",
                        "user_email": "user@example.com",
                    }
                )
                + "\n"
            )
            # User prompt
            f.write(
                json.dumps(
                    {"message": {"role": "user", "content": "implement feature X"}}
                )
                + "\n"
            )
            transcript_path = f.name

        yield transcript_path

        Path(transcript_path).unlink()

    def test_first_trace_increments_sessions_and_traces(
        self, mock_config, test_db, transcript_with_user_prompt
    ):
        """
        Test that first trace in session increments both sessions and traces counters.

        BUG: handle_user_prompt_submit() creates traces but never increments metrics.
        This test verifies the fix for Story #34.

        Expected behavior:
        - First trace in session: increment sessions counter
        - All traces: increment traces counter
        """
        with tempfile.TemporaryDirectory() as state_dir:
            session_id = "test-session-new"
            user_message = "implement feature X"

            # Add db_path to config
            config_with_db = {**mock_config, "db_path": test_db}

            # Mock push to avoid network call
            with patch(
                "pacemaker.langfuse.orchestrator.push.push_batch_events"
            ) as mock_push:
                mock_push.return_value = True

                # Call handle_user_prompt_submit (first trace in session)
                result = handle_user_prompt_submit(
                    config=config_with_db,
                    session_id=session_id,
                    transcript_path=transcript_with_user_prompt,
                    state_dir=state_dir,
                    user_message=user_message,
                )

                assert result is True

                # Verify metrics were incremented
                conn = sqlite3.connect(test_db)
                cursor = conn.cursor()

                # Check sessions counter
                cursor.execute(
                    "SELECT COALESCE(SUM(sessions_count), 0) FROM langfuse_metrics"
                )
                sessions_count = cursor.fetchone()[0]
                assert (
                    sessions_count == 1
                ), "Sessions counter should be incremented on first trace"

                # Check traces counter
                cursor.execute(
                    "SELECT COALESCE(SUM(traces_count), 0) FROM langfuse_metrics"
                )
                traces_count = cursor.fetchone()[0]
                assert traces_count == 1, "Traces counter should be incremented"

                conn.close()

    def test_subsequent_traces_increment_only_traces(
        self, mock_config, test_db, transcript_with_user_prompt
    ):
        """
        Test that subsequent traces in same session increment only traces counter.

        Expected behavior:
        - First trace: sessions=1, traces=1
        - Second trace: sessions=1, traces=2 (sessions unchanged)
        """
        with tempfile.TemporaryDirectory() as state_dir:
            session_id = "test-session-existing"
            user_message = "implement feature Y"

            # Add db_path to config
            config_with_db = {**mock_config, "db_path": test_db}

            # Mock push to avoid network call
            with patch(
                "pacemaker.langfuse.orchestrator.push.push_batch_events"
            ) as mock_push:
                mock_push.return_value = True

                # First trace
                result1 = handle_user_prompt_submit(
                    config=config_with_db,
                    session_id=session_id,
                    transcript_path=transcript_with_user_prompt,
                    state_dir=state_dir,
                    user_message=user_message,
                )
                assert result1 is True

                # Second trace in same session
                result2 = handle_user_prompt_submit(
                    config=config_with_db,
                    session_id=session_id,
                    transcript_path=transcript_with_user_prompt,
                    state_dir=state_dir,
                    user_message="implement feature Z",
                )
                assert result2 is True

                # Verify metrics
                conn = sqlite3.connect(test_db)
                cursor = conn.cursor()

                # Sessions should be 1 (only first trace increments)
                cursor.execute(
                    "SELECT COALESCE(SUM(sessions_count), 0) FROM langfuse_metrics"
                )
                sessions_count = cursor.fetchone()[0]
                assert (
                    sessions_count == 1
                ), "Sessions counter should only increment on first trace"

                # Traces should be 2 (both traces increment)
                cursor.execute(
                    "SELECT COALESCE(SUM(traces_count), 0) FROM langfuse_metrics"
                )
                traces_count = cursor.fetchone()[0]
                assert (
                    traces_count == 2
                ), "Traces counter should increment for each trace"

                conn.close()

    def test_failed_push_does_not_increment_metrics(
        self, mock_config, test_db, transcript_with_user_prompt
    ):
        """
        Test that metrics are not incremented if push fails.

        Expected behavior:
        - Push fails → handle_user_prompt_submit returns False
        - Metrics remain at 0
        """
        with tempfile.TemporaryDirectory() as state_dir:
            session_id = "test-session-failed"
            user_message = "implement feature X"

            # Add db_path to config
            config_with_db = {**mock_config, "db_path": test_db}

            # Mock push to fail
            with patch(
                "pacemaker.langfuse.orchestrator.push.push_batch_events"
            ) as mock_push:
                mock_push.return_value = False

                result = handle_user_prompt_submit(
                    config=config_with_db,
                    session_id=session_id,
                    transcript_path=transcript_with_user_prompt,
                    state_dir=state_dir,
                    user_message=user_message,
                )

                # Should fail
                assert result is False

                # Verify metrics were NOT incremented
                conn = sqlite3.connect(test_db)
                cursor = conn.cursor()

                cursor.execute(
                    "SELECT COALESCE(SUM(sessions_count), 0) FROM langfuse_metrics"
                )
                sessions_count = cursor.fetchone()[0]
                assert (
                    sessions_count == 0
                ), "Sessions counter should not increment on failed push"

                cursor.execute(
                    "SELECT COALESCE(SUM(traces_count), 0) FROM langfuse_metrics"
                )
                traces_count = cursor.fetchone()[0]
                assert (
                    traces_count == 0
                ), "Traces counter should not increment on failed push"

                conn.close()
