#!/usr/bin/env python3
"""
Integration tests for fallback.py - Full fallback flow.

TDD: Tests written first to define behavior before implementation.
Story #38: Complete fallback flow from enter to recovery.

Integration tests use real filesystem (no mocking) and test:
- Full fallback cycle: enter -> accumulate -> calculate -> exit
- State persistence across invocations
- Shared file access
- Display indicators (Scenario 6)
"""

import json
import time
from pathlib import Path
import sys

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestFullFallbackCycle:
    """Integration tests for the complete fallback lifecycle."""

    def _write_usage_cache(
        self, tmp_path, five_hour_util: float, seven_day_util: float
    ) -> Path:
        """Helper: write a usage_cache.json file."""
        usage_cache_path = tmp_path / "usage_cache.json"
        usage_cache_path.write_text(
            json.dumps(
                {
                    "timestamp": time.time(),
                    "response": {
                        "five_hour": {
                            "utilization": five_hour_util,
                            "resets_at": None,
                        },
                        "seven_day": {
                            "utilization": seven_day_util,
                            "resets_at": None,
                        },
                    },
                }
            )
        )
        return usage_cache_path

    def _write_token_costs(self, tmp_path) -> Path:
        """Helper: write a token_costs.json file."""
        costs_path = tmp_path / "token_costs.json"
        costs_path.write_text(
            json.dumps(
                {
                    "5x": {"coefficient_5h": 0.0075, "coefficient_7d": 0.0011},
                    "20x": {"coefficient_5h": 0.0014, "coefficient_7d": 0.0002},
                }
            )
        )
        return costs_path

    def test_full_cycle_normal_to_fallback_and_back(self, tmp_path):
        """
        Full cycle: NORMAL -> 429 -> FALLBACK -> accumulate -> API recovers -> NORMAL.
        """
        from pacemaker.fallback import (
            enter_fallback,
            exit_fallback,
            accumulate_cost,
            calculate_synthetic,
            load_fallback_state,
            load_token_costs,
            is_fallback_active,
            FallbackState,
        )

        usage_cache_path = self._write_usage_cache(tmp_path, 45.0, 30.0)
        costs_path = self._write_token_costs(tmp_path)
        state_path = tmp_path / "fallback_state.json"

        # 1. Start in NORMAL state
        assert is_fallback_active(str(state_path)) is False

        # 2. 429 happens -> enter fallback
        enter_fallback(str(usage_cache_path), str(state_path))
        assert is_fallback_active(str(state_path)) is True

        state = load_fallback_state(str(state_path))
        assert state["state"] == FallbackState.FALLBACK.value
        assert state["baseline_5h"] == 45.0
        assert state["baseline_7d"] == 30.0
        assert state["accumulated_cost"] == 0.0

        # 3. Accumulate costs from Claude API usage
        accumulate_cost(
            input_tokens=5000,
            output_tokens=2000,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            state_path=str(state_path),
        )

        # 4. Calculate synthetic utilization
        state = load_fallback_state(str(state_path))
        token_costs = load_token_costs(str(costs_path))
        result = calculate_synthetic(state, tier="5x", token_costs=token_costs)

        assert result["is_synthetic"] is True
        assert result["fallback_mode"] is True
        assert result["synthetic_5h"] >= 45.0  # Should be at least baseline
        assert result["synthetic_7d"] >= 30.0

        # 5. API recovers -> exit fallback
        exit_fallback(real_5h=47.0, real_7d=32.0, state_path=str(state_path))

        state = load_fallback_state(str(state_path))
        assert state["state"] == FallbackState.NORMAL.value
        assert state["accumulated_cost"] == 0.0
        assert state["baseline_5h"] is None

        assert is_fallback_active(str(state_path)) is False

    def test_state_persists_across_invocations(self, tmp_path):
        """State is correctly persisted and loaded across separate Python sessions."""
        from pacemaker.fallback import (
            enter_fallback,
            accumulate_cost,
            load_fallback_state,
            FallbackState,
        )

        usage_cache_path = self._write_usage_cache(tmp_path, 50.0, 25.0)
        state_path = tmp_path / "fallback_state.json"

        # Simulate first "session": enter fallback and accumulate some cost
        enter_fallback(str(usage_cache_path), str(state_path))
        accumulate_cost(
            input_tokens=10000,
            output_tokens=5000,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="opus",
            state_path=str(state_path),
        )

        # Read state after first "session"
        state_after_first = load_fallback_state(str(state_path))
        cost_after_first = state_after_first["accumulated_cost"]

        assert cost_after_first > 0.0
        assert state_after_first["state"] == FallbackState.FALLBACK.value

        # Simulate second "session": load existing state and add more cost
        # Use different token values to avoid dedup (real API turns always differ)
        accumulate_cost(
            input_tokens=10000,
            output_tokens=5001,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="opus",
            state_path=str(state_path),
        )

        state_after_second = load_fallback_state(str(state_path))
        cost_after_second = state_after_second["accumulated_cost"]

        # Cost should be cumulative (nearly doubled)
        assert cost_after_second == pytest.approx(cost_after_first * 2, rel=0.01)

    def test_synthetic_values_increase_with_cost(self, tmp_path):
        """Synthetic utilization increases monotonically as costs accumulate."""
        from pacemaker.fallback import (
            enter_fallback,
            accumulate_cost,
            calculate_synthetic,
            load_fallback_state,
            load_token_costs,
        )

        usage_cache_path = self._write_usage_cache(tmp_path, 30.0, 20.0)
        costs_path = self._write_token_costs(tmp_path)
        state_path = tmp_path / "fallback_state.json"
        enter_fallback(str(usage_cache_path), str(state_path))

        token_costs = load_token_costs(str(costs_path))

        # Measure synthetic values after each accumulation
        state = load_fallback_state(str(state_path))
        result0 = calculate_synthetic(state, tier="5x", token_costs=token_costs)

        accumulate_cost(
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            state_path=str(state_path),
        )
        state = load_fallback_state(str(state_path))
        result1 = calculate_synthetic(state, tier="5x", token_costs=token_costs)

        accumulate_cost(
            input_tokens=2000,
            output_tokens=1000,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            state_path=str(state_path),
        )
        state = load_fallback_state(str(state_path))
        result2 = calculate_synthetic(state, tier="5x", token_costs=token_costs)

        assert result1["synthetic_5h"] > result0["synthetic_5h"]
        assert result2["synthetic_5h"] > result1["synthetic_5h"]

    def test_multiple_enter_fallback_is_idempotent(self, tmp_path):
        """Calling enter_fallback multiple times does not reset accumulated cost."""
        from pacemaker.fallback import (
            enter_fallback,
            accumulate_cost,
            load_fallback_state,
        )

        usage_cache_path = self._write_usage_cache(tmp_path, 45.0, 30.0)
        state_path = tmp_path / "fallback_state.json"

        # Enter fallback
        enter_fallback(str(usage_cache_path), str(state_path))

        # Accumulate some cost
        accumulate_cost(
            input_tokens=5000,
            output_tokens=2000,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            state_path=str(state_path),
        )

        state_before = load_fallback_state(str(state_path))
        cost_before = state_before["accumulated_cost"]

        # Enter fallback again (simulating repeated 429s)
        enter_fallback(str(usage_cache_path), str(state_path))

        state_after = load_fallback_state(str(state_path))
        cost_after = state_after["accumulated_cost"]

        # Cost should NOT be reset
        assert cost_after == pytest.approx(cost_before, abs=0.0001)


class TestTokenCostsIntegration:
    """Integration tests for token_costs.json file loading."""

    def test_load_token_costs_from_real_config_dir(self):
        """load_token_costs can load from the project's config directory."""
        from pacemaker.fallback import load_token_costs

        # The config/token_costs.json is expected to exist in the project
        config_path = Path(__file__).parent.parent / "config" / "token_costs.json"

        if config_path.exists():
            result = load_token_costs(str(config_path))
            assert "5x" in result
            assert "20x" in result
            assert "coefficient_5h" in result["5x"]
        else:
            # File doesn't exist yet - load_token_costs should return defaults
            result = load_token_costs(str(config_path))
            assert "5x" in result
            assert "coefficient_5h" in result["5x"]

    def test_default_coefficients_produce_reasonable_values(self):
        """Default coefficients produce reasonable synthetic utilization values."""
        from pacemaker.fallback import (
            calculate_synthetic,
            load_token_costs,
            FallbackState,
        )

        # Load defaults (no file path that exists)
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            token_costs = load_token_costs(str(Path(tmpdir) / "token_costs.json"))

        state = {
            "state": FallbackState.FALLBACK.value,
            "baseline_5h": 45.0,
            "baseline_7d": 30.0,
            "accumulated_cost": 10.0,  # $10 of API usage
            "entered_at": time.time(),
        }

        result = calculate_synthetic(state, tier="5x", token_costs=token_costs)

        # With $10 API cost, synthetic_5h should be slightly above baseline
        assert result["synthetic_5h"] >= 45.0
        assert result["synthetic_5h"] <= 100.0
        assert result["synthetic_7d"] >= 30.0
        assert result["synthetic_7d"] <= 100.0
