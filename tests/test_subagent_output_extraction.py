#!/usr/bin/env python3
"""
Tests for subagent output extraction from subagent's own transcript.

Tests the new extract_subagent_output() function that reads subagent's
transcript JSONL file and extracts the last assistant message.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from pacemaker.langfuse.orchestrator import (
    extract_subagent_output,
    handle_subagent_stop,
)


class TestExtractSubagentOutput:
    """Test subagent output extraction from subagent transcript."""

    def setup_method(self):
        """Set up test fixtures before each test."""
        self.transcript_dir = tempfile.mkdtemp()

    def teardown_method(self):
        """Clean up after each test."""
        import shutil

        shutil.rmtree(self.transcript_dir, ignore_errors=True)

    def create_transcript(self, messages: list) -> str:
        """Create temporary transcript file."""
        transcript_path = Path(self.transcript_dir) / "subagent_transcript.jsonl"
        with open(transcript_path, "w") as f:
            for msg in messages:
                entry = {
                    "type": msg["role"],
                    "message": {"role": msg["role"], "content": msg["content"]},
                }
                f.write(json.dumps(entry) + "\n")
        return str(transcript_path)

    def test_extract_output_with_string_content(self):
        """Test extracting output when content is a string."""
        messages = [
            {"role": "user", "content": "Test the function"},
            {"role": "assistant", "content": "Subagent output here"},
        ]
        transcript_path = self.create_transcript(messages)

        result = extract_subagent_output(transcript_path)

        assert result == "Subagent output here"

    def test_extract_output_with_array_content(self):
        """Test extracting output when content is an array of blocks."""
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "Test"}]},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "First block\n"},
                    {"type": "text", "text": "Second block"},
                ],
            },
        ]
        transcript_path = self.create_transcript(messages)

        result = extract_subagent_output(transcript_path)

        assert result == "First block\nSecond block"

    def test_extract_output_returns_last_assistant_message(self):
        """Test that function returns LAST assistant message when multiple exist."""
        messages = [
            {"role": "user", "content": "First request"},
            {"role": "assistant", "content": "First response"},
            {"role": "user", "content": "Second request"},
            {"role": "assistant", "content": "Second response"},
            {"role": "user", "content": "Third request"},
            {"role": "assistant", "content": "LAST response"},
        ]
        transcript_path = self.create_transcript(messages)

        result = extract_subagent_output(transcript_path)

        assert result == "LAST response"

    def test_extract_output_with_missing_transcript(self):
        """Test that function returns None when transcript file doesn't exist."""
        result = extract_subagent_output("/nonexistent/path.jsonl")

        assert result is None

    def test_extract_output_with_no_assistant_messages(self):
        """Test that function returns None when no assistant messages found."""
        messages = [{"role": "user", "content": "Only user message"}]
        transcript_path = self.create_transcript(messages)

        result = extract_subagent_output(transcript_path)

        assert result is None

    def test_extract_output_with_empty_transcript(self):
        """Test that function returns None with empty transcript."""
        transcript_path = Path(self.transcript_dir) / "empty.jsonl"
        transcript_path.touch()  # Create empty file

        result = extract_subagent_output(str(transcript_path))

        assert result is None

    def test_extract_output_ignores_malformed_lines(self):
        """Test that function gracefully skips malformed JSON lines."""
        transcript_path = Path(self.transcript_dir) / "malformed.jsonl"
        with open(transcript_path, "w") as f:
            f.write('{"invalid json\n')  # Malformed line
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"role": "assistant", "content": "Valid output"},
                    }
                )
                + "\n"
            )

        result = extract_subagent_output(str(transcript_path))

        assert result == "Valid output"

    def test_extract_output_with_mixed_content_types(self):
        """Test extraction when content has non-text blocks."""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Text block"},
                    {"type": "tool_use", "name": "SomeTool", "input": {}},
                    {"type": "text", "text": "More text"},
                ],
            }
        ]
        transcript_path = self.create_transcript(messages)

        result = extract_subagent_output(transcript_path)

        # Should only extract text blocks
        assert result == "Text blockMore text"


class TestHandleSubagentStopWithTranscriptPath:
    """Test handle_subagent_stop with agent_transcript_path parameter."""

    def setup_method(self):
        """Set up test fixtures."""
        self.state_dir = tempfile.mkdtemp()
        self.transcript_dir = tempfile.mkdtemp()

    def teardown_method(self):
        """Clean up after each test."""
        import shutil

        shutil.rmtree(self.state_dir, ignore_errors=True)
        shutil.rmtree(self.transcript_dir, ignore_errors=True)

    def create_subagent_transcript(self, output_text: str) -> str:
        """Create subagent transcript with output."""
        transcript_path = Path(self.transcript_dir) / "subagent.jsonl"
        with open(transcript_path, "w") as f:
            entry = {
                "type": "assistant",
                "message": {"role": "assistant", "content": output_text},
            }
            f.write(json.dumps(entry) + "\n")
        return str(transcript_path)

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_uses_agent_transcript_path_when_provided(self, mock_push):
        """Test that agent_transcript_path is used when provided."""
        mock_push.return_value = True

        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://test.com",
            "langfuse_public_key": "pk_test",
            "langfuse_secret_key": "sk_test",
        }

        subagent_output = "Output from subagent transcript"
        agent_transcript_path = self.create_subagent_transcript(subagent_output)

        result = handle_subagent_stop(
            config=config,
            subagent_trace_id="test-trace-123",
            parent_transcript_path="/parent/transcript.jsonl",
            agent_id="agent-456",
            agent_transcript_path=agent_transcript_path,
        )

        assert result is True

        # Verify push was called with correct output
        assert mock_push.called
        call_args = mock_push.call_args
        batch = call_args[0][3]  # batch is 4th positional arg

        assert len(batch) == 1
        trace_update = batch[0]["body"]
        assert trace_update["output"] == subagent_output

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    @patch("pacemaker.langfuse.orchestrator.extract_task_tool_result")
    def test_falls_back_to_parent_transcript_when_no_agent_path(
        self, mock_extract_task, mock_push
    ):
        """Test backward compatibility: falls back to parent transcript search."""
        mock_push.return_value = True
        mock_extract_task.return_value = "Output from parent Task result"

        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://test.com",
            "langfuse_public_key": "pk_test",
            "langfuse_secret_key": "sk_test",
        }

        result = handle_subagent_stop(
            config=config,
            subagent_trace_id="test-trace-123",
            parent_transcript_path="/parent/transcript.jsonl",
            agent_id="agent-456",
            agent_transcript_path=None,  # No agent path provided
        )

        assert result is True

        # Verify fallback was used
        mock_extract_task.assert_called_once_with(
            "/parent/transcript.jsonl", agent_id="agent-456"
        )

        # Verify push was called with fallback output
        assert mock_push.called
        call_args = mock_push.call_args
        batch = call_args[0][3]

        trace_update = batch[0]["body"]
        assert trace_update["output"] == "Output from parent Task result"

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_uses_empty_string_when_transcript_not_found(self, mock_push):
        """Test that empty string is used when agent transcript doesn't exist."""
        mock_push.return_value = True

        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://test.com",
            "langfuse_public_key": "pk_test",
            "langfuse_secret_key": "sk_test",
        }

        result = handle_subagent_stop(
            config=config,
            subagent_trace_id="test-trace-123",
            parent_transcript_path=None,
            agent_id="agent-456",
            agent_transcript_path="/nonexistent/transcript.jsonl",
        )

        assert result is True

        # Verify push was called with empty string
        assert mock_push.called
        call_args = mock_push.call_args
        batch = call_args[0][3]

        trace_update = batch[0]["body"]
        assert trace_update["output"] == ""
