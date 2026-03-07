#!/usr/bin/env python3
"""
Edge-case tests for token cost loading, tier detection, and cost accumulation:
load_token_costs, detect_tier, accumulate_cost.
"""

import json
import time
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pacemaker.fallback import (
    _default_state,
    load_fallback_state,
    save_fallback_state,
    load_token_costs,
    detect_tier,
    accumulate_cost,
)


# ---------------------------------------------------------------------------
# load_token_costs
# ---------------------------------------------------------------------------
class TestLoadTokenCostsEdgeCases:
    def test_missing_file_returns_defaults(self, tmp_path):
        result = load_token_costs(str(tmp_path / "nope.json"))
        assert result["5x"]["coefficient_5h"] == pytest.approx(0.0075)
        assert result["20x"]["coefficient_5h"] == pytest.approx(0.001875)

    def test_empty_file_returns_defaults(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text("")
        result = load_token_costs(str(p))
        assert "5x" in result

    def test_corrupt_json_returns_defaults(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{nope")
        result = load_token_costs(str(p))
        assert "5x" in result

    def test_missing_tier_keys_returns_defaults(self, tmp_path):
        p = tmp_path / "partial.json"
        p.write_text(json.dumps({"5x": {"coefficient_5h": 0.01}}))
        result = load_token_costs(str(p))
        assert result["5x"]["coefficient_5h"] == pytest.approx(0.0075)

    def test_valid_file_loaded(self, tmp_path):
        data = {
            "5x": {"coefficient_5h": 0.01, "coefficient_7d": 0.002},
            "20x": {"coefficient_5h": 0.005, "coefficient_7d": 0.001},
        }
        p = tmp_path / "costs.json"
        p.write_text(json.dumps(data))
        result = load_token_costs(str(p))
        assert result["5x"]["coefficient_5h"] == pytest.approx(0.01)
        assert result["20x"]["coefficient_7d"] == pytest.approx(0.001)

    def test_null_path_uses_default(self):
        result = load_token_costs(None)
        assert "5x" in result
        assert "20x" in result


# ---------------------------------------------------------------------------
# detect_tier
# ---------------------------------------------------------------------------
class TestDetectTierEdgeCases:
    def test_none_profile(self):
        assert detect_tier(None) == "5x"

    def test_empty_dict(self):
        assert detect_tier({}) == "5x"

    def test_no_account_key(self):
        assert detect_tier({"other": "data"}) == "5x"

    def test_account_none(self):
        assert detect_tier({"account": None}) == "5x"

    def test_has_claude_max_true(self):
        assert detect_tier({"account": {"has_claude_max": True}}) == "20x"

    def test_has_claude_max_false(self):
        assert detect_tier({"account": {"has_claude_max": False}}) == "5x"

    def test_missing_has_claude_max(self):
        assert detect_tier({"account": {"email": "test@test.com"}}) == "5x"


# ---------------------------------------------------------------------------
# accumulate_cost
# ---------------------------------------------------------------------------
class TestAccumulateCostEdgeCases:
    def _enter(self, tmp_path):
        sp = str(tmp_path / "state.json")
        state = _default_state()
        state["state"] = "fallback"
        state["entered_at"] = time.time()
        save_fallback_state(state, sp)
        return sp

    def test_opus_input_pricing(self, tmp_path):
        sp = self._enter(tmp_path)
        accumulate_cost(1_000_000, 0, 0, 0, "opus", sp)
        state = load_fallback_state(sp)
        assert state["accumulated_cost"] == pytest.approx(15.0)

    def test_sonnet_input_pricing(self, tmp_path):
        sp = self._enter(tmp_path)
        accumulate_cost(1_000_000, 0, 0, 0, "sonnet", sp)
        state = load_fallback_state(sp)
        assert state["accumulated_cost"] == pytest.approx(3.0)

    def test_haiku_input_pricing(self, tmp_path):
        sp = self._enter(tmp_path)
        accumulate_cost(1_000_000, 0, 0, 0, "haiku", sp)
        state = load_fallback_state(sp)
        assert state["accumulated_cost"] == pytest.approx(0.80)

    def test_output_tokens_opus(self, tmp_path):
        sp = self._enter(tmp_path)
        accumulate_cost(0, 1_000_000, 0, 0, "opus", sp)
        state = load_fallback_state(sp)
        assert state["accumulated_cost"] == pytest.approx(75.0)

    def test_cache_read_tokens(self, tmp_path):
        sp = self._enter(tmp_path)
        accumulate_cost(0, 0, 1_000_000, 0, "opus", sp)
        state = load_fallback_state(sp)
        assert state["accumulated_cost"] == pytest.approx(1.50)

    def test_cache_create_tokens(self, tmp_path):
        sp = self._enter(tmp_path)
        accumulate_cost(0, 0, 0, 1_000_000, "opus", sp)
        state = load_fallback_state(sp)
        assert state["accumulated_cost"] == pytest.approx(18.75)

    def test_mixed_tokens_sonnet(self, tmp_path):
        sp = self._enter(tmp_path)
        accumulate_cost(500_000, 100_000, 200_000, 50_000, "sonnet", sp)
        state = load_fallback_state(sp)
        expected = (
            500_000 * 3.0 / 1_000_000
            + 100_000 * 15.0 / 1_000_000
            + 200_000 * 0.30 / 1_000_000
            + 50_000 * 3.75 / 1_000_000
        )
        assert state["accumulated_cost"] == pytest.approx(expected)

    def test_zero_tokens_adds_nothing(self, tmp_path):
        sp = self._enter(tmp_path)
        accumulate_cost(0, 0, 0, 0, "opus", sp)
        state = load_fallback_state(sp)
        assert state["accumulated_cost"] == pytest.approx(0.0)

    def test_unknown_model_falls_back_to_sonnet(self, tmp_path):
        sp = self._enter(tmp_path)
        accumulate_cost(1_000_000, 0, 0, 0, "gpt-5", sp)
        state = load_fallback_state(sp)
        assert state["accumulated_cost"] == pytest.approx(3.0)

    def test_noop_when_normal(self, tmp_path):
        sp = str(tmp_path / "state.json")
        save_fallback_state(_default_state(), sp)
        accumulate_cost(1_000_000, 1_000_000, 0, 0, "opus", sp)
        result = load_fallback_state(sp)
        assert result["accumulated_cost"] == 0.0

    def test_cumulative_across_calls(self, tmp_path):
        sp = self._enter(tmp_path)
        accumulate_cost(1_000_000, 0, 0, 0, "sonnet", sp)  # $3
        accumulate_cost(0, 1_000_000, 0, 0, "sonnet", sp)  # $15
        accumulate_cost(1_000_000, 500_000, 0, 0, "sonnet", sp)  # $3 + $7.5
        state = load_fallback_state(sp)
        assert state["accumulated_cost"] == pytest.approx(28.5)

    def test_mixed_model_families(self, tmp_path):
        sp = self._enter(tmp_path)
        accumulate_cost(1_000_000, 0, 0, 0, "opus", sp)
        accumulate_cost(1_000_000, 0, 0, 0, "sonnet", sp)
        accumulate_cost(1_000_000, 0, 0, 0, "haiku", sp)
        state = load_fallback_state(sp)
        assert state["accumulated_cost"] == pytest.approx(18.80)

    def test_large_token_count(self, tmp_path):
        sp = self._enter(tmp_path)
        accumulate_cost(100_000_000, 0, 0, 0, "opus", sp)
        state = load_fallback_state(sp)
        assert state["accumulated_cost"] == pytest.approx(1500.0)

    def test_case_insensitive_model_family(self, tmp_path):
        sp = self._enter(tmp_path)
        accumulate_cost(1_000_000, 0, 0, 0, "OPUS", sp)
        state = load_fallback_state(sp)
        assert state["accumulated_cost"] == pytest.approx(15.0)
