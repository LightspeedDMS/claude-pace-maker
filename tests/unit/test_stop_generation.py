#!/usr/bin/env python3
"""
Tests for generation observation creation in handle_stop_finalize.

Verifies that Stop hook creates generation-create events with proper token
usage so Langfuse can compute totalCost correctly.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from pacemaker.langfuse.state import StateManager
from pacemaker.langfuse.orchestrator import handle_stop_finalize


class TestStopGenerationObservation:
    """Test generation observation creation in Stop hook."""

    def setup_method(self):
        """Set up test fixtures."""
        self.state_dir = tempfile.mkdtemp()
        self.transcript_dir = tempfile.mkdtemp()
        self.config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://test.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
            "db_path": ":memory:",
        }

    def teardown_method(self):
        """Clean up."""
        import shutil

        shutil.rmtree(self.state_dir, ignore_errors=True)
        shutil.rmtree(self.transcript_dir, ignore_errors=True)

    def _create_transcript(self, messages: list) -> str:
        """Create temporary transcript file."""
        transcript_path = Path(self.transcript_dir) / "transcript.jsonl"
        with open(transcript_path, "w") as f:
            for msg in messages:
                entry = {
                    "type": msg["role"],
                    "message": {"role": msg["role"], "content": msg["content"]},
                }
                f.write(json.dumps(entry) + "\n")
        return str(transcript_path)

    @patch(
        "pacemaker.langfuse.orchestrator.jsonl_parser.parse_session_metadata",
        return_value={"model": "claude-opus-4-6"},
    )
    @patch(
        "pacemaker.langfuse.orchestrator.incremental.parse_incremental_lines",
        return_value={
            "token_usage": {
                "input_tokens": 5000,
                "output_tokens": 2000,
                "cache_read_tokens": 100000,
            },
            "lines_parsed": 10,
            "last_line": 10,
            "tool_calls": [],
        },
    )
    @patch("pacemaker.langfuse.orchestrator.finalize_trace_with_output")
    @patch(
        "pacemaker.langfuse.orchestrator.sanitize_trace",
        side_effect=lambda batch, db_path: batch,
    )
    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    @patch("pacemaker.langfuse.orchestrator.flush_pending_trace")
    def test_stop_finalize_creates_generation_with_tokens(
        self,
        mock_flush_pending,
        mock_push,
        mock_sanitize,
        mock_finalize,
        mock_parse_incremental,
        mock_parse_metadata,
    ):
        """
        Test that handle_stop_finalize creates generation with token usage.

        When transcript has accumulated tokens, Stop should create 2 events:
        1. trace-create (finalized trace)
        2. generation-create (with usage data for cost calculation)
        """
        session_id = "test-gen-with-tokens"
        trace_id = f"{session_id}-trace-abc"

        # Create state WITHOUT token usage (tokens come from transcript parsing)
        state_manager = StateManager(self.state_dir)
        state_manager.create_or_update(
            session_id=session_id,
            trace_id=trace_id,
            last_pushed_line=0,
            metadata={
                "current_trace_id": trace_id,
                "trace_start_line": 0,
            },
        )

        # Mock finalize to return trace body
        mock_finalize.return_value = {
            "id": trace_id,
            "output": "test output",
            "metadata": {},
        }

        # Mock push to capture batch
        captured_batch = None

        def capture_push(base_url, pk, sk, batch, timeout=10):
            nonlocal captured_batch
            captured_batch = batch
            return (True, len(batch))

        mock_push.side_effect = capture_push

        # Create transcript
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "test"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "response"}]},
        ]
        transcript_path = self._create_transcript(messages)

        # Call handle_stop_finalize
        result = handle_stop_finalize(
            config=self.config,
            session_id=session_id,
            transcript_path=transcript_path,
            state_dir=self.state_dir,
        )

        assert result is True
        assert captured_batch is not None
        assert len(captured_batch) == 2, "Should have trace-create + generation-create"

        # Verify trace-create event
        trace_event = captured_batch[0]
        assert trace_event["type"] == "trace-create"
        assert trace_event["body"]["id"] == trace_id

        # Verify generation-create event
        gen_event = captured_batch[1]
        assert gen_event["type"] == "generation-create"
        gen_body = gen_event["body"]

        # Verify generation body structure
        assert gen_body["traceId"] == trace_id
        assert gen_body["name"] == "claude-code-generation"
        assert gen_body["model"] == "claude-opus-4-6"
        assert "startTime" in gen_body
        assert "type" not in gen_body  # Should NOT be in body

        # Verify usage fields
        usage = gen_body["usage"]
        assert usage["input"] == 5000
        assert usage["output"] == 2000
        assert usage["total"] == 107000  # 5000 + 2000 + 100000
        # Cache tokens must be in usageDetails (Langfuse drops them from usage)
        usage_details = gen_body["usageDetails"]
        assert usage_details["cache_read_input_tokens"] == 100000

    @patch(
        "pacemaker.langfuse.orchestrator.incremental.parse_incremental_lines",
        return_value={
            "token_usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
            },
            "lines_parsed": 0,
            "last_line": 0,
            "tool_calls": [],
        },
    )
    @patch("pacemaker.langfuse.orchestrator.finalize_trace_with_output")
    @patch(
        "pacemaker.langfuse.orchestrator.sanitize_trace",
        side_effect=lambda batch, db_path: batch,
    )
    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    @patch("pacemaker.langfuse.orchestrator.flush_pending_trace")
    def test_stop_finalize_skips_generation_when_no_tokens(
        self,
        mock_flush_pending,
        mock_push,
        mock_sanitize,
        mock_finalize,
        mock_parse_incremental,
    ):
        """
        Test that generation is NOT created when no token usage exists.

        If transcript has no tokens or zero tokens, only trace-create should be sent.
        """
        session_id = "test-gen-no-tokens"
        trace_id = f"{session_id}-trace-def"

        # Create state WITHOUT token usage (tokens come from transcript parsing)
        state_manager = StateManager(self.state_dir)
        state_manager.create_or_update(
            session_id=session_id,
            trace_id=trace_id,
            last_pushed_line=0,
            metadata={
                "current_trace_id": trace_id,
                "trace_start_line": 0,
            },
        )

        mock_finalize.return_value = {
            "id": trace_id,
            "output": "test output",
            "metadata": {},
        }

        captured_batch = None

        def capture_push(base_url, pk, sk, batch, timeout=10):
            nonlocal captured_batch
            captured_batch = batch
            return (True, len(batch))

        mock_push.side_effect = capture_push

        messages = [
            {"role": "user", "content": [{"type": "text", "text": "test"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "response"}]},
        ]
        transcript_path = self._create_transcript(messages)

        result = handle_stop_finalize(
            config=self.config,
            session_id=session_id,
            transcript_path=transcript_path,
            state_dir=self.state_dir,
        )

        assert result is True
        assert captured_batch is not None
        assert len(captured_batch) == 1, "Should only have trace-create (no generation)"
        assert captured_batch[0]["type"] == "trace-create"

    @patch(
        "pacemaker.langfuse.orchestrator.jsonl_parser.parse_session_metadata",
        return_value={"model": "claude-sonnet-4-5"},
    )
    @patch(
        "pacemaker.langfuse.orchestrator.incremental.parse_incremental_lines",
        return_value={
            "token_usage": {
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_read_tokens": 0,
            },
            "lines_parsed": 5,
            "last_line": 5,
            "tool_calls": [],
        },
    )
    @patch("pacemaker.langfuse.orchestrator.finalize_trace_with_output")
    @patch(
        "pacemaker.langfuse.orchestrator.sanitize_trace",
        side_effect=lambda batch, db_path: batch,
    )
    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    @patch("pacemaker.langfuse.orchestrator.flush_pending_trace")
    def test_stop_finalize_generation_omits_cache_read_when_zero(
        self,
        mock_flush_pending,
        mock_push,
        mock_sanitize,
        mock_finalize,
        mock_parse_incremental,
        mock_parse_metadata,
    ):
        """
        Test that cache_read is omitted from usage when zero.

        When cache_read_tokens is 0, the usage dict should NOT include
        the cache_read field at all.
        """
        session_id = "test-gen-no-cache"
        trace_id = f"{session_id}-trace-ghi"

        # Create state WITHOUT token usage (tokens come from transcript parsing)
        state_manager = StateManager(self.state_dir)
        state_manager.create_or_update(
            session_id=session_id,
            trace_id=trace_id,
            last_pushed_line=0,
            metadata={
                "current_trace_id": trace_id,
                "trace_start_line": 0,
            },
        )

        mock_finalize.return_value = {
            "id": trace_id,
            "output": "test output",
            "metadata": {},
        }

        captured_batch = None

        def capture_push(base_url, pk, sk, batch, timeout=10):
            nonlocal captured_batch
            captured_batch = batch
            return (True, len(batch))

        mock_push.side_effect = capture_push

        messages = [
            {"role": "user", "content": [{"type": "text", "text": "test"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "response"}]},
        ]
        transcript_path = self._create_transcript(messages)

        result = handle_stop_finalize(
            config=self.config,
            session_id=session_id,
            transcript_path=transcript_path,
            state_dir=self.state_dir,
        )

        assert result is True
        assert captured_batch is not None
        assert len(captured_batch) == 2

        # Verify generation usage
        gen_event = captured_batch[1]
        usage = gen_event["body"]["usage"]

        assert usage["input"] == 1000
        assert usage["output"] == 500
        assert usage["total"] == 1500
        assert "cache_read_input_tokens" not in usage  # Should NOT be in usage
        # usageDetails should also not have cache_read when zero
        usage_details = gen_event["body"]["usageDetails"]
        assert "cache_read_input_tokens" not in usage_details
