#!/usr/bin/env python3
"""
Tests for Langfuse hook integration.

Tests AC1, AC2, AC3, AC5: Hook integration and timeout behavior
- AC1: Incremental push on UserPromptSubmit
- AC2: Incremental push on PostToolUse
- AC3: State cleanup for stale files
- AC5: Timeout and non-blocking behavior
"""

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from pacemaker.langfuse.orchestrator import (
    run_incremental_push,
    should_run_langfuse_push,
)

# AC5: Timeout configuration for incremental push
# Push timeout is 2 seconds to ensure hooks complete quickly
PUSH_TIMEOUT_SECONDS = 2
# Test margin accounts for overhead (assertion time, mock setup, etc)
TEST_TIMEOUT_MARGIN = 0.5
# Slow API simulation exceeds timeout to trigger abort
SLOW_API_DELAY_SECONDS = 3


class TestIncrementalPushOrchestrator:
    """Test the orchestrator that coordinates incremental pushes."""

    @pytest.fixture
    def config(self):
        """Config with Langfuse enabled."""
        return {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://cloud.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

    @pytest.fixture
    def state_dir(self):
        """Create temporary state directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def transcript_file(self):
        """Create temporary transcript file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Write transcript data in correct Claude Code format
            f.write(
                json.dumps(
                    {
                        "type": "session_start",
                        "session_id": "test-123",
                        "model": "claude-sonnet-4-5",
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps({"message": {"role": "user", "content": "Hello"}}) + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "message": {
                            "role": "assistant",
                            "usage": {"input_tokens": 10, "output_tokens": 20},
                        }
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "tool_use", "name": "Read"}],
                        }
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "message": {
                            "role": "assistant",
                            "usage": {"input_tokens": 15, "output_tokens": 25},
                        }
                    }
                )
                + "\n"
            )
            transcript_path = f.name

        yield transcript_path

        # Cleanup
        Path(transcript_path).unlink()

    def test_first_push_creates_state_and_trace(
        self, config, state_dir, transcript_file
    ):
        """
        Test first incremental push creates state and trace.

        AC1: First push on UserPromptSubmit creates new trace
        """
        session_id = "test-session-first"

        with patch("pacemaker.langfuse.push.push_batch_events") as mock_push:
            mock_push.return_value = (True, 2)

            # Run first push
            success = run_incremental_push(
                config=config,
                session_id=session_id,
                transcript_path=transcript_file,
                state_dir=state_dir,
                hook_type="user_prompt_submit",
            )

            assert success is True

            # Verify push was called with batch
            assert mock_push.called
            # Extract batch from mock call (4th positional arg: base_url, public_key, secret_key, batch)
            call_args = mock_push.call_args[0]
            batch = call_args[3]
            assert isinstance(batch, list)
            assert len(batch) == 2  # trace + generation

            # Verify trace in batch
            trace = batch[0]["body"]
            assert trace["id"] == session_id
            assert trace["metadata"]["input_tokens"] == 25  # 10 + 15
            assert trace["metadata"]["output_tokens"] == 45  # 20 + 25

            # Verify generation in batch
            generation = batch[1]["body"]
            assert generation["traceId"] == session_id
            assert generation["usage"]["input"] == 25
            assert generation["usage"]["output"] == 45

        # Verify state file created
        state_file = Path(state_dir) / f"{session_id}.json"
        assert state_file.exists()

        with open(state_file) as f:
            state = json.load(f)

        assert state["session_id"] == session_id
        assert state["trace_id"] == session_id
        assert state["last_pushed_line"] == 5  # All 5 lines pushed

    def test_subsequent_push_updates_trace(self, config, state_dir, transcript_file):
        """
        Test subsequent push updates existing trace.

        AC2: Subsequent push on PostToolUse updates trace incrementally
        """
        session_id = "test-session-update"

        # Create initial state (simulate previous push)
        state_file = Path(state_dir) / f"{session_id}.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "session_id": session_id,
                    "trace_id": session_id,
                    "last_pushed_line": 3,  # Previously pushed lines 1-3
                    "metadata": {
                        "tool_calls": ["Read"],
                        "tool_count": 1,
                        "input_tokens": 10,
                        "output_tokens": 20,
                        "cache_read_tokens": 0,
                    },
                },
                f,
            )

        with patch("pacemaker.langfuse.push.push_batch_events") as mock_push:
            mock_push.return_value = (True, 2)

            # Run incremental push (should only process lines 4-5)
            success = run_incremental_push(
                config=config,
                session_id=session_id,
                transcript_path=transcript_file,
                state_dir=state_dir,
                hook_type="post_tool_use",
            )

            assert success is True

            # Verify push was called with batch
            assert mock_push.called
            # Extract batch from mock call
            call_args = mock_push.call_args[0]
            batch = call_args[3]
            assert isinstance(batch, list)
            assert len(batch) == 2

            # Verify trace has accumulated tokens (10+15=25, 20+25=45)
            trace = batch[0]["body"]
            assert (
                trace["metadata"]["input_tokens"] == 25
            )  # Accumulated: 10 (previous) + 15 (new)
            assert (
                trace["metadata"]["output_tokens"] == 45
            )  # Accumulated: 20 (previous) + 25 (new)

            # Verify generation has accumulated tokens
            generation = batch[1]["body"]
            assert generation["usage"]["input"] == 25
            assert generation["usage"]["output"] == 45

        # Verify state updated
        with open(state_file) as f:
            state = json.load(f)

        assert state["last_pushed_line"] == 5  # Now all lines pushed

    def test_timeout_behavior_non_blocking(self, config, state_dir, transcript_file):
        """
        Test timeout aborts push without blocking session.

        AC5: Timeout and non-blocking behavior
        CRITICAL: State is STILL created even on failure to prevent duplicate pushes
        """
        session_id = "test-session-timeout"

        with patch("pacemaker.langfuse.push.push_batch_events") as mock_push:
            # Simulate timeout by returning (False, 0)
            mock_push.return_value = (False, 0)

            # Run push (should fail gracefully)
            success = run_incremental_push(
                config=config,
                session_id=session_id,
                transcript_path=transcript_file,
                state_dir=state_dir,
                hook_type="user_prompt_submit",
            )

            # Should return False but not raise exception
            assert success is False

        # CRITICAL: State IS created even on failure (to prevent duplicate pushes)
        state_file = Path(state_dir) / f"{session_id}.json"
        assert state_file.exists()  # State created to track last_pushed_line

    def test_push_with_slow_api_times_out(self, config, state_dir, transcript_file):
        """
        Test push times out with slow Langfuse API.

        AC5: Push is aborted at 2-second timeout

        Tests complete timeout chain: requests.post raises Timeout ->
        push.push_trace catches it and returns False ->
        orchestrator handles failure gracefully
        """
        session_id = "test-session-slow"

        # Mock at requests level to test real timeout handling in push.py
        with patch("requests.post") as mock_post:
            # Simulate timeout exception that push.py will catch
            mock_post.side_effect = requests.exceptions.Timeout("Connection timed out")

            start_time = time.time()

            # Run push (should handle timeout gracefully)
            success = run_incremental_push(
                config=config,
                session_id=session_id,
                transcript_path=transcript_file,
                state_dir=state_dir,
                hook_type="user_prompt_submit",
            )

            elapsed = time.time() - start_time

            # Should fail due to timeout and complete quickly
            PUSH_TIMEOUT_SECONDS + TEST_TIMEOUT_MARGIN
            assert success is False
            # Should complete immediately (no actual network delay in test)
            assert elapsed < 1.0

    def test_failed_push_updates_state_to_prevent_duplicates(
        self, config, state_dir, transcript_file
    ):
        """
        Test failed push STILL updates last_pushed_line to prevent duplicates.

        CRITICAL FIX: State is updated even on failure to prevent duplicate spans.
        When timeout occurs, data may have been sent to Langfuse (server just
        didn't respond in time). We must update last_pushed_line to prevent
        re-processing the same lines on next hook call.
        """
        session_id = "test-session-retain"

        # Create initial state
        state_file = Path(state_dir) / f"{session_id}.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "session_id": session_id,
                    "trace_id": session_id,
                    "last_pushed_line": 3,
                },
                f,
            )

        with patch("pacemaker.langfuse.push.push_batch_events") as mock_push:
            mock_push.return_value = (False, 0)  # Simulate failure

            # Run push (should fail)
            success = run_incremental_push(
                config=config,
                session_id=session_id,
                transcript_path=transcript_file,
                state_dir=state_dir,
                hook_type="post_tool_use",
            )

            assert success is False

        # CRITICAL: State IS updated even on failure (to prevent duplicate pushes)
        with open(state_file) as f:
            state = json.load(f)

        assert state["last_pushed_line"] == 5  # Updated to prevent duplicates

    def test_push_disabled_when_langfuse_disabled(self, state_dir, transcript_file):
        """Test push is skipped when Langfuse disabled in config."""
        config = {"langfuse_enabled": False}
        session_id = "test-session-disabled"

        with patch("pacemaker.langfuse.push.push_trace") as mock_push:
            # Run push (should be skipped)
            success = run_incremental_push(
                config=config,
                session_id=session_id,
                transcript_path=transcript_file,
                state_dir=state_dir,
                hook_type="user_prompt_submit",
            )

            # Should return True (nothing to do) but not call push
            assert success is True
            assert not mock_push.called


class TestShouldRunLangfusePush:
    """Test decision logic for when to run Langfuse push."""

    def test_should_run_when_enabled_and_configured(self):
        """Test push should run when Langfuse is enabled and configured."""
        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://cloud.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

        assert should_run_langfuse_push(config) is True

    def test_should_not_run_when_disabled(self):
        """Test push should not run when Langfuse is disabled."""
        config = {
            "langfuse_enabled": False,
            "langfuse_base_url": "https://cloud.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

        assert should_run_langfuse_push(config) is False

    def test_should_not_run_when_missing_credentials(self):
        """Test push should not run when credentials missing."""
        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://cloud.langfuse.com",
            # Missing public_key and secret_key
        }

        assert should_run_langfuse_push(config) is False

    def test_should_not_run_when_missing_base_url(self):
        """Test push should not run when base_url missing."""
        config = {
            "langfuse_enabled": True,
            # Missing base_url
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

        assert should_run_langfuse_push(config) is False


class TestStateCleanup:
    """Test state cleanup functionality for stale state files."""

    @pytest.fixture
    def state_dir(self):
        """Create temporary state directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_cleanup_removes_old_state_files(self, state_dir):
        """
        Test cleanup removes state files older than 7 days.

        AC3: Stale state files (>7 days old) are cleaned up
        """
        from pacemaker.langfuse.state import StateManager

        manager = StateManager(state_dir)

        # Create old state file (8 days ago)
        old_session = "old-session"
        manager.create_or_update(old_session, trace_id="old-trace", last_pushed_line=10)
        old_file = Path(state_dir) / f"{old_session}.json"

        # Set mtime to 8 days ago
        eight_days_ago = time.time() - (8 * 24 * 60 * 60)
        os.utime(old_file, (eight_days_ago, eight_days_ago))

        # Create recent state file
        recent_session = "recent-session"
        manager.create_or_update(
            recent_session, trace_id="recent-trace", last_pushed_line=5
        )
        recent_file = Path(state_dir) / f"{recent_session}.json"

        # Run cleanup
        manager.cleanup_stale_files(max_age_days=7)

        # Verify old file deleted, recent file preserved
        assert not old_file.exists()
        assert recent_file.exists()
