#!/usr/bin/env python3
"""
Tests for cost accumulation idempotency in fallback mode (P9).

Problem: When Claude runs tools in parallel, PostToolUse fires N times for the
same API turn. Each invocation reads the SAME last usage entry from the
transcript and adds the full token cost. This causes accumulated_cost to inflate
N-fold (e.g., 38% -> 52% unexpectedly).

Fix: Two-layer protection:
1. Usage deduplication in accumulate_cost() via last_accumulated_usage field
2. Per-project file lock in _accumulate_fallback_cost() via fcntl.LOCK_NB

This test file verifies both layers independently.

Tests are TDD-first: written BEFORE production code changes.
"""

import json
import time
import sys
import fcntl
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pacemaker.fallback import (
    _default_state,
    accumulate_cost,
    load_fallback_state,
    save_fallback_state,
    FallbackState,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enter_fallback(tmp_path) -> str:
    """Write fallback_state.json in FALLBACK mode, return path as str."""
    sp = str(tmp_path / "fallback_state.json")
    state = _default_state()
    state["state"] = FallbackState.FALLBACK.value
    state["entered_at"] = time.time()
    save_fallback_state(state, sp)
    return sp


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


# ---------------------------------------------------------------------------
# Layer 1: Usage deduplication in accumulate_cost()
# ---------------------------------------------------------------------------


class TestSameUsageNotCountedTwice:
    """Calling accumulate_cost() twice with identical args must add cost only once."""

    def test_same_usage_not_counted_twice(self, tmp_path):
        sp = _enter_fallback(tmp_path)

        accumulate_cost(1_000_000, 0, 0, 0, "sonnet", sp)
        accumulate_cost(1_000_000, 0, 0, 0, "sonnet", sp)  # identical — must be skipped

        state = load_fallback_state(sp)
        # Only the first call should count: sonnet input = $3/1M -> $3.0
        assert state["accumulated_cost"] == pytest.approx(3.0)


class TestDifferentUsageBothCounted:
    """Two calls with different token counts must both add to cost."""

    def test_different_usage_both_counted(self, tmp_path):
        sp = _enter_fallback(tmp_path)

        accumulate_cost(1_000_000, 0, 0, 0, "sonnet", sp)  # $3.0
        accumulate_cost(0, 1_000_000, 0, 0, "sonnet", sp)  # $15.0

        state = load_fallback_state(sp)
        assert state["accumulated_cost"] == pytest.approx(18.0)


class TestLastAccumulatedUsageStored:
    """After accumulation, last_accumulated_usage must be present in state."""

    def test_last_accumulated_usage_stored(self, tmp_path):
        sp = _enter_fallback(tmp_path)

        accumulate_cost(500, 200, 100, 50, "opus", sp)

        state = load_fallback_state(sp)
        assert "last_accumulated_usage" in state
        lau = state["last_accumulated_usage"]
        assert lau is not None
        assert lau["input"] == 500
        assert lau["output"] == 200
        assert lau["cache_read"] == 100
        assert lau["cache_create"] == 50
        assert lau["model"] == "opus"


class TestLastAccumulatedUsageClearedOnExit:
    """exit_fallback() must clear last_accumulated_usage (via _default_state())."""

    def test_last_accumulated_usage_cleared_on_exit(self, tmp_path):
        from pacemaker.fallback import exit_fallback

        sp = _enter_fallback(tmp_path)

        # Accumulate something
        accumulate_cost(1_000_000, 0, 0, 0, "sonnet", sp)

        # Verify it was stored
        state = load_fallback_state(sp)
        assert state.get("last_accumulated_usage") is not None

        # Exit fallback — must reset everything including last_accumulated_usage
        exit_fallback(real_5h=50.0, real_7d=30.0, state_path=sp)

        state = load_fallback_state(sp)
        assert state["last_accumulated_usage"] is None


class TestDifferentModelSameTokensCounted:
    """Same token counts but different model = different usage fingerprint, should count."""

    def test_different_model_same_tokens_counted(self, tmp_path):
        sp = _enter_fallback(tmp_path)

        accumulate_cost(1_000_000, 0, 0, 0, "sonnet", sp)  # $3.0
        accumulate_cost(
            1_000_000, 0, 0, 0, "opus", sp
        )  # $15.0 — different model, must count

        state = load_fallback_state(sp)
        assert state["accumulated_cost"] == pytest.approx(18.0)


class TestZeroTokensStillTracked:
    """Zero-token call sets last_accumulated_usage; subsequent identical zero call is skipped."""

    def test_zero_tokens_still_tracked(self, tmp_path):
        sp = _enter_fallback(tmp_path)

        accumulate_cost(
            0, 0, 0, 0, "sonnet", sp
        )  # First zero call — stored (but adds $0)
        accumulate_cost(0, 0, 0, 0, "sonnet", sp)  # Identical — skipped (still $0)

        state = load_fallback_state(sp)
        assert state["accumulated_cost"] == pytest.approx(0.0)
        # Critically, last_accumulated_usage must be set (dedup is tracking)
        assert state["last_accumulated_usage"] is not None
        assert state["last_accumulated_usage"]["input"] == 0


class TestDedupSurvivesLoadSaveCycle:
    """Deduplication state survives a load/save cycle (persisted in state file)."""

    def test_dedup_survives_load_save_cycle(self, tmp_path):
        sp = _enter_fallback(tmp_path)

        # First call
        accumulate_cost(1_000_000, 0, 0, 0, "sonnet", sp)

        # Reload state and verify last_accumulated_usage is in the file
        state = load_fallback_state(sp)
        assert state["last_accumulated_usage"] is not None

        # Second call with same args — must still be deduplicated after reload
        accumulate_cost(1_000_000, 0, 0, 0, "sonnet", sp)

        state = load_fallback_state(sp)
        # Cost still $3.0, not $6.0
        assert state["accumulated_cost"] == pytest.approx(3.0)


class TestDefaultStateContainsLastAccumulatedUsage:
    """_default_state() must include last_accumulated_usage key set to None."""

    def test_default_state_has_last_accumulated_usage(self):
        state = _default_state()
        assert "last_accumulated_usage" in state
        assert state["last_accumulated_usage"] is None


class TestDefaultStateKeySet:
    """_default_state() must have exactly the expected set of keys (regression guard)."""

    def test_returns_all_expected_keys(self):
        state = _default_state()
        expected_keys = {
            "state",
            "baseline_5h",
            "baseline_7d",
            "resets_at_5h",
            "resets_at_7d",
            "accumulated_cost",
            "rollover_cost_5h",
            "rollover_cost_7d",
            "last_rollover_resets_5h",
            "last_rollover_resets_7d",
            "tier",
            "entered_at",
            "last_accumulated_usage",  # NEW field added by P9
        }
        assert set(state.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Layer 2: File lock skip in _accumulate_fallback_cost()
# ---------------------------------------------------------------------------


class TestConcurrentLockSkip:
    """
    When the file lock is held by another invocation, _accumulate_fallback_cost
    must skip gracefully without raising.

    We simulate lock contention by holding an exclusive lock on the lock file
    before calling _accumulate_fallback_cost. With LOCK_NB, the second attempt
    must fail-fast (BlockingIOError) and return without accumulating.
    """

    def test_concurrent_lock_skip(self, tmp_path):
        from pacemaker.hook import _accumulate_fallback_cost

        sp = _enter_fallback(tmp_path)

        transcript = _write_transcript(
            tmp_path,
            [_make_assistant_entry("claude-sonnet-4-6", 1_000_000, 0)],
        )

        lock_path = Path.home() / ".claude-pace-maker" / "fallback_accumulate.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        # Hold the lock before calling _accumulate_fallback_cost
        lock_fd = open(lock_path, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)  # Blocking — we hold it

            # This invocation should detect lock contention and skip
            _accumulate_fallback_cost(
                transcript_path=str(transcript),
                fallback_state_path=sp,
            )
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()

        # No cost must have been accumulated — the call was skipped
        state = load_fallback_state(sp)
        assert state["accumulated_cost"] == pytest.approx(0.0)
