#!/usr/bin/env python3
"""
Tests for extracting tool outputs and attaching to tool use blocks.

Tests the two-pass extraction logic:
1. First pass: Extract all tool_result blocks and build tool_use_id -> output mapping
2. Second pass: Extract tool_use blocks and attach matching outputs
"""

import json
import tempfile
from pathlib import Path

import pytest

from pacemaker.langfuse.incremental import extract_content_blocks


class TestToolOutputExtraction:
    """Test extracting tool outputs and matching to tool use blocks."""

    @pytest.fixture
    def transcript_with_tool_output(self):
        """Create transcript with tool_use and matching tool_result."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Assistant message with tool_use
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2024-01-01T00:00:00Z",
                        "uuid": "msg-1",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "toolu_123",
                                    "name": "Bash",
                                    "input": {"command": "ls -la"},
                                }
                            ],
                        },
                    }
                )
                + "\n"
            )
            # User message with tool_result
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
                                    "content": "file1.txt\nfile2.txt\n",
                                }
                            ],
                        },
                    }
                )
                + "\n"
            )
            transcript_path = f.name

        yield transcript_path
        Path(transcript_path).unlink()

    def test_extract_tool_output_from_result(self, transcript_with_tool_output):
        """Test that tool_result content is extracted and matched to tool_use."""
        blocks = extract_content_blocks(transcript_with_tool_output, start_line=0)

        # Should extract one tool_use block
        tool_blocks = [b for b in blocks if b["content_type"] == "tool_use"]
        assert len(tool_blocks) == 1

        # Tool block should have output attached
        tool_block = tool_blocks[0]
        assert tool_block["tool_name"] == "Bash"
        assert tool_block["tool_id"] == "toolu_123"
        assert "tool_output" in tool_block
        assert tool_block["tool_output"] == "file1.txt\nfile2.txt\n"

    def test_extract_multiple_tools_with_outputs(self):
        """Test extracting multiple tool calls with matching outputs."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Assistant with two tool calls
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2024-01-01T00:00:00Z",
                        "uuid": "msg-1",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "toolu_read",
                                    "name": "Read",
                                    "input": {"file_path": "/test.py"},
                                },
                                {
                                    "type": "tool_use",
                                    "id": "toolu_bash",
                                    "name": "Bash",
                                    "input": {"command": "pwd"},
                                },
                            ],
                        },
                    }
                )
                + "\n"
            )
            # User with two tool results
            f.write(
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_read",
                                    "content": "def hello():\n    pass\n",
                                },
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_bash",
                                    "content": "/home/user/project\n",
                                },
                            ],
                        },
                    }
                )
                + "\n"
            )
            transcript_path = f.name

        try:
            blocks = extract_content_blocks(transcript_path, start_line=0)
            tool_blocks = [b for b in blocks if b["content_type"] == "tool_use"]

            # Should extract both tools with correct outputs
            assert len(tool_blocks) == 2

            read_block = [b for b in tool_blocks if b["tool_name"] == "Read"][0]
            assert read_block["tool_output"] == "def hello():\n    pass\n"

            bash_block = [b for b in tool_blocks if b["tool_name"] == "Bash"][0]
            assert bash_block["tool_output"] == "/home/user/project\n"

        finally:
            Path(transcript_path).unlink()

    def test_tool_without_result_has_empty_output(self):
        """Test that tool_use without matching result gets empty string output."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Assistant with tool_use but no corresponding tool_result
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2024-01-01T00:00:00Z",
                        "uuid": "msg-1",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "toolu_orphan",
                                    "name": "Read",
                                    "input": {"file_path": "/test.py"},
                                }
                            ],
                        },
                    }
                )
                + "\n"
            )
            transcript_path = f.name

        try:
            blocks = extract_content_blocks(transcript_path, start_line=0)
            tool_blocks = [b for b in blocks if b["content_type"] == "tool_use"]

            assert len(tool_blocks) == 1
            # Should have empty output (graceful handling)
            assert tool_blocks[0]["tool_output"] == ""

        finally:
            Path(transcript_path).unlink()

    def test_tool_result_with_array_content(self):
        """Test tool_result with array content format (e.g., [{"type": "text", "text": "..."}])."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Assistant with tool_use
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2024-01-01T00:00:00Z",
                        "uuid": "msg-1",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "toolu_array",
                                    "name": "Bash",
                                    "input": {"command": "echo test"},
                                }
                            ],
                        },
                    }
                )
                + "\n"
            )
            # User with array-format tool_result
            f.write(
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_array",
                                    "content": [
                                        {"type": "text", "text": "test\n"},
                                        {"type": "text", "text": "second line\n"},
                                    ],
                                }
                            ],
                        },
                    }
                )
                + "\n"
            )
            transcript_path = f.name

        try:
            blocks = extract_content_blocks(transcript_path, start_line=0)
            tool_blocks = [b for b in blocks if b["content_type"] == "tool_use"]

            assert len(tool_blocks) == 1
            # Should concatenate array content
            assert tool_blocks[0]["tool_output"] == "test\nsecond line\n"

        finally:
            Path(transcript_path).unlink()

    def test_incremental_extraction_with_start_line(self):
        """Test that incremental extraction (start_line > 0) includes matching outputs."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Line 1: Old tool call (should be skipped)
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2024-01-01T00:00:00Z",
                        "uuid": "msg-old",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "toolu_old",
                                    "name": "Old",
                                    "input": {},
                                }
                            ],
                        },
                    }
                )
                + "\n"
            )
            # Line 2: Result for old tool (but we need it for matching!)
            f.write(
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_old",
                                    "content": "old output",
                                }
                            ],
                        },
                    }
                )
                + "\n"
            )
            # Line 3: New tool call (should be extracted)
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2024-01-01T00:01:00Z",
                        "uuid": "msg-new",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "toolu_new",
                                    "name": "New",
                                    "input": {},
                                }
                            ],
                        },
                    }
                )
                + "\n"
            )
            # Line 4: Result for new tool
            f.write(
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_new",
                                    "content": "new output",
                                }
                            ],
                        },
                    }
                )
                + "\n"
            )
            transcript_path = f.name

        try:
            # Extract incrementally starting from line 2
            blocks = extract_content_blocks(transcript_path, start_line=2)
            tool_blocks = [b for b in blocks if b["content_type"] == "tool_use"]

            # Should only extract the new tool (line 3)
            assert len(tool_blocks) == 1
            assert tool_blocks[0]["tool_name"] == "New"
            # CRITICAL: Should have output even though result is after start_line
            # The two-pass algorithm must scan ALL lines for results
            assert tool_blocks[0]["tool_output"] == "new output"

        finally:
            Path(transcript_path).unlink()


class TestSpanCreationWithOutput:
    """Test that spans are created with tool outputs from blocks."""

    def test_span_created_with_output(self):
        """Test _create_spans_from_blocks uses tool_output from block."""
        from pacemaker.langfuse.orchestrator import _create_spans_from_blocks
        from datetime import datetime, timezone

        # Content block with tool_output attached
        content_blocks = [
            {
                "content_type": "tool_use",
                "line_number": 1,
                "tool_name": "Bash",
                "tool_id": "toolu_123",
                "tool_input": {"command": "ls"},
                "tool_output": "file1.txt\nfile2.txt\n",
            }
        ]

        trace_id = "test-trace-123"
        timestamp = datetime.now(timezone.utc)

        batch = _create_spans_from_blocks(content_blocks, trace_id, timestamp)

        # Should create one span with output
        assert len(batch) == 1
        span_event = batch[0]

        assert span_event["type"] == "span-create"
        span_body = span_event["body"]

        # CRITICAL: Span should have output from block
        assert span_body["output"] == "file1.txt\nfile2.txt\n"
        assert span_body["name"] == "Tool - Bash"
        assert span_body["input"] == {"command": "ls"}

    def test_span_with_empty_output_when_missing(self):
        """Test span gets empty output when tool_output not in block."""
        from pacemaker.langfuse.orchestrator import _create_spans_from_blocks
        from datetime import datetime, timezone

        # Content block WITHOUT tool_output
        content_blocks = [
            {
                "content_type": "tool_use",
                "line_number": 1,
                "tool_name": "Read",
                "tool_id": "toolu_456",
                "tool_input": {"file_path": "/test.py"},
                # No tool_output field
            }
        ]

        trace_id = "test-trace-456"
        timestamp = datetime.now(timezone.utc)

        batch = _create_spans_from_blocks(content_blocks, trace_id, timestamp)

        # Should create span with empty output (graceful handling)
        assert len(batch) == 1
        span_body = batch[0]["body"]
        assert span_body["output"] == ""
