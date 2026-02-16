#!/usr/bin/env python3
"""
Tests for Langfuse metrics integration with push workflow.

Verifies that metrics counters (sessions, traces) are incremented
correctly when Langfuse pushes succeed.

Story #34: Langfuse Integration Status and Metrics Display
"""

import os
import tempfile
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.pacemaker.langfuse.orchestrator import run_incremental_push
from src.pacemaker.database import initialize_database
from src.pacemaker.langfuse.metrics import get_24h_metrics


class TestLangfuseMetricsIntegration:
    """Test metrics tracking during Langfuse push workflow."""

    @pytest.fixture
    def temp_paths(self):
        """Create temporary database, state directory, and transcript."""
        db_fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)

        state_dir = tempfile.mkdtemp()
        transcript_fd, transcript_path = tempfile.mkstemp(suffix=".jsonl")
        os.close(transcript_fd)

        # Initialize database with schema
        initialize_database(db_path)

        # Create minimal transcript with one line
        with open(transcript_path, "w") as f:
            f.write(
                json.dumps(
                    {
                        "type": "api_response",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Hello"}],
                            "usage": {
                                "input_tokens": 100,
                                "output_tokens": 50,
                                "cache_read_input_tokens": 10,
                            },
                        },
                    }
                )
                + "\n"
            )

        yield {
            "db_path": db_path,
            "state_dir": state_dir,
            "transcript_path": transcript_path,
        }

        # Cleanup
        Path(db_path).unlink(missing_ok=True)
        Path(transcript_path).unlink(missing_ok=True)
        import shutil

        shutil.rmtree(state_dir, ignore_errors=True)

    @pytest.fixture
    def mock_config(self, temp_paths):
        """Config with Langfuse enabled and database path."""
        return {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://test.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
            "db_path": temp_paths["db_path"],
        }

    def test_successful_push_increments_metrics(self, temp_paths, mock_config):
        """Successful push increments sessions and traces counters."""
        # Mock successful API push
        with (
            patch("src.pacemaker.langfuse.push.push_batch_events") as mock_push,
            patch(
                "src.pacemaker.telemetry.jsonl_parser.parse_session_metadata"
            ) as mock_metadata,
            patch("src.pacemaker.telemetry.jsonl_parser.extract_user_id") as mock_user,
        ):

            mock_push.return_value = (
                True,
                1,
            )  # Successful push (returns tuple[bool, int])
            mock_metadata.return_value = {"model": "claude-sonnet-4"}
            mock_user.return_value = "test@example.com"

            # Run incremental push
            success = run_incremental_push(
                config=mock_config,
                session_id="test-session-123",
                transcript_path=temp_paths["transcript_path"],
                state_dir=temp_paths["state_dir"],
                hook_type="user_prompt_submit",
            )

            assert success is True

            # Verify metrics were incremented
            metrics = get_24h_metrics(temp_paths["db_path"])

            # First push should increment sessions (new session)
            assert (
                metrics["sessions"] == 1
            ), "Sessions counter should be incremented on first push"

            # First push should also increment traces (new trace)
            assert (
                metrics["traces"] == 1
            ), "Traces counter should be incremented on push"

    def test_failed_push_does_not_increment_metrics(self, temp_paths, mock_config):
        """Failed pushes should not increment any metrics counters."""
        # Mock failed API push
        with (
            patch("src.pacemaker.langfuse.push.push_batch_events") as mock_push,
            patch(
                "src.pacemaker.telemetry.jsonl_parser.parse_session_metadata"
            ) as mock_metadata,
            patch("src.pacemaker.telemetry.jsonl_parser.extract_user_id") as mock_user,
        ):

            mock_push.return_value = (
                False,
                0,
            )  # Failed push (returns tuple[bool, int])
            mock_metadata.return_value = {"model": "claude-sonnet-4"}
            mock_user.return_value = "test@example.com"

            # Run incremental push
            success = run_incremental_push(
                config=mock_config,
                session_id="test-session-456",
                transcript_path=temp_paths["transcript_path"],
                state_dir=temp_paths["state_dir"],
                hook_type="user_prompt_submit",
            )

            assert success is False

            # Verify metrics were NOT incremented
            metrics = get_24h_metrics(temp_paths["db_path"])

            assert (
                metrics["sessions"] == 0
            ), "Sessions counter should not increment on failed push"
            assert (
                metrics["traces"] == 0
            ), "Traces counter should not increment on failed push"
            assert (
                metrics["spans"] == 0
            ), "Spans counter should not increment on failed push"
