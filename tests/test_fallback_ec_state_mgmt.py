#!/usr/bin/env python3
"""
Edge-case tests for fallback state management functions:
_default_state, load_fallback_state, save_fallback_state.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pacemaker.fallback import (
    _default_state,
    load_fallback_state,
    save_fallback_state,
)

import pytest


# ---------------------------------------------------------------------------
# _default_state
# ---------------------------------------------------------------------------
class TestDefaultState:
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
            "last_accumulated_usage",
        }
        assert set(state.keys()) == expected_keys

    def test_state_is_normal(self):
        assert _default_state()["state"] == "normal"

    def test_accumulated_cost_is_zero(self):
        assert _default_state()["accumulated_cost"] == 0.0

    def test_none_fields(self):
        state = _default_state()
        for key in [
            "baseline_5h",
            "baseline_7d",
            "resets_at_5h",
            "resets_at_7d",
            "rollover_cost_5h",
            "rollover_cost_7d",
            "last_rollover_resets_5h",
            "last_rollover_resets_7d",
            "tier",
            "entered_at",
        ]:
            assert state[key] is None, f"{key} should be None"

    def test_returns_new_dict_each_call(self):
        a = _default_state()
        b = _default_state()
        assert a is not b
        a["accumulated_cost"] = 999.0
        assert b["accumulated_cost"] == 0.0


# ---------------------------------------------------------------------------
# load_fallback_state
# ---------------------------------------------------------------------------
class TestLoadFallbackStateEdgeCases:
    def test_missing_file_returns_defaults(self, tmp_path):
        state = load_fallback_state(str(tmp_path / "nonexistent.json"))
        assert state["state"] == "normal"
        assert state["accumulated_cost"] == 0.0

    def test_empty_file_returns_defaults(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text("")
        state = load_fallback_state(str(p))
        assert state["state"] == "normal"

    def test_whitespace_only_file_returns_defaults(self, tmp_path):
        p = tmp_path / "ws.json"
        p.write_text("   \n\t  ")
        state = load_fallback_state(str(p))
        assert state["state"] == "normal"

    def test_corrupt_json_returns_defaults(self, tmp_path):
        p = tmp_path / "corrupt.json"
        p.write_text("{not valid json!!")
        state = load_fallback_state(str(p))
        assert state["state"] == "normal"

    def test_valid_json_missing_keys_gets_filled(self, tmp_path):
        p = tmp_path / "partial.json"
        p.write_text(json.dumps({"state": "fallback", "accumulated_cost": 5.0}))
        state = load_fallback_state(str(p))
        assert state["state"] == "fallback"
        assert state["accumulated_cost"] == 5.0
        assert state["baseline_5h"] is None
        assert state["rollover_cost_5h"] is None
        assert state["tier"] is None

    def test_valid_json_all_keys_returned_as_is(self, tmp_path):
        full = _default_state()
        full["state"] = "fallback"
        full["accumulated_cost"] = 42.0
        full["tier"] = "20x"
        p = tmp_path / "full.json"
        p.write_text(json.dumps(full))
        state = load_fallback_state(str(p))
        assert state == full

    def test_extra_unknown_keys_preserved(self, tmp_path):
        data = _default_state()
        data["unknown_future_key"] = "hello"
        p = tmp_path / "extra.json"
        p.write_text(json.dumps(data))
        state = load_fallback_state(str(p))
        assert state["unknown_future_key"] == "hello"

    def test_null_path_uses_default(self):
        state = load_fallback_state(None)
        assert "state" in state


# ---------------------------------------------------------------------------
# save_fallback_state
# ---------------------------------------------------------------------------
class TestSaveFallbackStateEdgeCases:
    def test_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "deep" / "nested" / "state.json"
        save_fallback_state({"state": "normal"}, str(p))
        assert p.exists()
        assert json.loads(p.read_text())["state"] == "normal"

    def test_writes_valid_json(self, tmp_path):
        state = _default_state()
        state["accumulated_cost"] = 123.456
        p = tmp_path / "state.json"
        save_fallback_state(state, str(p))
        loaded = json.loads(p.read_text())
        assert loaded["accumulated_cost"] == pytest.approx(123.456)

    def test_overwrites_existing(self, tmp_path):
        p = tmp_path / "state.json"
        save_fallback_state({"state": "fallback"}, str(p))
        save_fallback_state({"state": "normal"}, str(p))
        assert json.loads(p.read_text())["state"] == "normal"

    def test_roundtrip_with_load(self, tmp_path):
        original = _default_state()
        original["state"] = "fallback"
        original["accumulated_cost"] = 77.77
        original["tier"] = "20x"
        p = tmp_path / "state.json"
        save_fallback_state(original, str(p))
        loaded = load_fallback_state(str(p))
        assert loaded["state"] == "fallback"
        assert loaded["accumulated_cost"] == pytest.approx(77.77)
        assert loaded["tier"] == "20x"
