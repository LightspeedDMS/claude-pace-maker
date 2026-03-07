#!/usr/bin/env python3
"""
Tests for token cost accumulation wiring in hook.py PostToolUse.

Story #38: During fallback mode, each PostToolUse must accumulate the cost
of the most recent API call by reading the last token usage from the transcript.

Integration points tested:
- _get_last_token_usage() parses last JSONL entry with usage from transcript
- _get_last_token_usage() handles missing file gracefully
- _get_last_token_usage() handles empty/corrupt file gracefully
- _get_last_token_usage() classifies model family correctly
- accumulate_cost is called when fallback active + transcript present
- accumulate_cost is NOT called when fallback not active
"""

import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _write_transcript(tmp_path, entries: list) -> Path:
    """Write a JSONL transcript file with the given entries."""
    transcript = tmp_path / "transcript.jsonl"
    lines = [json.dumps(entry) for entry in entries]
    transcript.write_text("\n".join(lines) + "\n")
    return transcript


def _make_assistant_entry(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read: int = 0,
    cache_create: int = 0,
) -> dict:
    """Build a minimal assistant JSONL entry with token usage."""
    return {
        "type": "assistant",
        "message": {
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_create,
            },
        },
        "timestamp": "2026-03-06T10:00:00Z",
    }


def _make_user_entry(text: str = "hello") -> dict:
    """Build a minimal user JSONL entry (no usage)."""
    return {
        "type": "user",
        "message": {"role": "user", "content": text},
        "timestamp": "2026-03-06T10:00:00Z",
    }


class TestGetLastTokenUsage:
    """Tests for _get_last_token_usage() internal function."""

    def test_returns_token_counts_from_last_assistant_entry(self, tmp_path):
        """_get_last_token_usage returns token counts from the last assistant message."""
        from pacemaker.hook import _get_last_token_usage

        transcript = _write_transcript(
            tmp_path,
            [
                _make_assistant_entry("claude-sonnet-4-6", 1000, 500),
            ],
        )

        result = _get_last_token_usage(str(transcript))

        assert result is not None
        assert result["input_tokens"] == 1000
        assert result["output_tokens"] == 500

    def test_returns_last_entry_when_multiple_assistant_entries(self, tmp_path):
        """_get_last_token_usage returns the LAST assistant entry, not the first."""
        from pacemaker.hook import _get_last_token_usage

        transcript = _write_transcript(
            tmp_path,
            [
                _make_assistant_entry("claude-sonnet-4-6", 100, 50),
                _make_user_entry("follow-up"),
                _make_assistant_entry("claude-sonnet-4-6", 2000, 1000),
            ],
        )

        result = _get_last_token_usage(str(transcript))

        assert result is not None
        assert result["input_tokens"] == 2000
        assert result["output_tokens"] == 1000

    def test_returns_none_when_file_missing(self, tmp_path):
        """_get_last_token_usage returns None when transcript file does not exist."""
        from pacemaker.hook import _get_last_token_usage

        missing = tmp_path / "nonexistent.jsonl"

        result = _get_last_token_usage(str(missing))

        assert result is None

    def test_returns_none_when_file_empty(self, tmp_path):
        """_get_last_token_usage returns None when transcript file is empty."""
        from pacemaker.hook import _get_last_token_usage

        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("")

        result = _get_last_token_usage(str(transcript))

        assert result is None

    def test_returns_none_when_no_usage_in_transcript(self, tmp_path):
        """_get_last_token_usage returns None when no assistant entries have usage."""
        from pacemaker.hook import _get_last_token_usage

        transcript = _write_transcript(
            tmp_path,
            [
                _make_user_entry("hello"),
                _make_user_entry("how are you"),
            ],
        )

        result = _get_last_token_usage(str(transcript))

        assert result is None

    def test_handles_corrupt_json_lines_gracefully(self, tmp_path):
        """_get_last_token_usage skips corrupt lines and returns last valid entry."""
        from pacemaker.hook import _get_last_token_usage

        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            json.dumps(_make_assistant_entry("claude-sonnet-4-6", 500, 200))
            + "\n"
            + "this is not valid json {\n"
            + json.dumps(_make_user_entry())
            + "\n"
        )

        result = _get_last_token_usage(str(transcript))

        # Should find the assistant entry (the corrupt line is skipped)
        assert result is not None
        assert result["input_tokens"] == 500

    def test_classifies_opus_model_correctly(self, tmp_path):
        """_get_last_token_usage classifies opus model as 'opus' family."""
        from pacemaker.hook import _get_last_token_usage

        transcript = _write_transcript(
            tmp_path,
            [
                _make_assistant_entry("claude-opus-4-6", 1000, 500),
            ],
        )

        result = _get_last_token_usage(str(transcript))

        assert result is not None
        assert result["model_family"] == "opus"

    def test_classifies_sonnet_model_correctly(self, tmp_path):
        """_get_last_token_usage classifies sonnet model as 'sonnet' family."""
        from pacemaker.hook import _get_last_token_usage

        transcript = _write_transcript(
            tmp_path,
            [
                _make_assistant_entry("claude-sonnet-4-5", 1000, 500),
            ],
        )

        result = _get_last_token_usage(str(transcript))

        assert result is not None
        assert result["model_family"] == "sonnet"

    def test_classifies_haiku_model_correctly(self, tmp_path):
        """_get_last_token_usage classifies haiku model as 'haiku' family."""
        from pacemaker.hook import _get_last_token_usage

        transcript = _write_transcript(
            tmp_path,
            [
                _make_assistant_entry("claude-haiku-3-5", 1000, 500),
            ],
        )

        result = _get_last_token_usage(str(transcript))

        assert result is not None
        assert result["model_family"] == "haiku"

    def test_defaults_to_sonnet_for_unknown_model(self, tmp_path):
        """_get_last_token_usage defaults to 'sonnet' family for unknown models."""
        from pacemaker.hook import _get_last_token_usage

        transcript = _write_transcript(
            tmp_path,
            [
                _make_assistant_entry("claude-unknown-future-model", 1000, 500),
            ],
        )

        result = _get_last_token_usage(str(transcript))

        assert result is not None
        assert result["model_family"] == "sonnet"

    def test_parses_cache_read_tokens(self, tmp_path):
        """_get_last_token_usage includes cache_read_tokens in result."""
        from pacemaker.hook import _get_last_token_usage

        transcript = _write_transcript(
            tmp_path,
            [
                _make_assistant_entry(
                    "claude-sonnet-4-6", 1000, 500, cache_read=2000, cache_create=0
                ),
            ],
        )

        result = _get_last_token_usage(str(transcript))

        assert result is not None
        assert result["cache_read_tokens"] == 2000

    def test_parses_cache_creation_tokens(self, tmp_path):
        """_get_last_token_usage includes cache_creation_tokens in result."""
        from pacemaker.hook import _get_last_token_usage

        transcript = _write_transcript(
            tmp_path,
            [
                _make_assistant_entry(
                    "claude-sonnet-4-6", 1000, 500, cache_read=0, cache_create=3000
                ),
            ],
        )

        result = _get_last_token_usage(str(transcript))

        assert result is not None
        assert result["cache_creation_tokens"] == 3000

    def test_reads_large_file_and_returns_last_entry(self, tmp_path):
        """
        _get_last_token_usage returns the correct last entry for a file with many entries.
        The implementation should seek to end efficiently rather than reading the whole file.
        """
        from pacemaker.hook import _get_last_token_usage

        entries = []
        for i in range(500):  # 500 entries = moderately large file
            if i < 499:
                entries.append(_make_user_entry(f"message {i}"))
            else:
                entries.append(_make_assistant_entry("claude-sonnet-4-6", 9999, 4444))

        transcript = _write_transcript(tmp_path, entries)

        result = _get_last_token_usage(str(transcript))

        assert result is not None
        assert result["input_tokens"] == 9999
        assert result["output_tokens"] == 4444


def _get_total_cost(db_path: str) -> float:
    """Query total accumulated cost from the test DB."""
    from pacemaker.database import execute_with_retry

    def op(conn):
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_dollars), 0.0) FROM accumulated_costs"
        ).fetchone()
        return float(row[0]) if row else 0.0

    return execute_with_retry(db_path, op, readonly=True)


class TestAccumulateCostCalledDuringFallback:
    """Tests that accumulate_cost is called from hook when fallback is active.

    Uses real UsageModel with an isolated test SQLite database.
    Patches pacemaker.hook.UsageModel so the constructor call inside
    _accumulate_fallback_cost() returns our pre-configured test instance.
    """

    def _make_model_in_fallback(self, tmp_path):
        """Create a real UsageModel in FALLBACK state with test DB."""
        from pacemaker.usage_model import UsageModel

        db_path = str(tmp_path / "test.db")
        model = UsageModel(db_path=db_path)
        model.store_api_response(
            {
                "five_hour": {"utilization": 50.0, "resets_at": None},
                "seven_day": {"utilization": 35.0, "resets_at": None},
            }
        )
        model.enter_fallback()
        return model

    def test_accumulate_cost_called_when_fallback_active(self, tmp_path):
        """
        When fallback is active and transcript has token usage,
        accumulate_cost() must be called from the hook's token accumulation path.
        """
        from pacemaker.hook import _accumulate_fallback_cost
        from unittest.mock import patch

        model = self._make_model_in_fallback(tmp_path)
        transcript = _write_transcript(
            tmp_path,
            [
                _make_assistant_entry("claude-sonnet-4-6", 1000, 500),
            ],
        )

        with patch("pacemaker.usage_model.UsageModel", return_value=model):
            _accumulate_fallback_cost(
                transcript_path=str(transcript),
                session_id="test",
            )

        # Verify cost was accumulated (sonnet: 1000 input * $3/1M + 500 output * $15/1M)
        expected_cost = (1000 * 3.0 + 500 * 15.0) / 1_000_000
        total = _get_total_cost(model.db_path)
        assert total == pytest.approx(expected_cost, rel=0.01)

        # Also verify the snapshot reflects synthetic state
        snapshot = model.get_current_usage()
        assert snapshot is not None
        assert snapshot.is_synthetic is True

    def test_accumulate_cost_noop_when_fallback_not_active(self, tmp_path):
        """
        When fallback is NOT active, _accumulate_fallback_cost must be a no-op.
        No cost accumulation in NORMAL mode.
        """
        from pacemaker.hook import _accumulate_fallback_cost
        from pacemaker.usage_model import UsageModel
        from unittest.mock import patch

        db_path = str(tmp_path / "test.db")
        model = UsageModel(db_path=db_path)
        # Do NOT enter fallback — model stays in NORMAL state

        transcript = _write_transcript(
            tmp_path,
            [
                _make_assistant_entry("claude-sonnet-4-6", 1000, 500),
            ],
        )

        with patch("pacemaker.usage_model.UsageModel", return_value=model):
            _accumulate_fallback_cost(
                transcript_path=str(transcript),
                session_id="test",
            )

        # No costs should be accumulated
        assert model.is_fallback_active() is False
        assert _get_total_cost(model.db_path) == pytest.approx(0.0)

    def test_accumulate_fallback_cost_handles_no_transcript(self, tmp_path):
        """
        _accumulate_fallback_cost must not crash when transcript_path is None or missing.
        """
        from pacemaker.hook import _accumulate_fallback_cost
        from unittest.mock import patch

        model = self._make_model_in_fallback(tmp_path)

        with patch("pacemaker.usage_model.UsageModel", return_value=model):
            # Should not raise with None transcript
            _accumulate_fallback_cost(
                transcript_path=None,
                session_id="test",
            )

            # Should not raise with missing file path
            _accumulate_fallback_cost(
                transcript_path=str(tmp_path / "nonexistent.jsonl"),
                session_id="test",
            )

        # No cost should have been accumulated (no valid token data)
        assert _get_total_cost(model.db_path) == pytest.approx(0.0)

    def test_accumulate_fallback_cost_handles_empty_transcript(self, tmp_path):
        """
        _accumulate_fallback_cost must not crash when transcript is empty.
        """
        from pacemaker.hook import _accumulate_fallback_cost
        from unittest.mock import patch

        model = self._make_model_in_fallback(tmp_path)
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("")

        with patch("pacemaker.usage_model.UsageModel", return_value=model):
            # Should not raise
            _accumulate_fallback_cost(
                transcript_path=str(transcript),
                session_id="test",
            )

        # No cost should have been accumulated (empty transcript = no token data)
        assert _get_total_cost(model.db_path) == pytest.approx(0.0)

    def test_accumulate_uses_opus_pricing_for_opus_model(self, tmp_path):
        """
        _accumulate_fallback_cost uses opus pricing when transcript shows opus model.
        Opus input: $15/1M tokens. 1000 tokens = $0.015.
        """
        from pacemaker.hook import _accumulate_fallback_cost
        from unittest.mock import patch

        model = self._make_model_in_fallback(tmp_path)
        transcript = _write_transcript(
            tmp_path,
            [
                _make_assistant_entry("claude-opus-4-6", 1000, 0),
            ],
        )

        with patch("pacemaker.usage_model.UsageModel", return_value=model):
            _accumulate_fallback_cost(
                transcript_path=str(transcript),
                session_id="test",
            )

        # Opus input: $15/1M tokens -> 1000 tokens = $0.015
        expected_cost = 1000 * 15.0 / 1_000_000
        assert _get_total_cost(model.db_path) == pytest.approx(expected_cost, rel=0.01)
