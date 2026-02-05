#!/usr/bin/env python3
"""
Tests for Langfuse incremental push functionality.

Tests AC1, AC2, AC4: Incremental collection and trace updates
- Parse only new lines (unpushed) from transcript
- Update existing trace with new spans
- Accumulate token usage across pushes
- Single trace per session (not multiple traces)
"""

import json
import tempfile
from pathlib import Path

import pytest

from pacemaker.langfuse.incremental import (
    parse_incremental_lines,
    create_or_update_trace,
)


class TestIncrementalParsing:
    """Test incremental parsing of transcript lines."""

    @pytest.fixture
    def transcript_file(self):
        """Create temporary transcript file matching actual Claude Code format."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Write 5 lines of transcript data in actual Claude Code transcript format
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
            # Token usage is nested inside message
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
            # Tool calls are in message.content array
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

    def test_parse_all_lines_from_start(self, transcript_file):
        """
        Test parsing all lines when last_pushed_line=0.

        AC1: Parse only new lines (unpushed) from transcript
        """
        result = parse_incremental_lines(transcript_file, last_pushed_line=0)

        # Should parse all 5 lines
        assert result["lines_parsed"] == 5
        assert result["last_line"] == 5

        # Should extract token usage
        assert result["token_usage"]["input_tokens"] == 25  # 10 + 15
        assert result["token_usage"]["output_tokens"] == 45  # 20 + 25

        # Should extract tool calls
        assert "Read" in result["tool_calls"]

    def test_parse_only_new_lines(self, transcript_file):
        """
        Test parsing only lines after last_pushed_line.

        AC1: Parse only new lines (unpushed) from transcript
        """
        # Simulate previous push stopped at line 3
        result = parse_incremental_lines(transcript_file, last_pushed_line=3)

        # Should parse only lines 4 and 5
        assert result["lines_parsed"] == 2
        assert result["last_line"] == 5

        # Should extract only new token usage (line 5)
        assert result["token_usage"]["input_tokens"] == 15
        assert result["token_usage"]["output_tokens"] == 25

    def test_parse_zero_new_lines(self, transcript_file):
        """Test parsing when already at end of file."""
        result = parse_incremental_lines(transcript_file, last_pushed_line=5)

        # Should parse 0 new lines
        assert result["lines_parsed"] == 0
        assert result["last_line"] == 5
        assert result["token_usage"]["input_tokens"] == 0
        assert result["token_usage"]["output_tokens"] == 0

    def test_parse_with_cache_tokens(self):
        """Test parsing extracts cache_read_tokens from nested message.usage."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Token usage is nested inside message (actual Claude Code format)
            f.write(
                json.dumps(
                    {
                        "message": {
                            "role": "assistant",
                            "usage": {
                                "input_tokens": 100,
                                "output_tokens": 50,
                                "cache_read_input_tokens": 30,
                            },
                        }
                    }
                )
                + "\n"
            )
            transcript_path = f.name

        try:
            result = parse_incremental_lines(transcript_path, last_pushed_line=0)

            assert result["token_usage"]["input_tokens"] == 100
            assert result["token_usage"]["output_tokens"] == 50
            assert result["token_usage"]["cache_read_tokens"] == 30

        finally:
            Path(transcript_path).unlink()

    def test_parse_empty_transcript(self):
        """Test parsing empty transcript file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            transcript_path = f.name

        try:
            result = parse_incremental_lines(transcript_path, last_pushed_line=0)

            assert result["lines_parsed"] == 0
            assert result["last_line"] == 0
            assert result["token_usage"]["input_tokens"] == 0

        finally:
            Path(transcript_path).unlink()


class TestTraceCreationAndUpdate:
    """Test creating and updating Langfuse traces."""

    def test_create_new_trace_first_push(self):
        """
        Test creating new trace for first push.

        AC4: Single trace per session
        """
        session_id = "test-session-abc"
        incremental_data = {
            "token_usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_tokens": 10,
            },
            "tool_calls": ["Read", "Write"],
            "lines_parsed": 10,
            "last_line": 10,
        }

        trace = create_or_update_trace(
            session_id=session_id,
            model="claude-sonnet-4-5",
            user_id="test@example.com",
            incremental_data=incremental_data,
            existing_trace=None,  # First push
        )

        # Should create new trace with session_id as trace_id
        assert trace["id"] == session_id
        assert trace["name"] == f"claude-code-session-{session_id[:8]}"
        assert trace["userId"] == "test@example.com"
        assert trace["metadata"]["model"] == "claude-sonnet-4-5"

        # Should have correct token usage in metadata
        assert trace["metadata"]["input_tokens"] == 100
        assert trace["metadata"]["output_tokens"] == 50
        assert trace["metadata"]["cache_read_tokens"] == 10

        # Should have tool calls
        assert trace["metadata"]["tool_calls"] == ["Read", "Write"]

    def test_update_existing_trace_incremental_push(self):
        """
        Test updating existing trace with new data.

        AC4: Spans are appended chronologically, token usage accumulates
        """
        session_id = "test-session-def"

        # Existing trace from previous push
        existing_trace = {
            "id": session_id,
            "name": f"claude-code-session-{session_id[:8]}",
            "userId": "test@example.com",
            "metadata": {
                "model": "claude-sonnet-4-5",
                "tool_calls": ["Read"],
                "tool_count": 1,
                "input_tokens": 50,
                "output_tokens": 25,
                "cache_read_tokens": 5,
            },
        }

        # New incremental data from second push
        incremental_data = {
            "token_usage": {
                "input_tokens": 30,
                "output_tokens": 15,
                "cache_read_tokens": 3,
            },
            "tool_calls": ["Write", "Bash"],
            "lines_parsed": 5,
            "last_line": 15,
        }

        trace = create_or_update_trace(
            session_id=session_id,
            model="claude-sonnet-4-5",
            user_id="test@example.com",
            incremental_data=incremental_data,
            existing_trace=existing_trace,
        )

        # Should keep same trace_id
        assert trace["id"] == session_id

        # Should accumulate token usage in metadata
        assert trace["metadata"]["input_tokens"] == 80  # 50 + 30
        assert trace["metadata"]["output_tokens"] == 40  # 25 + 15
        assert trace["metadata"]["cache_read_tokens"] == 8  # 5 + 3

        # Should append new tool calls
        assert trace["metadata"]["tool_calls"] == ["Read", "Write", "Bash"]
        assert trace["metadata"]["tool_count"] == 3

    def test_multiple_incremental_pushes_accumulate(self):
        """
        Test 5 incremental pushes create single trace with accumulated data.

        AC4: Single trace with 5 incremental pushes accumulates correctly
        """
        session_id = "test-session-multi"
        trace = None

        # Simulate 5 incremental pushes
        for i in range(5):
            incremental_data = {
                "token_usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_tokens": 1,
                },
                "tool_calls": [f"Tool{i}"],
                "lines_parsed": 2,
                "last_line": (i + 1) * 2,
            }

            trace = create_or_update_trace(
                session_id=session_id,
                model="claude-sonnet-4-5",
                user_id="test@example.com",
                incremental_data=incremental_data,
                existing_trace=trace,  # Pass previous trace
            )

        # After 5 pushes, should have single trace with accumulated data
        assert trace["id"] == session_id

        # Token usage should accumulate in metadata (5 pushes × 10 input, 5 output, 1 cache)
        assert trace["metadata"]["input_tokens"] == 50
        assert trace["metadata"]["output_tokens"] == 25
        assert trace["metadata"]["cache_read_tokens"] == 5

        # Tool calls should accumulate
        assert len(trace["metadata"]["tool_calls"]) == 5
        assert trace["metadata"]["tool_calls"] == [
            "Tool0",
            "Tool1",
            "Tool2",
            "Tool3",
            "Tool4",
        ]

    def test_update_trace_with_no_new_tokens(self):
        """Test updating trace when incremental push has no new token usage."""
        session_id = "test-session-notoken"

        existing_trace = {
            "id": session_id,
            "metadata": {
                "tool_calls": ["Read"],
                "tool_count": 1,
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_tokens": 0,
            },
        }

        incremental_data = {
            "token_usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
            },
            "tool_calls": [],
            "lines_parsed": 0,
            "last_line": 10,
        }

        trace = create_or_update_trace(
            session_id=session_id,
            model="claude-sonnet-4-5",
            user_id="test@example.com",
            incremental_data=incremental_data,
            existing_trace=existing_trace,
        )

        # Should preserve existing token usage in metadata
        assert trace["metadata"]["input_tokens"] == 100
        assert trace["metadata"]["output_tokens"] == 50
        assert trace["metadata"]["cache_read_tokens"] == 0

        # Should preserve existing tool calls
        assert trace["metadata"]["tool_calls"] == ["Read"]

    def test_trace_includes_session_id(self):
        """
        Test that traces include sessionId field for Langfuse session linking.

        Langfuse hierarchy: Session (auto-created) → Trace (has sessionId)
        """
        session_id = "test-session-with-link"
        incremental_data = {
            "token_usage": {
                "input_tokens": 50,
                "output_tokens": 25,
                "cache_read_tokens": 5,
            },
            "tool_calls": ["Read"],
            "lines_parsed": 5,
            "last_line": 5,
        }

        trace = create_or_update_trace(
            session_id=session_id,
            model="claude-sonnet-4-5",
            user_id="test@example.com",
            incremental_data=incremental_data,
            existing_trace=None,
        )

        # CRITICAL: trace must have sessionId field to link to Langfuse session
        assert "sessionId" in trace
        assert trace["sessionId"] == session_id


class TestGenerationCreation:
    """Test generation observation creation for token tracking."""

    def test_create_generation_first_push(self):
        """
        Test creating generation observation for first push.

        Langfuse expects token usage on generation observations, not trace level.
        Generation is observation within trace.
        """
        from pacemaker.langfuse.incremental import create_generation

        session_id = "test-session-gen"
        incremental_data = {
            "token_usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_tokens": 10,
            },
            "tool_calls": ["Read", "Write"],
            "lines_parsed": 10,
            "last_line": 10,
        }

        generation = create_generation(
            trace_id=session_id,  # In old architecture, session_id was used as trace_id
            model="claude-sonnet-4-5",
            incremental_data=incremental_data,
        )

        # Generation must be linked to trace via traceId
        assert generation["traceId"] == session_id
        assert generation["type"] == "generation"
        assert generation["name"] == "claude-code-generation"

        # Token usage on generation (Langfuse requirement)
        assert "usage" in generation
        assert generation["usage"]["input"] == 100
        assert generation["usage"]["output"] == 50
        assert generation["usage"]["total"] == 150

        # Cache tokens if present
        if incremental_data["token_usage"]["cache_read_tokens"] > 0:
            assert generation["usage"]["cache_read"] == 10

        # Model on generation
        assert generation["model"] == "claude-sonnet-4-5"

    def test_update_generation_incremental_push(self):
        """
        Test updating generation with accumulated token usage.

        Each incremental push updates generation with total accumulated tokens.
        """
        from pacemaker.langfuse.incremental import create_generation

        session_id = "test-session-gen-update"

        # First push
        incremental_data_1 = {
            "token_usage": {
                "input_tokens": 50,
                "output_tokens": 25,
                "cache_read_tokens": 5,
            },
            "tool_calls": ["Read"],
            "lines_parsed": 5,
            "last_line": 5,
        }

        gen1 = create_generation(
            trace_id=session_id,  # In old architecture, session_id was used as trace_id
            model="claude-sonnet-4-5",
            incremental_data=incremental_data_1,
        )

        assert gen1["usage"]["input"] == 50
        assert gen1["usage"]["output"] == 25
        assert gen1["usage"]["total"] == 75

        # Second push - accumulated tokens
        accumulated_tokens = {
            "input_tokens": 80,  # 50 + 30
            "output_tokens": 40,  # 25 + 15
            "cache_read_tokens": 8,  # 5 + 3
        }
        incremental_data_2 = {
            "token_usage": accumulated_tokens,
            "tool_calls": ["Write"],
            "lines_parsed": 5,
            "last_line": 10,
        }

        gen2 = create_generation(
            trace_id=session_id,  # In old architecture, session_id was used as trace_id
            model="claude-sonnet-4-5",
            incremental_data=incremental_data_2,
        )

        # Generation should have accumulated totals
        assert gen2["usage"]["input"] == 80
        assert gen2["usage"]["output"] == 40
        assert gen2["usage"]["total"] == 120
        assert gen2["usage"]["cache_read"] == 8

    def test_generation_has_unique_id(self):
        """Test that generation has unique ID separate from trace."""
        from pacemaker.langfuse.incremental import create_generation

        session_id = "test-session-unique"
        incremental_data = {
            "token_usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_tokens": 0,
            },
            "tool_calls": [],
            "lines_parsed": 1,
            "last_line": 1,
        }

        generation = create_generation(
            trace_id=session_id,  # In old architecture, session_id was used as trace_id
            model="claude-sonnet-4-5",
            incremental_data=incremental_data,
        )

        # Generation must have its own ID (not same as trace_id)
        assert "id" in generation
        assert generation["id"] != session_id  # Different from trace_id
        # But must link to trace
        assert generation["traceId"] == session_id


class TestBatchEventStructure:
    """Test batch event structure for Langfuse ingestion API."""

    def test_create_batch_event_first_push(self):
        """
        Test creating batch event with trace + generation for first push.

        Langfuse ingestion API expects batch array: [trace, generation]
        """
        from pacemaker.langfuse.incremental import create_batch_event

        session_id = "test-session-batch"
        incremental_data = {
            "token_usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_tokens": 10,
            },
            "tool_calls": ["Read"],
            "lines_parsed": 5,
            "last_line": 5,
        }

        batch = create_batch_event(
            session_id=session_id,
            model="claude-sonnet-4-5",
            user_id="test@example.com",
            incremental_data=incremental_data,
            existing_trace=None,
        )

        # Batch should be array of events
        assert isinstance(batch, list)
        assert len(batch) == 2  # trace + generation

        # First event: trace
        trace = batch[0]
        assert trace["type"] == "trace-create"
        assert trace["body"]["id"] == session_id
        assert trace["body"]["sessionId"] == session_id

        # Second event: generation
        generation = batch[1]
        assert generation["type"] == "generation-create"
        assert generation["body"]["traceId"] == session_id
        assert "usage" in generation["body"]
        assert generation["body"]["usage"]["input"] == 100
        assert generation["body"]["usage"]["output"] == 50

    def test_create_batch_event_incremental_push(self):
        """
        Test creating batch event for incremental push (update existing).

        Incremental push uses trace-update and generation-update events.
        """
        from pacemaker.langfuse.incremental import create_batch_event

        session_id = "test-session-batch-update"

        existing_trace = {
            "id": session_id,
            "sessionId": session_id,
            "metadata": {"tool_calls": ["Read"], "tool_count": 1},
        }

        incremental_data = {
            "token_usage": {
                "input_tokens": 30,
                "output_tokens": 15,
                "cache_read_tokens": 3,
            },
            "tool_calls": ["Write"],
            "lines_parsed": 3,
            "last_line": 8,
        }

        batch = create_batch_event(
            session_id=session_id,
            model="claude-sonnet-4-5",
            user_id="test@example.com",
            incremental_data=incremental_data,
            existing_trace=existing_trace,
        )

        # Batch should have update events
        assert isinstance(batch, list)
        assert len(batch) == 2

        # First event: trace update
        trace_update = batch[0]
        assert trace_update["type"] == "trace-update"
        assert trace_update["body"]["id"] == session_id

        # Second event: generation update
        gen_update = batch[1]
        assert gen_update["type"] == "generation-update"
        assert gen_update["body"]["traceId"] == session_id
