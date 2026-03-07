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
import time
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


class TestAccumulateCostCalledDuringFallback:
    """Tests that accumulate_cost is called from hook when fallback is active."""

    def _setup_fallback_state(self, tmp_path) -> str:
        """Helper: write fallback_state.json in FALLBACK mode."""
        from pacemaker.fallback import FallbackState, save_fallback_state

        state_path = tmp_path / "fallback_state.json"
        state = {
            "state": FallbackState.FALLBACK.value,
            "baseline_5h": 50.0,
            "baseline_7d": 35.0,
            "accumulated_cost": 0.0,
            "entered_at": time.time() - 300,
        }
        save_fallback_state(state, str(state_path))
        return str(state_path)

    def test_accumulate_cost_called_when_fallback_active(self, tmp_path):
        """
        When fallback is active and transcript has token usage,
        accumulate_cost() must be called from the hook's token accumulation path.
        """
        from pacemaker.hook import _accumulate_fallback_cost
        from pacemaker import fallback

        state_path = self._setup_fallback_state(tmp_path)
        transcript = _write_transcript(
            tmp_path,
            [
                _make_assistant_entry("claude-sonnet-4-6", 1000, 500),
            ],
        )

        _accumulate_fallback_cost(
            transcript_path=str(transcript),
            fallback_state_path=state_path,
        )

        state = fallback.load_fallback_state(state_path)
        # Cost must have been accumulated (sonnet: 1000 input + 500 output)
        expected_cost = (1000 * 3.0 + 500 * 15.0) / 1_000_000  # sonnet pricing
        assert state["accumulated_cost"] == pytest.approx(expected_cost, rel=0.01)

    def test_accumulate_cost_noop_when_fallback_not_active(self, tmp_path):
        """
        When fallback is NOT active, _accumulate_fallback_cost must be a no-op.
        No cost accumulation in NORMAL mode.
        """
        from pacemaker.hook import _accumulate_fallback_cost
        from pacemaker import fallback

        state_path = str(tmp_path / "fallback_state.json")
        # No state file = NORMAL state

        transcript = _write_transcript(
            tmp_path,
            [
                _make_assistant_entry("claude-sonnet-4-6", 1000, 500),
            ],
        )

        _accumulate_fallback_cost(
            transcript_path=str(transcript),
            fallback_state_path=state_path,
        )

        # State should still be NORMAL
        state = fallback.load_fallback_state(state_path)
        assert state["accumulated_cost"] == 0.0

    def test_accumulate_fallback_cost_handles_no_transcript(self, tmp_path):
        """
        _accumulate_fallback_cost must not crash when transcript_path is None or missing.
        """
        from pacemaker.hook import _accumulate_fallback_cost

        state_path = self._setup_fallback_state(tmp_path)

        # Should not raise
        _accumulate_fallback_cost(
            transcript_path=None,
            fallback_state_path=state_path,
        )

        _accumulate_fallback_cost(
            transcript_path=str(tmp_path / "nonexistent.jsonl"),
            fallback_state_path=state_path,
        )

    def test_accumulate_fallback_cost_handles_empty_transcript(self, tmp_path):
        """
        _accumulate_fallback_cost must not crash when transcript is empty.
        """
        from pacemaker.hook import _accumulate_fallback_cost

        state_path = self._setup_fallback_state(tmp_path)
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("")

        # Should not raise
        _accumulate_fallback_cost(
            transcript_path=str(transcript),
            fallback_state_path=state_path,
        )

    def test_accumulate_uses_opus_pricing_for_opus_model(self, tmp_path):
        """
        _accumulate_fallback_cost uses opus pricing when transcript shows opus model.
        Opus input: $15/1M tokens. 1000 tokens = $0.015.
        """
        from pacemaker.hook import _accumulate_fallback_cost
        from pacemaker import fallback
        from pacemaker.fallback import FallbackState, save_fallback_state

        state_path = str(tmp_path / "fallback_state.json")
        save_fallback_state(
            {
                "state": FallbackState.FALLBACK.value,
                "baseline_5h": 10.0,
                "baseline_7d": 5.0,
                "accumulated_cost": 0.0,
                "entered_at": time.time(),
            },
            state_path,
        )

        transcript = _write_transcript(
            tmp_path,
            [
                _make_assistant_entry("claude-opus-4-6", 1000, 0),
            ],
        )

        _accumulate_fallback_cost(
            transcript_path=str(transcript),
            fallback_state_path=state_path,
        )

        state = fallback.load_fallback_state(state_path)
        # Opus input: $15/1M tokens -> 1000 tokens = $0.015
        assert state["accumulated_cost"] == pytest.approx(
            1000 * 15.0 / 1_000_000, rel=0.01
        )
