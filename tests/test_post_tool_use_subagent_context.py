#!/usr/bin/env python3
"""
Tests for handle_post_tool_use() subagent context detection.

When PostToolUse fires in a subagent, it should use the subagent's trace_id
instead of the parent's trace_id for creating spans.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


@pytest.fixture
def base_config():
    """Base Langfuse configuration."""
    return {
        "langfuse_enabled": True,
        "langfuse_base_url": "https://langfuse.example.com",
        "langfuse_public_key": "pk-test",
        "langfuse_secret_key": "sk-test",
    }


@pytest.fixture
def setup_test_files(tmp_path):
    """
    Factory fixture for setting up all test files.

    Creates transcript, Langfuse state, and pacemaker state files.
    Returns paths dict for use in tests.
    """

    def _setup(in_subagent=False, subagent_trace_id=None):
        # Create transcript
        transcript = tmp_path / "session.jsonl"
        entry = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Test content"}],
            },
        }
        with open(transcript, "w") as f:
            f.write(json.dumps(entry) + "\n")

        # Create Langfuse state directory and file
        langfuse_state_dir = tmp_path / "langfuse_state"
        langfuse_state_dir.mkdir()

        langfuse_state_file = langfuse_state_dir / "parent-session-123.json"
        langfuse_state_data = {
            "session_id": "parent-session-123",
            "trace_id": "parent-trace-456",
            "last_pushed_line": 0,
            "metadata": {
                "current_trace_id": "parent-trace-456",
                "trace_start_line": 0,
            },
        }
        with open(langfuse_state_file, "w") as f:
            json.dump(langfuse_state_data, f)

        # Create pacemaker state file
        pacemaker_state_file = tmp_path / "state.json"
        pacemaker_state_data = {"in_subagent": in_subagent}

        if subagent_trace_id:
            pacemaker_state_data["current_subagent_trace_id"] = subagent_trace_id
            pacemaker_state_data["current_subagent_agent_id"] = "agent-abc"

        with open(pacemaker_state_file, "w") as f:
            json.dump(pacemaker_state_data, f)

        return {
            "transcript": str(transcript),
            "langfuse_state_dir": str(langfuse_state_dir),
            "pacemaker_state": str(pacemaker_state_file),
        }

    return _setup


class TestHandlePostToolUseSubagentContext:
    """Tests for subagent context detection in handle_post_tool_use."""

    def test_uses_subagent_trace_id_when_in_subagent(
        self, base_config, setup_test_files
    ):
        """
        Verify spans use subagent trace_id when in_subagent is True.
        """
        paths = setup_test_files(
            in_subagent=True, subagent_trace_id="subagent-trace-789"
        )

        with patch("pacemaker.langfuse.orchestrator.push") as mock_push_module:
            mock_push_module.push_batch_events = MagicMock(return_value=True)

            # Patch DEFAULT_STATE_PATH to point to our test file
            with patch(
                "pacemaker.langfuse.orchestrator.DEFAULT_STATE_PATH",
                paths["pacemaker_state"],
            ):
                from pacemaker.langfuse.orchestrator import handle_post_tool_use

                result = handle_post_tool_use(
                    config=base_config,
                    session_id="parent-session-123",
                    transcript_path=paths["transcript"],
                    state_dir=paths["langfuse_state_dir"],
                )

                assert result is True

                # Verify spans use subagent trace_id
                args, _ = mock_push_module.push_batch_events.call_args
                batch = args[3]

                for event in batch:
                    assert event["type"] == "span-create"
                    assert event["body"]["traceId"] == "subagent-trace-789"

    def test_uses_parent_trace_id_when_not_in_subagent(
        self, base_config, setup_test_files
    ):
        """
        Verify spans use parent trace_id when in_subagent is False.
        """
        paths = setup_test_files(in_subagent=False)

        with patch("pacemaker.langfuse.orchestrator.push") as mock_push_module:
            mock_push_module.push_batch_events = MagicMock(return_value=True)

            with patch(
                "pacemaker.langfuse.orchestrator.DEFAULT_STATE_PATH",
                paths["pacemaker_state"],
            ):
                from pacemaker.langfuse.orchestrator import handle_post_tool_use

                result = handle_post_tool_use(
                    config=base_config,
                    session_id="parent-session-123",
                    transcript_path=paths["transcript"],
                    state_dir=paths["langfuse_state_dir"],
                )

                assert result is True

                # Verify spans use parent trace_id (normal behavior)
                args, _ = mock_push_module.push_batch_events.call_args
                batch = args[3]

                for event in batch:
                    assert event["type"] == "span-create"
                    assert event["body"]["traceId"] == "parent-trace-456"

    def test_handles_missing_pacemaker_state_gracefully(
        self, base_config, setup_test_files
    ):
        """
        Verify graceful fallback to parent trace_id when pacemaker state is missing.
        """
        paths = setup_test_files(in_subagent=False)

        # Point to non-existent file
        nonexistent_path = str(
            Path(paths["pacemaker_state"]).parent / "nonexistent.json"
        )

        with patch("pacemaker.langfuse.orchestrator.push") as mock_push_module:
            mock_push_module.push_batch_events = MagicMock(return_value=True)

            with patch(
                "pacemaker.langfuse.orchestrator.DEFAULT_STATE_PATH", nonexistent_path
            ):
                from pacemaker.langfuse.orchestrator import handle_post_tool_use

                result = handle_post_tool_use(
                    config=base_config,
                    session_id="parent-session-123",
                    transcript_path=paths["transcript"],
                    state_dir=paths["langfuse_state_dir"],
                )

                assert result is True

                # Verify fallback to parent trace_id
                args, _ = mock_push_module.push_batch_events.call_args
                batch = args[3]

                for event in batch:
                    assert event["body"]["traceId"] == "parent-trace-456"

    def test_handles_malformed_pacemaker_state_gracefully(
        self, base_config, setup_test_files, tmp_path
    ):
        """
        Verify graceful fallback when pacemaker state has invalid JSON.
        """
        paths = setup_test_files(in_subagent=False)

        # Create malformed state file
        malformed_state = tmp_path / "malformed_state.json"
        malformed_state.write_text("{ invalid json")

        with patch("pacemaker.langfuse.orchestrator.push") as mock_push_module:
            mock_push_module.push_batch_events = MagicMock(return_value=True)

            with patch(
                "pacemaker.langfuse.orchestrator.DEFAULT_STATE_PATH",
                str(malformed_state),
            ):
                from pacemaker.langfuse.orchestrator import handle_post_tool_use

                result = handle_post_tool_use(
                    config=base_config,
                    session_id="parent-session-123",
                    transcript_path=paths["transcript"],
                    state_dir=paths["langfuse_state_dir"],
                )

                assert result is True

                # Verify fallback to parent trace_id
                args, _ = mock_push_module.push_batch_events.call_args
                batch = args[3]

                for event in batch:
                    assert event["body"]["traceId"] == "parent-trace-456"
