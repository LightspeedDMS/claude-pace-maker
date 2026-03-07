#!/usr/bin/env python3
"""
Tests for fallback.py - Resilient Pacing with Fallback Mode.

TDD: Tests written first to define behavior before implementation.
Story #38: API returns 429 -> enter fallback -> synthetic utilization -> recovery.

Acceptance Criteria covered:
- Scenario 1: API returns 429 -> enter fallback, capture baselines
- Scenario 2: Synthetic utilization calculated during fallback
- Scenario 5: API recovery triggers true-up then normal
- Scenario 7: Self-sufficient without claude-usage
"""

import json
import time
from pathlib import Path
import sys

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestFallbackStateEnum:
    """Tests for FallbackState enum values."""

    def test_normal_state_exists(self):
        """FallbackState has NORMAL value."""
        from pacemaker.fallback import FallbackState

        assert hasattr(FallbackState, "NORMAL")

    def test_fallback_state_exists(self):
        """FallbackState has FALLBACK value."""
        from pacemaker.fallback import FallbackState

        assert hasattr(FallbackState, "FALLBACK")

    def test_states_are_distinct(self):
        """NORMAL and FALLBACK are distinct values."""
        from pacemaker.fallback import FallbackState

        assert FallbackState.NORMAL != FallbackState.FALLBACK


class TestLoadFallbackState:
    """Tests for load_fallback_state() function."""

    def test_returns_normal_when_file_missing(self, tmp_path):
        """load_fallback_state returns NORMAL state when file does not exist."""
        from pacemaker.fallback import load_fallback_state, FallbackState

        missing_path = tmp_path / "fallback_state.json"
        state = load_fallback_state(str(missing_path))

        assert state["state"] == FallbackState.NORMAL.value

    def test_returns_defaults_when_file_corrupt(self, tmp_path):
        """load_fallback_state returns NORMAL when file contains invalid JSON."""
        from pacemaker.fallback import load_fallback_state, FallbackState

        corrupt_path = tmp_path / "fallback_state.json"
        corrupt_path.write_text("not valid json {{{")
        state = load_fallback_state(str(corrupt_path))

        assert state["state"] == FallbackState.NORMAL.value

    def test_returns_saved_fallback_state(self, tmp_path):
        """load_fallback_state returns previously saved state."""
        from pacemaker.fallback import load_fallback_state, FallbackState

        state_path = tmp_path / "fallback_state.json"
        saved = {
            "state": FallbackState.FALLBACK.value,
            "baseline_5h": 45.0,
            "baseline_7d": 30.0,
            "accumulated_cost": 10.0,
            "entered_at": time.time(),
        }
        state_path.write_text(json.dumps(saved))

        state = load_fallback_state(str(state_path))

        assert state["state"] == FallbackState.FALLBACK.value
        assert state["baseline_5h"] == 45.0
        assert state["baseline_7d"] == 30.0
        assert state["accumulated_cost"] == 10.0

    def test_normal_state_has_zero_accumulated_cost(self, tmp_path):
        """Default (NORMAL) state has accumulated_cost = 0.0."""
        from pacemaker.fallback import load_fallback_state

        missing_path = tmp_path / "fallback_state.json"
        state = load_fallback_state(str(missing_path))

        assert state["accumulated_cost"] == 0.0

    def test_normal_state_has_none_baselines(self, tmp_path):
        """Default (NORMAL) state has None baseline values."""
        from pacemaker.fallback import load_fallback_state

        missing_path = tmp_path / "fallback_state.json"
        state = load_fallback_state(str(missing_path))

        assert state["baseline_5h"] is None
        assert state["baseline_7d"] is None


class TestSaveFallbackState:
    """Tests for save_fallback_state() - atomic writes."""

    def test_saves_and_loads_state(self, tmp_path):
        """save_fallback_state persists state that can be loaded back."""
        from pacemaker.fallback import (
            save_fallback_state,
            load_fallback_state,
            FallbackState,
        )

        state_path = tmp_path / "fallback_state.json"
        state = {
            "state": FallbackState.FALLBACK.value,
            "baseline_5h": 45.0,
            "baseline_7d": 30.0,
            "accumulated_cost": 10.0,
            "entered_at": time.time(),
        }

        save_fallback_state(state, str(state_path))
        loaded = load_fallback_state(str(state_path))

        assert loaded["state"] == FallbackState.FALLBACK.value
        assert loaded["baseline_5h"] == 45.0
        assert loaded["accumulated_cost"] == 10.0

    def test_uses_atomic_write(self, tmp_path):
        """save_fallback_state writes atomically (no tmp file left behind)."""
        from pacemaker.fallback import save_fallback_state, FallbackState

        state_path = tmp_path / "fallback_state.json"
        state = {
            "state": FallbackState.NORMAL.value,
            "baseline_5h": None,
            "baseline_7d": None,
            "accumulated_cost": 0.0,
            "entered_at": None,
        }

        save_fallback_state(state, str(state_path))

        # No temp files should remain
        tmp_files = list(tmp_path.glob("*.tmp*"))
        assert len(tmp_files) == 0

        # Main file should exist
        assert state_path.exists()

    def test_creates_parent_directory(self, tmp_path):
        """save_fallback_state creates parent directories if needed."""
        from pacemaker.fallback import save_fallback_state, FallbackState

        nested_path = tmp_path / "subdir" / "nested" / "fallback_state.json"
        state = {
            "state": FallbackState.NORMAL.value,
            "baseline_5h": None,
            "baseline_7d": None,
            "accumulated_cost": 0.0,
            "entered_at": None,
        }

        save_fallback_state(state, str(nested_path))

        assert nested_path.exists()


class TestEnterFallback:
    """Tests for enter_fallback() - Scenario 1."""

    def test_transitions_to_fallback_state(self, tmp_path):
        """enter_fallback transitions state to FALLBACK."""
        from pacemaker.fallback import (
            enter_fallback,
            load_fallback_state,
            FallbackState,
        )

        usage_cache_path = tmp_path / "usage_cache.json"
        state_path = tmp_path / "fallback_state.json"

        # Write usage cache with real data
        usage_cache_path.write_text(
            json.dumps(
                {
                    "timestamp": time.time(),
                    "response": {
                        "five_hour": {"utilization": 45.0, "resets_at": None},
                        "seven_day": {"utilization": 30.0, "resets_at": None},
                    },
                }
            )
        )

        enter_fallback(str(usage_cache_path), str(state_path))

        state = load_fallback_state(str(state_path))
        assert state["state"] == FallbackState.FALLBACK.value

    def test_captures_baseline_5h_from_usage_cache(self, tmp_path):
        """enter_fallback captures baseline_5h from usage_cache.json."""
        from pacemaker.fallback import enter_fallback, load_fallback_state

        usage_cache_path = tmp_path / "usage_cache.json"
        state_path = tmp_path / "fallback_state.json"

        usage_cache_path.write_text(
            json.dumps(
                {
                    "timestamp": time.time(),
                    "response": {
                        "five_hour": {"utilization": 45.0, "resets_at": None},
                        "seven_day": {"utilization": 30.0, "resets_at": None},
                    },
                }
            )
        )

        enter_fallback(str(usage_cache_path), str(state_path))

        state = load_fallback_state(str(state_path))
        assert state["baseline_5h"] == 45.0

    def test_captures_baseline_7d_from_usage_cache(self, tmp_path):
        """enter_fallback captures baseline_7d from usage_cache.json."""
        from pacemaker.fallback import enter_fallback, load_fallback_state

        usage_cache_path = tmp_path / "usage_cache.json"
        state_path = tmp_path / "fallback_state.json"

        usage_cache_path.write_text(
            json.dumps(
                {
                    "timestamp": time.time(),
                    "response": {
                        "five_hour": {"utilization": 45.0, "resets_at": None},
                        "seven_day": {"utilization": 30.0, "resets_at": None},
                    },
                }
            )
        )

        enter_fallback(str(usage_cache_path), str(state_path))

        state = load_fallback_state(str(state_path))
        assert state["baseline_7d"] == 30.0

    def test_initializes_accumulated_cost_to_zero(self, tmp_path):
        """enter_fallback initializes accumulated_cost to 0.0."""
        from pacemaker.fallback import enter_fallback, load_fallback_state

        usage_cache_path = tmp_path / "usage_cache.json"
        state_path = tmp_path / "fallback_state.json"

        usage_cache_path.write_text(
            json.dumps(
                {
                    "timestamp": time.time(),
                    "response": {
                        "five_hour": {"utilization": 45.0, "resets_at": None},
                        "seven_day": None,
                    },
                }
            )
        )

        enter_fallback(str(usage_cache_path), str(state_path))

        state = load_fallback_state(str(state_path))
        assert state["accumulated_cost"] == 0.0

    def test_sets_entered_at_timestamp(self, tmp_path):
        """enter_fallback records the timestamp when fallback was entered."""
        from pacemaker.fallback import enter_fallback, load_fallback_state

        usage_cache_path = tmp_path / "usage_cache.json"
        state_path = tmp_path / "fallback_state.json"

        before = time.time()
        usage_cache_path.write_text(
            json.dumps(
                {
                    "timestamp": time.time(),
                    "response": {
                        "five_hour": {"utilization": 45.0, "resets_at": None},
                        "seven_day": None,
                    },
                }
            )
        )

        enter_fallback(str(usage_cache_path), str(state_path))
        after = time.time()

        state = load_fallback_state(str(state_path))
        assert state["entered_at"] is not None
        assert before <= state["entered_at"] <= after

    def test_handles_missing_usage_cache_gracefully(self, tmp_path):
        """enter_fallback still enters fallback even if usage_cache.json is missing."""
        from pacemaker.fallback import (
            enter_fallback,
            load_fallback_state,
            FallbackState,
        )

        usage_cache_path = tmp_path / "usage_cache.json"  # Does not exist
        state_path = tmp_path / "fallback_state.json"

        enter_fallback(str(usage_cache_path), str(state_path))

        state = load_fallback_state(str(state_path))
        # Should still transition to FALLBACK even without cache
        assert state["state"] == FallbackState.FALLBACK.value
        # Baselines default to 0.0 if cache missing
        assert state["baseline_5h"] is not None or state["baseline_5h"] == 0.0

    def test_handles_null_seven_day_in_usage_cache(self, tmp_path):
        """enter_fallback handles null seven_day in usage_cache gracefully."""
        from pacemaker.fallback import enter_fallback, load_fallback_state

        usage_cache_path = tmp_path / "usage_cache.json"
        state_path = tmp_path / "fallback_state.json"

        usage_cache_path.write_text(
            json.dumps(
                {
                    "timestamp": time.time(),
                    "response": {
                        "five_hour": {"utilization": 60.0, "resets_at": None},
                        "seven_day": None,
                    },
                }
            )
        )

        enter_fallback(str(usage_cache_path), str(state_path))

        state = load_fallback_state(str(state_path))
        assert state["baseline_5h"] == 60.0
        assert state["baseline_7d"] == 0.0

    def test_idempotent_when_already_in_fallback(self, tmp_path):
        """enter_fallback does not reset accumulated_cost if already in fallback."""
        from pacemaker.fallback import (
            enter_fallback,
            load_fallback_state,
            save_fallback_state,
        )

        usage_cache_path = tmp_path / "usage_cache.json"
        state_path = tmp_path / "fallback_state.json"

        usage_cache_path.write_text(
            json.dumps(
                {
                    "timestamp": time.time(),
                    "response": {
                        "five_hour": {"utilization": 45.0, "resets_at": None},
                        "seven_day": None,
                    },
                }
            )
        )

        # Enter fallback first time
        enter_fallback(str(usage_cache_path), str(state_path))

        # Manually accumulate some cost
        state = load_fallback_state(str(state_path))
        state["accumulated_cost"] = 5.0
        save_fallback_state(state, str(state_path))

        # Enter fallback again - should NOT reset accumulated_cost
        enter_fallback(str(usage_cache_path), str(state_path))

        state = load_fallback_state(str(state_path))
        # accumulated_cost should be preserved (already in fallback)
        assert state["accumulated_cost"] == 5.0


class TestExitFallback:
    """Tests for exit_fallback() - Scenario 5."""

    def _setup_fallback_state(self, tmp_path) -> Path:
        """Helper: create a fallback_state.json in fallback mode."""
        from pacemaker.fallback import save_fallback_state, FallbackState

        state_path = tmp_path / "fallback_state.json"
        state = {
            "state": FallbackState.FALLBACK.value,
            "baseline_5h": 45.0,
            "baseline_7d": 30.0,
            "accumulated_cost": 10.0,
            "entered_at": time.time() - 300,
        }
        save_fallback_state(state, str(state_path))
        return state_path

    def test_transitions_to_normal_on_recovery(self, tmp_path):
        """exit_fallback transitions state to NORMAL when real data arrives."""
        from pacemaker.fallback import exit_fallback, load_fallback_state, FallbackState

        state_path = self._setup_fallback_state(tmp_path)

        exit_fallback(real_5h=50.0, real_7d=35.0, state_path=str(state_path))

        state = load_fallback_state(str(state_path))
        assert state["state"] == FallbackState.NORMAL.value

    def test_clears_accumulated_cost_on_recovery(self, tmp_path):
        """exit_fallback clears accumulated_cost when transitioning to normal."""
        from pacemaker.fallback import exit_fallback, load_fallback_state

        state_path = self._setup_fallback_state(tmp_path)

        exit_fallback(real_5h=50.0, real_7d=35.0, state_path=str(state_path))

        state = load_fallback_state(str(state_path))
        assert state["accumulated_cost"] == 0.0

    def test_clears_baselines_on_recovery(self, tmp_path):
        """exit_fallback clears baseline values when transitioning to normal."""
        from pacemaker.fallback import exit_fallback, load_fallback_state

        state_path = self._setup_fallback_state(tmp_path)

        exit_fallback(real_5h=50.0, real_7d=35.0, state_path=str(state_path))

        state = load_fallback_state(str(state_path))
        assert state["baseline_5h"] is None
        assert state["baseline_7d"] is None

    def test_noop_when_already_normal(self, tmp_path):
        """exit_fallback is a no-op when state is already NORMAL."""
        from pacemaker.fallback import (
            exit_fallback,
            load_fallback_state,
            save_fallback_state,
            FallbackState,
        )

        state_path = tmp_path / "fallback_state.json"
        state = {
            "state": FallbackState.NORMAL.value,
            "baseline_5h": None,
            "baseline_7d": None,
            "accumulated_cost": 0.0,
            "entered_at": None,
        }
        save_fallback_state(state, str(state_path))

        exit_fallback(real_5h=50.0, real_7d=35.0, state_path=str(state_path))

        loaded = load_fallback_state(str(state_path))
        assert loaded["state"] == FallbackState.NORMAL.value


class TestCalculateSynthetic:
    """Tests for calculate_synthetic() - Scenario 2."""

    def _make_fallback_state(
        self, baseline_5h: float, baseline_7d: float, accumulated_cost: float
    ) -> dict:
        """Helper: create a fallback state dict."""
        from pacemaker.fallback import FallbackState

        return {
            "state": FallbackState.FALLBACK.value,
            "baseline_5h": baseline_5h,
            "baseline_7d": baseline_7d,
            "accumulated_cost": accumulated_cost,
            "entered_at": time.time() - 300,
        }

    def _make_token_costs(self) -> dict:
        """Helper: create token_costs dict matching config/token_costs.json."""
        return {
            "5x": {"coefficient_5h": 0.0075, "coefficient_7d": 0.0011},
            "20x": {"coefficient_5h": 0.0014, "coefficient_7d": 0.0002},
        }

    def test_scenario2_synthetic_5h_calculation(self):
        """
        Scenario 2: synthetic_5h = baseline_5h + (cost * coefficient_5h * 100)
        Given baseline_5h=45.0, tier=5x, coefficient=0.0075, cost=$10.00
        Then synthetic_5h = 45.0 + (10.0 * 0.0075 * 100) = 52.5

        Note: The story spec stated 45.75 but that is an arithmetic error.
        10.0 * 0.0075 * 100 = 7.5, so 45.0 + 7.5 = 52.5.
        """
        from pacemaker.fallback import calculate_synthetic

        state = self._make_fallback_state(
            baseline_5h=45.0, baseline_7d=30.0, accumulated_cost=10.0
        )
        token_costs = self._make_token_costs()

        result = calculate_synthetic(state, tier="5x", token_costs=token_costs)

        assert result["synthetic_5h"] == pytest.approx(52.5, abs=0.001)

    def test_synthetic_5h_capped_at_100(self):
        """Synthetic 5h utilization is capped at 100.0."""
        from pacemaker.fallback import calculate_synthetic

        state = self._make_fallback_state(
            baseline_5h=95.0, baseline_7d=30.0, accumulated_cost=1000.0
        )
        token_costs = self._make_token_costs()

        result = calculate_synthetic(state, tier="5x", token_costs=token_costs)

        assert result["synthetic_5h"] == 100.0

    def test_synthetic_7d_calculation(self):
        """
        synthetic_7d = baseline_7d + (cost * coefficient_7d * 100)
        Given baseline_7d=30.0, tier=5x, coefficient=0.0011, cost=$10.00
        Then synthetic_7d = 30.0 + (10.0 * 0.0011 * 100) = 31.1
        """
        from pacemaker.fallback import calculate_synthetic

        state = self._make_fallback_state(
            baseline_5h=45.0, baseline_7d=30.0, accumulated_cost=10.0
        )
        token_costs = self._make_token_costs()

        result = calculate_synthetic(state, tier="5x", token_costs=token_costs)

        assert result["synthetic_7d"] == pytest.approx(31.1, abs=0.001)

    def test_synthetic_7d_capped_at_100(self):
        """Synthetic 7d utilization is capped at 100.0."""
        from pacemaker.fallback import calculate_synthetic

        state = self._make_fallback_state(
            baseline_5h=30.0, baseline_7d=99.0, accumulated_cost=10000.0
        )
        token_costs = self._make_token_costs()

        result = calculate_synthetic(state, tier="5x", token_costs=token_costs)

        assert result["synthetic_7d"] == 100.0

    def test_zero_accumulated_cost_returns_baselines(self):
        """With zero accumulated cost, synthetic values equal baselines."""
        from pacemaker.fallback import calculate_synthetic

        state = self._make_fallback_state(
            baseline_5h=45.0, baseline_7d=30.0, accumulated_cost=0.0
        )
        token_costs = self._make_token_costs()

        result = calculate_synthetic(state, tier="5x", token_costs=token_costs)

        assert result["synthetic_5h"] == pytest.approx(45.0, abs=0.001)
        assert result["synthetic_7d"] == pytest.approx(30.0, abs=0.001)

    def test_20x_tier_uses_different_coefficients(self):
        """20x tier uses different (smaller) coefficients than 5x."""
        from pacemaker.fallback import calculate_synthetic

        state = self._make_fallback_state(
            baseline_5h=45.0, baseline_7d=30.0, accumulated_cost=10.0
        )
        token_costs = self._make_token_costs()

        result_5x = calculate_synthetic(state, tier="5x", token_costs=token_costs)
        result_20x = calculate_synthetic(state, tier="20x", token_costs=token_costs)

        # 20x has smaller coefficients, so synthetic values should be smaller
        assert result_20x["synthetic_5h"] < result_5x["synthetic_5h"]
        assert result_20x["synthetic_7d"] < result_5x["synthetic_7d"]

    def test_result_contains_is_synthetic_flag(self):
        """calculate_synthetic result includes is_synthetic=True flag."""
        from pacemaker.fallback import calculate_synthetic

        state = self._make_fallback_state(
            baseline_5h=45.0, baseline_7d=30.0, accumulated_cost=10.0
        )
        token_costs = self._make_token_costs()

        result = calculate_synthetic(state, tier="5x", token_costs=token_costs)

        assert result["is_synthetic"] is True

    def test_result_contains_fallback_mode(self):
        """calculate_synthetic result includes fallback_mode=True flag."""
        from pacemaker.fallback import calculate_synthetic

        state = self._make_fallback_state(
            baseline_5h=45.0, baseline_7d=30.0, accumulated_cost=10.0
        )
        token_costs = self._make_token_costs()

        result = calculate_synthetic(state, tier="5x", token_costs=token_costs)

        assert result["fallback_mode"] is True

    def test_none_baseline_treated_as_zero(self):
        """calculate_synthetic treats None baselines as 0.0."""
        from pacemaker.fallback import calculate_synthetic, FallbackState

        state = {
            "state": FallbackState.FALLBACK.value,
            "baseline_5h": None,
            "baseline_7d": None,
            "accumulated_cost": 10.0,
            "entered_at": time.time(),
        }
        token_costs = self._make_token_costs()

        # Should not raise, should treat None as 0.0
        result = calculate_synthetic(state, tier="5x", token_costs=token_costs)

        assert result["synthetic_5h"] == pytest.approx(
            7.5, abs=0.001
        )  # 0 + 10 * 0.0075 * 100
        assert result["synthetic_7d"] == pytest.approx(
            1.1, abs=0.001
        )  # 0 + 10 * 0.0011 * 100


class TestAccumulateCost:
    """Tests for accumulate_cost() - cost accumulation during fallback."""

    def _setup_fallback_state_file(
        self, tmp_path, accumulated_cost: float = 0.0
    ) -> Path:
        """Helper: create fallback_state.json in fallback mode."""
        from pacemaker.fallback import save_fallback_state, FallbackState

        state_path = tmp_path / "fallback_state.json"
        state = {
            "state": FallbackState.FALLBACK.value,
            "baseline_5h": 45.0,
            "baseline_7d": 30.0,
            "accumulated_cost": accumulated_cost,
            "entered_at": time.time() - 300,
        }
        save_fallback_state(state, str(state_path))
        return state_path

    def test_accumulates_opus_input_tokens(self, tmp_path):
        """accumulate_cost adds cost for opus input tokens."""
        from pacemaker.fallback import accumulate_cost, load_fallback_state

        state_path = self._setup_fallback_state_file(tmp_path)

        # Opus input: $15 per 1M tokens
        # 1000 tokens = $0.015
        accumulate_cost(
            input_tokens=1000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="opus",
            state_path=str(state_path),
        )

        state = load_fallback_state(str(state_path))
        expected = 1000 * 15.0 / 1_000_000  # $0.015
        assert state["accumulated_cost"] == pytest.approx(expected, abs=0.0001)

    def test_accumulates_sonnet_output_tokens(self, tmp_path):
        """accumulate_cost adds cost for sonnet output tokens."""
        from pacemaker.fallback import accumulate_cost, load_fallback_state

        state_path = self._setup_fallback_state_file(tmp_path)

        # Sonnet output: $15 per 1M tokens
        # 500 tokens = $0.0075
        accumulate_cost(
            input_tokens=0,
            output_tokens=500,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            state_path=str(state_path),
        )

        state = load_fallback_state(str(state_path))
        expected = 500 * 15.0 / 1_000_000
        assert state["accumulated_cost"] == pytest.approx(expected, abs=0.0001)

    def test_accumulates_across_multiple_calls(self, tmp_path):
        """accumulate_cost sums costs across multiple invocations."""
        from pacemaker.fallback import accumulate_cost, load_fallback_state

        state_path = self._setup_fallback_state_file(tmp_path)

        accumulate_cost(
            input_tokens=1000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="opus",
            state_path=str(state_path),
        )
        accumulate_cost(
            input_tokens=2000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="opus",
            state_path=str(state_path),
        )

        state = load_fallback_state(str(state_path))
        expected = (1000 + 2000) * 15.0 / 1_000_000
        assert state["accumulated_cost"] == pytest.approx(expected, abs=0.0001)

    def test_noop_when_not_in_fallback(self, tmp_path):
        """accumulate_cost does nothing when state is NORMAL."""
        from pacemaker.fallback import (
            accumulate_cost,
            load_fallback_state,
            save_fallback_state,
            FallbackState,
        )

        state_path = tmp_path / "fallback_state.json"
        state = {
            "state": FallbackState.NORMAL.value,
            "baseline_5h": None,
            "baseline_7d": None,
            "accumulated_cost": 0.0,
            "entered_at": None,
        }
        save_fallback_state(state, str(state_path))

        accumulate_cost(
            input_tokens=1000,
            output_tokens=1000,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="opus",
            state_path=str(state_path),
        )

        loaded = load_fallback_state(str(state_path))
        assert loaded["accumulated_cost"] == 0.0

    def test_handles_haiku_model(self, tmp_path):
        """accumulate_cost handles haiku model pricing."""
        from pacemaker.fallback import accumulate_cost, load_fallback_state

        state_path = self._setup_fallback_state_file(tmp_path)

        # Haiku input: $0.80 per 1M tokens
        # 1000 tokens = $0.0008
        accumulate_cost(
            input_tokens=1000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="haiku",
            state_path=str(state_path),
        )

        state = load_fallback_state(str(state_path))
        expected = 1000 * 0.80 / 1_000_000
        assert state["accumulated_cost"] == pytest.approx(expected, abs=0.00001)

    def test_includes_cache_read_cost(self, tmp_path):
        """accumulate_cost includes cache read token cost."""
        from pacemaker.fallback import accumulate_cost, load_fallback_state

        state_path = self._setup_fallback_state_file(tmp_path)

        # Opus cache_read: $1.50 per 1M tokens
        accumulate_cost(
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=1000,
            cache_creation_tokens=0,
            model_family="opus",
            state_path=str(state_path),
        )

        state = load_fallback_state(str(state_path))
        expected = 1000 * 1.50 / 1_000_000
        assert state["accumulated_cost"] == pytest.approx(expected, abs=0.00001)

    def test_includes_cache_creation_cost(self, tmp_path):
        """accumulate_cost includes cache creation token cost."""
        from pacemaker.fallback import accumulate_cost, load_fallback_state

        state_path = self._setup_fallback_state_file(tmp_path)

        # Opus cache_create: $18.75 per 1M tokens
        accumulate_cost(
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=1000,
            model_family="opus",
            state_path=str(state_path),
        )

        state = load_fallback_state(str(state_path))
        expected = 1000 * 18.75 / 1_000_000
        assert state["accumulated_cost"] == pytest.approx(expected, abs=0.00001)

    def test_noop_when_state_file_missing(self, tmp_path):
        """accumulate_cost does not crash when state file is missing."""
        from pacemaker.fallback import accumulate_cost

        state_path = tmp_path / "fallback_state.json"  # Does not exist

        # Should not raise
        accumulate_cost(
            input_tokens=1000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="opus",
            state_path=str(state_path),
        )


class TestLoadTokenCosts:
    """Tests for load_token_costs() function."""

    def test_loads_valid_token_costs_file(self, tmp_path):
        """load_token_costs reads a valid token_costs.json file."""
        from pacemaker.fallback import load_token_costs

        costs_path = tmp_path / "token_costs.json"
        data = {
            "5x": {"coefficient_5h": 0.0075, "coefficient_7d": 0.0011},
            "20x": {"coefficient_5h": 0.0014, "coefficient_7d": 0.0002},
        }
        costs_path.write_text(json.dumps(data))

        result = load_token_costs(str(costs_path))

        assert result["5x"]["coefficient_5h"] == 0.0075
        assert result["5x"]["coefficient_7d"] == 0.0011
        assert result["20x"]["coefficient_5h"] == 0.0014

    def test_returns_defaults_when_file_missing(self, tmp_path):
        """load_token_costs returns default coefficients when file missing."""
        from pacemaker.fallback import load_token_costs

        missing_path = tmp_path / "token_costs.json"

        result = load_token_costs(str(missing_path))

        # Should still return a usable dict with 5x and 20x keys
        assert "5x" in result
        assert "20x" in result
        assert "coefficient_5h" in result["5x"]
        assert "coefficient_7d" in result["5x"]

    def test_returns_defaults_when_file_corrupt(self, tmp_path):
        """load_token_costs returns defaults when file is corrupt JSON."""
        from pacemaker.fallback import load_token_costs

        corrupt_path = tmp_path / "token_costs.json"
        corrupt_path.write_text("not valid json")

        result = load_token_costs(str(corrupt_path))

        assert "5x" in result
        assert "coefficient_5h" in result["5x"]


class TestDetectTier:
    """Tests for detect_tier() - Scenario 7."""

    def test_detects_5x_for_pro_plan(self):
        """detect_tier returns '5x' for Claude Pro plan."""
        from pacemaker.fallback import detect_tier

        profile = {
            "account": {
                "has_claude_pro": True,
                "has_claude_max": False,
            }
        }

        assert detect_tier(profile) == "5x"

    def test_detects_20x_for_max_plan(self):
        """detect_tier returns '20x' for Claude Max plan."""
        from pacemaker.fallback import detect_tier

        profile = {
            "account": {
                "has_claude_pro": True,
                "has_claude_max": True,
            }
        }

        assert detect_tier(profile) == "20x"

    def test_defaults_to_5x_when_profile_empty(self):
        """detect_tier defaults to '5x' when profile has no plan info."""
        from pacemaker.fallback import detect_tier

        profile = {}

        assert detect_tier(profile) == "5x"

    def test_defaults_to_5x_when_profile_none(self):
        """detect_tier defaults to '5x' when profile is None."""
        from pacemaker.fallback import detect_tier

        assert detect_tier(None) == "5x"

    def test_defaults_to_5x_for_unknown_plan(self):
        """detect_tier defaults to '5x' when plan cannot be determined."""
        from pacemaker.fallback import detect_tier

        profile = {
            "account": {
                "has_claude_pro": False,
                "has_claude_max": False,
            }
        }

        assert detect_tier(profile) == "5x"


class TestIsFallbackActive:
    """Tests for is_fallback_active() helper."""

    def test_returns_true_when_in_fallback(self, tmp_path):
        """is_fallback_active returns True when state is FALLBACK."""
        from pacemaker.fallback import (
            is_fallback_active,
            save_fallback_state,
            FallbackState,
        )

        state_path = tmp_path / "fallback_state.json"
        state = {
            "state": FallbackState.FALLBACK.value,
            "baseline_5h": 45.0,
            "baseline_7d": 30.0,
            "accumulated_cost": 0.0,
            "entered_at": time.time(),
        }
        save_fallback_state(state, str(state_path))

        assert is_fallback_active(str(state_path)) is True

    def test_returns_false_when_in_normal(self, tmp_path):
        """is_fallback_active returns False when state is NORMAL."""
        from pacemaker.fallback import (
            is_fallback_active,
            save_fallback_state,
            FallbackState,
        )

        state_path = tmp_path / "fallback_state.json"
        state = {
            "state": FallbackState.NORMAL.value,
            "baseline_5h": None,
            "baseline_7d": None,
            "accumulated_cost": 0.0,
            "entered_at": None,
        }
        save_fallback_state(state, str(state_path))

        assert is_fallback_active(str(state_path)) is False

    def test_returns_false_when_file_missing(self, tmp_path):
        """is_fallback_active returns False when state file missing."""
        from pacemaker.fallback import is_fallback_active

        missing_path = tmp_path / "fallback_state.json"

        assert is_fallback_active(str(missing_path)) is False


class TestAdditionalCoverage:
    """Additional tests to cover exception paths and edge cases."""

    def test_enter_fallback_with_corrupt_cache_still_enters_fallback(self, tmp_path):
        """enter_fallback enters FALLBACK even when usage_cache.json is corrupt."""
        from pacemaker.fallback import (
            enter_fallback,
            load_fallback_state,
            FallbackState,
        )

        usage_cache_path = tmp_path / "usage_cache.json"
        state_path = tmp_path / "fallback_state.json"

        # Write corrupt (non-JSON) cache file
        usage_cache_path.write_text("not valid json {{{")

        enter_fallback(str(usage_cache_path), str(state_path))

        state = load_fallback_state(str(state_path))
        # Should still enter fallback despite corrupt cache
        assert state["state"] == FallbackState.FALLBACK.value
        # Baselines default to 0.0 on parse error
        assert state["baseline_5h"] == 0.0
        assert state["baseline_7d"] == 0.0

    def test_load_token_costs_empty_file_returns_defaults(self, tmp_path):
        """load_token_costs returns defaults when file is empty."""
        from pacemaker.fallback import load_token_costs

        empty_path = tmp_path / "token_costs.json"
        empty_path.write_text("")

        result = load_token_costs(str(empty_path))

        assert "5x" in result
        assert "20x" in result

    def test_load_token_costs_missing_tier_keys_returns_defaults(self, tmp_path):
        """load_token_costs returns defaults when file has no 5x/20x keys."""
        from pacemaker.fallback import load_token_costs

        bad_path = tmp_path / "token_costs.json"
        bad_path.write_text(json.dumps({"generated_date": "2026-03-05"}))

        result = load_token_costs(str(bad_path))

        # Should return defaults, not crash
        assert "5x" in result
        assert "coefficient_5h" in result["5x"]

    def test_detect_tier_handles_exception_in_profile(self):
        """detect_tier returns '5x' when profile raises exception during access."""
        from pacemaker.fallback import detect_tier

        class BadProfile:
            def get(self, *args, **kwargs):
                raise RuntimeError("unexpected error")

        # Should not raise; should return default '5x'
        result = detect_tier(BadProfile())
        assert result == "5x"

    def test_accumulate_cost_uses_sonnet_pricing_for_unknown_model(self, tmp_path):
        """accumulate_cost uses sonnet pricing as fallback for unknown model families."""
        from pacemaker.fallback import (
            accumulate_cost,
            load_fallback_state,
            save_fallback_state,
            FallbackState,
        )

        state_path = tmp_path / "fallback_state.json"
        state = {
            "state": FallbackState.FALLBACK.value,
            "baseline_5h": 45.0,
            "baseline_7d": 30.0,
            "accumulated_cost": 0.0,
            "entered_at": time.time(),
        }
        save_fallback_state(state, str(state_path))

        # Use an unknown model family - should fall back to sonnet pricing
        accumulate_cost(
            input_tokens=1000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="unknown_model",
            state_path=str(state_path),
        )

        loaded = load_fallback_state(str(state_path))
        # Sonnet input: $3.0 per 1M tokens -> 1000 tokens = $0.003
        expected = 1000 * 3.0 / 1_000_000
        assert loaded["accumulated_cost"] == pytest.approx(expected, abs=0.00001)

    def test_load_fallback_state_fills_missing_keys(self, tmp_path):
        """load_fallback_state fills missing keys with defaults."""
        from pacemaker.fallback import load_fallback_state, FallbackState

        state_path = tmp_path / "fallback_state.json"
        # Write partial state (missing accumulated_cost)
        partial = {"state": FallbackState.FALLBACK.value, "baseline_5h": 45.0}
        state_path.write_text(json.dumps(partial))

        state = load_fallback_state(str(state_path))

        # Missing key should be filled with default
        assert "accumulated_cost" in state
        assert state["accumulated_cost"] == 0.0
        assert "baseline_7d" in state

    def test_enter_fallback_with_empty_cache_uses_zero_baselines(self, tmp_path):
        """enter_fallback uses 0.0 baselines when usage_cache.json is empty."""
        from pacemaker.fallback import (
            enter_fallback,
            load_fallback_state,
            FallbackState,
        )

        usage_cache_path = tmp_path / "usage_cache.json"
        state_path = tmp_path / "fallback_state.json"

        # Write empty cache file
        usage_cache_path.write_text("")

        enter_fallback(str(usage_cache_path), str(state_path))

        state = load_fallback_state(str(state_path))
        assert state["state"] == FallbackState.FALLBACK.value
        assert state["baseline_5h"] == 0.0


class TestEnterFallbackCapturesTier:
    """Tests for Finding 7: enter_fallback() captures tier from profile cache."""

    def _write_profile_cache(self, cache_path, has_claude_max: bool) -> None:
        """Helper: write a profile_cache.json with given max setting."""
        import time

        content = {
            "profile": {
                "account": {
                    "has_claude_pro": True,
                    "has_claude_max": has_claude_max,
                }
            },
            "timestamp": time.time(),
        }
        cache_path.write_text(json.dumps(content))

    def test_enter_fallback_captures_20x_tier_from_profile_cache(self, tmp_path):
        """
        enter_fallback captures '20x' tier from profile cache when user has Claude Max.

        Finding 7: The tier was hardcoded to '5x' in pacing_engine.py, causing
        over-estimation of synthetic utilization for 20x users. The fix stores
        tier in fallback state during enter_fallback so it persists across invocations.
        """
        from pacemaker.fallback import enter_fallback, load_fallback_state
        from unittest.mock import patch

        usage_cache_path = tmp_path / "usage_cache.json"
        state_path = tmp_path / "fallback_state.json"
        profile_cache_path = tmp_path / "profile_cache.json"

        usage_cache_path.write_text(
            json.dumps(
                {
                    "timestamp": time.time(),
                    "response": {
                        "five_hour": {"utilization": 45.0, "resets_at": None},
                        "seven_day": {"utilization": 30.0, "resets_at": None},
                    },
                }
            )
        )
        self._write_profile_cache(profile_cache_path, has_claude_max=True)

        with patch(
            "pacemaker.profile_cache.DEFAULT_PROFILE_CACHE_PATH",
            str(profile_cache_path),
        ):
            enter_fallback(str(usage_cache_path), str(state_path))

        state = load_fallback_state(str(state_path))
        assert (
            state.get("tier") == "20x"
        ), f"Expected tier='20x' for Claude Max user, got: {state.get('tier')}"

    def test_enter_fallback_captures_5x_tier_when_no_profile_cache(self, tmp_path):
        """
        enter_fallback defaults to '5x' tier when no profile cache is available.

        Safe default: over-estimates rather than under-estimates utilization.
        """
        from pacemaker.fallback import enter_fallback, load_fallback_state
        from unittest.mock import patch

        usage_cache_path = tmp_path / "usage_cache.json"
        state_path = tmp_path / "fallback_state.json"
        missing_profile_cache = tmp_path / "profile_cache.json"  # Does not exist

        usage_cache_path.write_text(
            json.dumps(
                {
                    "timestamp": time.time(),
                    "response": {
                        "five_hour": {"utilization": 45.0, "resets_at": None},
                        "seven_day": {"utilization": 30.0, "resets_at": None},
                    },
                }
            )
        )

        with patch(
            "pacemaker.profile_cache.DEFAULT_PROFILE_CACHE_PATH",
            str(missing_profile_cache),
        ):
            enter_fallback(str(usage_cache_path), str(state_path))

        state = load_fallback_state(str(state_path))
        assert (
            state.get("tier") == "5x"
        ), f"Expected tier='5x' as safe default when no profile cache, got: {state.get('tier')}"

    def test_default_state_includes_tier_key(self):
        """
        _default_state() includes 'tier' key initialized to None.

        This ensures load_fallback_state() fills in the tier key via
        the defaults-filling logic, preventing KeyError in existing states.
        """
        from pacemaker.fallback import _default_state

        state = _default_state()
        assert "tier" in state, "_default_state() must include 'tier' key"
        assert state["tier"] is None, "_default_state() 'tier' must default to None"
