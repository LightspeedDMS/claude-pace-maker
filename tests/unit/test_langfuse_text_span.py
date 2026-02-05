#!/usr/bin/env python3
"""
Tests for creating Langfuse text spans for assistant text output.

Text spans are separate from tool spans - they represent Claude's text responses
between tool calls. This allows Langfuse to show the full conversation flow.
"""

from datetime import datetime, timezone

from pacemaker.langfuse.span import create_text_span


class TestTextSpanCreation:
    """Test creating text spans for assistant text output."""

    def test_create_text_span_has_correct_structure(self):
        """
        Test that text span has required Langfuse API structure.

        Text spans must have: id, traceId, name, startTime, endTime, output
        """
        trace_id = "test-session-abc-turn-1"
        text_content = "Let me check that file for you..."
        start_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        end_time = datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc)
        line_number = 42

        span = create_text_span(
            trace_id=trace_id,
            text=text_content,
            start_time=start_time,
            end_time=end_time,
            line_number=line_number,
        )

        # Span must link to trace
        assert span["traceId"] == trace_id

        # Span must have descriptive name
        assert span["name"] == "Assistant Response"

        # Span must have text in output field
        assert span["output"] == text_content

        # Span must have timing
        assert span["startTime"] == start_time.isoformat()
        assert span["endTime"] == end_time.isoformat()

        # Span must have unique ID
        assert "id" in span
        assert span["id"] != trace_id  # Different from trace ID

    def test_create_text_span_includes_output_field(self):
        """
        Test that text span includes output field with text content.

        Langfuse uses 'output' field for text responses.
        """
        trace_id = "test-trace-123"
        text_content = "The file contains important configuration data."
        now = datetime.now(timezone.utc)

        span = create_text_span(
            trace_id=trace_id,
            text=text_content,
            start_time=now,
            end_time=now,
            line_number=10,
        )

        # Output field must contain the text
        assert "output" in span
        assert span["output"] == text_content

    def test_create_text_span_has_unique_id(self):
        """
        Test that text spans have unique IDs.

        Each text response creates a separate span with unique ID.
        """
        trace_id = "test-trace-456"
        now = datetime.now(timezone.utc)

        # Create two text spans for same trace
        span1 = create_text_span(
            trace_id=trace_id,
            text="First response",
            start_time=now,
            end_time=now,
            line_number=5,
        )

        span2 = create_text_span(
            trace_id=trace_id,
            text="Second response",
            start_time=now,
            end_time=now,
            line_number=10,
        )

        # Both link to same trace
        assert span1["traceId"] == trace_id
        assert span2["traceId"] == trace_id

        # But have different IDs
        assert span1["id"] != span2["id"]

    def test_create_text_span_includes_line_number_in_id(self):
        """
        Test that span ID includes line number for uniqueness.

        Line numbers help create deterministic span IDs.
        """
        trace_id = "test-trace-789"
        now = datetime.now(timezone.utc)
        line_number = 42

        span = create_text_span(
            trace_id=trace_id,
            text="Response at line 42",
            start_time=now,
            end_time=now,
            line_number=line_number,
        )

        # Span ID should incorporate line number
        # Format: {trace_id}-text-{line_number}-{uuid[:8]}
        assert f"-text-{line_number}-" in span["id"]

    def test_create_text_span_handles_empty_text(self):
        """
        Test that text span handles empty text gracefully.

        Empty text blocks should still create valid spans.
        """
        trace_id = "test-trace-empty"
        now = datetime.now(timezone.utc)

        span = create_text_span(
            trace_id=trace_id,
            text="",
            start_time=now,
            end_time=now,
            line_number=1,
        )

        # Should create valid span with empty output
        assert span["traceId"] == trace_id
        assert span["output"] == ""
        assert span["name"] == "Assistant Response"

    def test_create_text_span_handles_multiline_text(self):
        """
        Test that text span handles multiline text.

        Assistant responses often span multiple lines.
        """
        trace_id = "test-trace-multiline"
        now = datetime.now(timezone.utc)
        multiline_text = """Let me explain:
1. First point
2. Second point
3. Third point"""

        span = create_text_span(
            trace_id=trace_id,
            text=multiline_text,
            start_time=now,
            end_time=now,
            line_number=20,
        )

        # Should preserve multiline text in output
        assert span["output"] == multiline_text
        assert "\n" in span["output"]

    def test_create_text_span_has_metadata(self):
        """
        Test that text span includes metadata field.

        Metadata helps distinguish text spans from tool spans.
        """
        trace_id = "test-trace-meta"
        now = datetime.now(timezone.utc)

        span = create_text_span(
            trace_id=trace_id,
            text="Response",
            start_time=now,
            end_time=now,
            line_number=5,
        )

        # Should have metadata marking this as text span
        assert "metadata" in span
        assert span["metadata"]["type"] == "text"

    def test_create_multiple_text_spans_for_trace(self):
        """
        Test creating multiple text spans for same trace.

        A conversation turn can have multiple text responses
        interspersed with tool calls.
        """
        trace_id = "test-trace-multi"
        now = datetime.now(timezone.utc)

        # Create 3 text spans for same trace
        spans = []
        for i in range(3):
            span = create_text_span(
                trace_id=trace_id,
                text=f"Response {i+1}",
                start_time=now,
                end_time=now,
                line_number=10 + i,
            )
            spans.append(span)

        # All link to same trace
        assert all(s["traceId"] == trace_id for s in spans)

        # Each has unique ID
        span_ids = [s["id"] for s in spans]
        assert len(span_ids) == len(set(span_ids))  # All unique

        # Each has correct output
        assert spans[0]["output"] == "Response 1"
        assert spans[1]["output"] == "Response 2"
        assert spans[2]["output"] == "Response 3"
