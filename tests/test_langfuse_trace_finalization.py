#!/usr/bin/env python3
"""
Tests for Langfuse trace finalization with output field.

Tests the finalize_trace_with_output function that extracts
Claude's final response from transcript and creates trace-update event.
"""

import json
import tempfile
from pathlib import Path

from pacemaker.langfuse.trace import finalize_trace_with_output


class TestTraceFinalizeWithOutput:
    """Test trace finalization with output extraction from transcript."""

    def create_test_transcript(self, messages: list) -> str:
        """
        Create a temporary transcript file with specified messages.

        Args:
            messages: List of message dicts (each with 'role' and 'content')

        Returns:
            Path to temporary transcript file
        """
        tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl")
        for msg in messages:
            entry = {
                "type": msg["role"],
                "message": {"role": msg["role"], "content": msg["content"]},
            }
            tmp.write(json.dumps(entry) + "\n")
        tmp.close()
        return tmp.name

    def test_finalize_trace_with_single_text_response(self):
        """
        Test extracting output from single assistant response with text content.

        Most common case: user prompt → assistant response with text.
        """
        # Create transcript with user message and assistant response
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "What is 2+2?"}]},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "The answer is 4."}],
            },
        ]
        transcript_path = self.create_test_transcript(messages)

        try:
            # Finalize trace starting from line 0 (beginning of transcript)
            trace_update = finalize_trace_with_output(
                trace_id="test-trace-123",
                transcript_path=transcript_path,
                trace_start_line=0,
            )

            # Verify trace-update event structure
            assert trace_update["id"] == "test-trace-123"
            assert trace_update["output"] == "The answer is 4."
            assert "timestamp" in trace_update

        finally:
            Path(transcript_path).unlink()

    def test_finalize_trace_with_multiple_text_blocks(self):
        """
        Test extracting output from assistant response with multiple text blocks.

        When assistant response has multiple text content blocks, join them.
        """
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "Explain Python"}]},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Python is a programming language."},
                    {"type": "text", "text": "It is widely used for web development."},
                ],
            },
        ]
        transcript_path = self.create_test_transcript(messages)

        try:
            trace_update = finalize_trace_with_output(
                trace_id="test-trace-456",
                transcript_path=transcript_path,
                trace_start_line=0,
            )

            # Multiple text blocks should be joined
            expected_output = "Python is a programming language.\nIt is widely used for web development."
            assert trace_update["output"] == expected_output

        finally:
            Path(transcript_path).unlink()

    def test_finalize_trace_with_tool_calls(self):
        """
        Test extracting output from assistant response with text and tool calls.

        When assistant uses tools, extract only the text content (skip tool_use blocks).
        """
        messages = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Read file test.py"}],
            },
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I'll read the file for you."},
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "/path/to/test.py"},
                    },
                ],
            },
        ]
        transcript_path = self.create_test_transcript(messages)

        try:
            trace_update = finalize_trace_with_output(
                trace_id="test-trace-789",
                transcript_path=transcript_path,
                trace_start_line=0,
            )

            # Should extract only text content, skip tool_use
            assert trace_update["output"] == "I'll read the file for you."

        finally:
            Path(transcript_path).unlink()

    def test_finalize_trace_with_start_line_offset(self):
        """
        Test extracting output when trace starts at specific line (not beginning).

        When trace_start_line > 0, only read from that line forward.
        This handles trace-per-turn where each trace starts at a specific position.
        """
        # Create transcript with multiple turns
        messages = [
            # First turn (lines 0-1)
            {"role": "user", "content": [{"type": "text", "text": "First question"}]},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "First answer"}],
            },
            # Second turn (lines 2-3) - this is what we want
            {"role": "user", "content": [{"type": "text", "text": "Second question"}]},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Second answer"}],
            },
        ]
        transcript_path = self.create_test_transcript(messages)

        try:
            # Start from line 2 (skip first turn)
            trace_update = finalize_trace_with_output(
                trace_id="test-trace-turn2",
                transcript_path=transcript_path,
                trace_start_line=2,
            )

            # Should extract only the second assistant response
            assert trace_update["output"] == "Second answer"

        finally:
            Path(transcript_path).unlink()

    def test_finalize_trace_with_multiple_assistant_responses(self):
        """
        Test extracting output when there are multiple assistant responses after start line.

        Should extract the LAST assistant response (most recent).
        """
        messages = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Do multiple things"}],
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Starting task 1"}],
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Completed task 2"}],
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Final response: all done"}],
            },
        ]
        transcript_path = self.create_test_transcript(messages)

        try:
            trace_update = finalize_trace_with_output(
                trace_id="test-trace-multi",
                transcript_path=transcript_path,
                trace_start_line=0,
            )

            # Should extract the LAST assistant response
            assert trace_update["output"] == "Final response: all done"

        finally:
            Path(transcript_path).unlink()

    def test_finalize_trace_with_no_assistant_response(self):
        """
        Test extracting output when no assistant response exists after start line.

        Edge case: trace created but no assistant response yet.
        Should return empty output.
        """
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "Just a question"}]}
        ]
        transcript_path = self.create_test_transcript(messages)

        try:
            trace_update = finalize_trace_with_output(
                trace_id="test-trace-no-response",
                transcript_path=transcript_path,
                trace_start_line=0,
            )

            # No assistant response → empty output
            assert trace_update["output"] == ""

        finally:
            Path(transcript_path).unlink()

    def test_finalize_trace_with_empty_assistant_response(self):
        """
        Test extracting output when assistant response has no text content.

        Edge case: assistant response with only tool calls, no text.
        """
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "Run this command"}]},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}
                ],
            },
        ]
        transcript_path = self.create_test_transcript(messages)

        try:
            trace_update = finalize_trace_with_output(
                trace_id="test-trace-empty",
                transcript_path=transcript_path,
                trace_start_line=0,
            )

            # No text content → empty output
            assert trace_update["output"] == ""

        finally:
            Path(transcript_path).unlink()

    def test_finalize_trace_includes_token_counts(self):
        """
        Test that trace finalization includes token counts in metadata.

        Token counts should be extracted from transcript messages and
        included in trace metadata for cost tracking.
        """
        # Create transcript with messages containing usage data
        tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl")

        # User message (line 0)
        tmp.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "What is 2+2?"}],
                    },
                }
            )
            + "\n"
        )

        # Assistant message with usage data (line 1)
        tmp.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "The answer is 4."}],
                        "usage": {
                            "input_tokens": 1000,
                            "output_tokens": 200,
                            "cache_read_input_tokens": 500,
                        },
                    },
                }
            )
            + "\n"
        )

        # Another assistant message with more usage (line 2)
        tmp.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Anything else?"}],
                        "usage": {
                            "input_tokens": 1200,
                            "output_tokens": 100,
                            "cache_read_input_tokens": 300,
                        },
                    },
                }
            )
            + "\n"
        )

        tmp.close()
        transcript_path = tmp.name

        try:
            trace_update = finalize_trace_with_output(
                trace_id="test-trace-tokens",
                transcript_path=transcript_path,
                trace_start_line=0,
            )

            # Should include token counts in metadata
            assert "metadata" in trace_update
            assert "input_tokens" in trace_update["metadata"]
            assert "output_tokens" in trace_update["metadata"]
            assert "cache_read_tokens" in trace_update["metadata"]

            # Should sum up all token counts from trace_start_line forward
            assert trace_update["metadata"]["input_tokens"] == 2200  # 1000 + 1200
            assert trace_update["metadata"]["output_tokens"] == 300  # 200 + 100
            assert trace_update["metadata"]["cache_read_tokens"] == 800  # 500 + 300

        finally:
            Path(transcript_path).unlink()

    def test_finalize_trace_with_partial_token_data(self):
        """
        Test finalization when some messages lack usage data.

        Should gracefully handle messages without usage field and
        only count tokens from messages that have usage data.
        """
        tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl")

        # Message without usage
        tmp.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Response 1"}],
                    },
                }
            )
            + "\n"
        )

        # Message with usage
        tmp.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Response 2"}],
                        "usage": {
                            "input_tokens": 500,
                            "output_tokens": 100,
                            "cache_read_input_tokens": 0,
                        },
                    },
                }
            )
            + "\n"
        )

        tmp.close()
        transcript_path = tmp.name

        try:
            trace_update = finalize_trace_with_output(
                trace_id="test-trace-partial-tokens",
                transcript_path=transcript_path,
                trace_start_line=0,
            )

            # Should only count tokens from message with usage data
            assert trace_update["metadata"]["input_tokens"] == 500
            assert trace_update["metadata"]["output_tokens"] == 100
            assert trace_update["metadata"]["cache_read_tokens"] == 0

        finally:
            Path(transcript_path).unlink()

    def test_finalize_trace_with_no_token_data(self):
        """
        Test finalization when no messages have usage data.

        Should return zero token counts when no usage data available.
        """
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "Question"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Answer"}]},
        ]
        transcript_path = self.create_test_transcript(messages)

        try:
            trace_update = finalize_trace_with_output(
                trace_id="test-trace-no-tokens",
                transcript_path=transcript_path,
                trace_start_line=0,
            )

            # Should have zero token counts
            assert trace_update["metadata"]["input_tokens"] == 0
            assert trace_update["metadata"]["output_tokens"] == 0
            assert trace_update["metadata"]["cache_read_tokens"] == 0

        finally:
            Path(transcript_path).unlink()
