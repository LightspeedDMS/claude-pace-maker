#!/usr/bin/env python3
"""
Tests for extracting content blocks from transcript JSONL.

This tests the NEW functionality needed for refactoring Langfuse integration:
- Extract text blocks from assistant messages
- Extract tool_use blocks from assistant messages
- Support incremental extraction with line offsets
- Avoid duplicates on repeated calls

Note: tool_result extraction is omitted for this iteration (optional for span linking).
"""

import json
import tempfile
from pathlib import Path

import pytest

from pacemaker.langfuse.incremental import extract_content_blocks


class TestContentBlockExtraction:
    """Test extracting content blocks from transcript for span creation."""

    @pytest.fixture
    def transcript_with_text_and_tool(self):
        """Create transcript with mixed text and tool_use content."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Line 1: Session start
            f.write(
                json.dumps({"type": "session_start", "session_id": "test-123"}) + "\n"
            )

            # Line 2: User message
            f.write(
                json.dumps(
                    {
                        "type": "user",
                        "message": {"role": "user", "content": "Read the file"},
                    }
                )
                + "\n"
            )

            # Line 3: Assistant message with TEXT and TOOL_USE
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": "Let me check the file..."},
                                {
                                    "type": "tool_use",
                                    "id": "toolu_123",
                                    "name": "Read",
                                    "input": {"file_path": "/test.py"},
                                },
                            ],
                        },
                        "timestamp": "2024-01-01T12:00:00Z",
                        "uuid": "msg-uuid-123",
                    }
                )
                + "\n"
            )

            # Line 4: Tool result (not extracted in this iteration)
            f.write(
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_123",
                                    "content": "file contents here",
                                }
                            ],
                        },
                    }
                )
                + "\n"
            )

            # Line 5: Assistant text response
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "The file contains important data.",
                                }
                            ],
                        },
                        "timestamp": "2024-01-01T12:00:01Z",
                        "uuid": "msg-uuid-456",
                    }
                )
                + "\n"
            )

            transcript_path = f.name

        yield transcript_path
        Path(transcript_path).unlink()

    def test_extract_text_content_from_assistant_message(
        self, transcript_with_text_and_tool
    ):
        """
        Test extracting text blocks from assistant messages.

        Text blocks should be captured for creating text spans.
        """
        content_blocks = extract_content_blocks(
            transcript_path=transcript_with_text_and_tool, start_line=0
        )

        # Should find 2 text blocks (line 3 and line 5)
        text_blocks = [b for b in content_blocks if b["content_type"] == "text"]
        assert len(text_blocks) == 2

        # First text block
        assert text_blocks[0]["text"] == "Let me check the file..."
        assert text_blocks[0]["line_number"] == 3
        assert text_blocks[0]["timestamp"] == "2024-01-01T12:00:00Z"
        assert text_blocks[0]["message_uuid"] == "msg-uuid-123"

        # Second text block
        assert text_blocks[1]["text"] == "The file contains important data."
        assert text_blocks[1]["line_number"] == 5

    def test_extract_tool_use_from_assistant_message(
        self, transcript_with_text_and_tool
    ):
        """
        Test extracting tool_use blocks from assistant messages.

        Tool_use blocks should be captured for creating tool spans.
        """
        content_blocks = extract_content_blocks(
            transcript_path=transcript_with_text_and_tool, start_line=0
        )

        # Should find 1 tool_use block (line 3)
        tool_blocks = [b for b in content_blocks if b["content_type"] == "tool_use"]
        assert len(tool_blocks) == 1

        # Tool use details
        assert tool_blocks[0]["tool_name"] == "Read"
        assert tool_blocks[0]["tool_id"] == "toolu_123"
        assert tool_blocks[0]["tool_input"] == {"file_path": "/test.py"}
        assert tool_blocks[0]["line_number"] == 3
        assert tool_blocks[0]["timestamp"] == "2024-01-01T12:00:00Z"

    def test_extract_mixed_content_text_and_tool(self, transcript_with_text_and_tool):
        """
        Test extracting mixed content (text + tool) from single message.

        A single assistant message can have both text and tool_use.
        Both should be extracted in order.
        """
        content_blocks = extract_content_blocks(
            transcript_path=transcript_with_text_and_tool, start_line=0
        )

        # Should find 3 blocks total: 2 text + 1 tool_use
        assert len(content_blocks) == 3

        # Order should be preserved (line 3: text, tool_use; line 5: text)
        assert content_blocks[0]["content_type"] == "text"
        assert content_blocks[0]["line_number"] == 3
        assert content_blocks[0]["position_in_message"] == 0

        assert content_blocks[1]["content_type"] == "tool_use"
        assert content_blocks[1]["line_number"] == 3
        assert content_blocks[1]["position_in_message"] == 1

        assert content_blocks[2]["content_type"] == "text"
        assert content_blocks[2]["line_number"] == 5
        assert content_blocks[2]["position_in_message"] == 0

    def test_incremental_extraction_respects_start_line(
        self, transcript_with_text_and_tool
    ):
        """
        Test that extraction respects start_line parameter.

        Only content from lines AFTER start_line should be extracted.
        """
        # Extract from line 3 onwards (skip first 3 lines)
        content_blocks = extract_content_blocks(
            transcript_path=transcript_with_text_and_tool, start_line=3
        )

        # Should only get content from lines 4 and 5
        # Line 4 is user message (skipped)
        # Line 5 has text
        text_blocks = [b for b in content_blocks if b["content_type"] == "text"]
        assert len(text_blocks) == 1
        assert text_blocks[0]["line_number"] == 5
        assert text_blocks[0]["text"] == "The file contains important data."

    def test_no_duplicate_content_on_repeated_calls(
        self, transcript_with_text_and_tool
    ):
        """
        Test that repeated calls with updated start_line don't return duplicates.

        Simulates incremental pushing scenario.
        """
        # First call: extract all
        extract_content_blocks(
            transcript_path=transcript_with_text_and_tool, start_line=0
        )

        # Second call: extract from line 3 (should get only line 5 content)
        blocks_2 = extract_content_blocks(
            transcript_path=transcript_with_text_and_tool, start_line=3
        )

        # No overlap - blocks_2 should not contain line 3 content
        line_3_content = [b for b in blocks_2 if b["line_number"] == 3]
        assert len(line_3_content) == 0

        # Only line 5 content in blocks_2
        assert all(b["line_number"] >= 4 for b in blocks_2)

    def test_extract_empty_content_array(self):
        """Test extraction handles messages with empty content arrays."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"role": "assistant", "content": []},
                    }
                )
                + "\n"
            )
            transcript_path = f.name

        try:
            content_blocks = extract_content_blocks(transcript_path, start_line=0)

            # Should return empty list (no content to extract)
            assert len(content_blocks) == 0
        finally:
            Path(transcript_path).unlink()

    def test_extract_skips_non_assistant_messages(self):
        """
        Test that extraction only processes assistant messages.

        User and system messages are skipped (only assistant content creates spans).
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # User message (should be skipped)
            f.write(
                json.dumps(
                    {"type": "user", "message": {"role": "user", "content": "Hello"}}
                )
                + "\n"
            )

            # System message (should be skipped)
            f.write(
                json.dumps(
                    {
                        "type": "system",
                        "message": {"role": "system", "content": "Instructions"},
                    }
                )
                + "\n"
            )

            # Assistant message (should be processed)
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Response"}],
                        },
                        "timestamp": "2024-01-01T12:00:00Z",
                        "uuid": "msg-123",
                    }
                )
                + "\n"
            )

            transcript_path = f.name

        try:
            content_blocks = extract_content_blocks(transcript_path, start_line=0)

            # Should only extract from assistant message
            assert len(content_blocks) == 1
            assert content_blocks[0]["content_type"] == "text"
            assert content_blocks[0]["text"] == "Response"
        finally:
            Path(transcript_path).unlink()

    def test_extract_handles_malformed_json_gracefully(self):
        """Test that extraction skips malformed JSON lines."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Valid line
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "First"}],
                        },
                        "timestamp": "2024-01-01T12:00:00Z",
                        "uuid": "msg-1",
                    }
                )
                + "\n"
            )

            # Malformed JSON (should be skipped)
            f.write("{invalid json\n")

            # Valid line
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Second"}],
                        },
                        "timestamp": "2024-01-01T12:00:01Z",
                        "uuid": "msg-2",
                    }
                )
                + "\n"
            )

            transcript_path = f.name

        try:
            content_blocks = extract_content_blocks(transcript_path, start_line=0)

            # Should extract 2 valid blocks (skip malformed line)
            assert len(content_blocks) == 2
            assert content_blocks[0]["text"] == "First"
            assert content_blocks[1]["text"] == "Second"
        finally:
            Path(transcript_path).unlink()

    def test_extract_returns_line_number_for_state_tracking(self):
        """
        Test that extracted blocks include line_number for state tracking.

        Line numbers are used to update last_pushed_line in state.
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "session_start"}) + "\n")  # Line 1
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Response"}],
                        },
                        "timestamp": "2024-01-01T12:00:00Z",
                        "uuid": "msg-1",
                    }
                )
                + "\n"
            )  # Line 2
            transcript_path = f.name

        try:
            content_blocks = extract_content_blocks(transcript_path, start_line=0)

            # Block should have line_number = 2
            assert len(content_blocks) == 1
            assert content_blocks[0]["line_number"] == 2
        finally:
            Path(transcript_path).unlink()
