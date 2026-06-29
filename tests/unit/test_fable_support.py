"""Tests for Fable (Claude Fable 5) model support across pace-maker inference stack."""

import json
import os
import re
import tempfile

import pytest


# ---------------------------------------------------------------------------
# 1. KNOWN_MODELS contains "fable"
# ---------------------------------------------------------------------------
class TestFableInKnownModels:
    def test_fable_is_in_known_models(self):
        from pacemaker.inference.model_aliases import KNOWN_MODELS

        assert "fable" in KNOWN_MODELS


# ---------------------------------------------------------------------------
# 2. AnthropicProvider — _KNOWN_ALIASES and _FALLBACK_MAP
# ---------------------------------------------------------------------------
class TestAnthropicProviderFable:
    def test_fable_in_known_aliases(self):
        from pacemaker.inference.anthropic_provider import _KNOWN_ALIASES

        assert "fable" in _KNOWN_ALIASES

    def test_fable_has_fallback_in_fallback_map(self):
        from pacemaker.inference.anthropic_provider import _FALLBACK_MAP

        assert "fable" in _FALLBACK_MAP
        assert _FALLBACK_MAP["fable"] == "opus"

    def test_resolve_model_fable_returns_fable(self):
        """_resolve_model("fable") should pass through as known alias."""
        from pacemaker.inference.anthropic_provider import _resolve_model

        assert _resolve_model("fable") == "fable"


# ---------------------------------------------------------------------------
# 3. Registry — get_provider routes fable to AnthropicProvider
# ---------------------------------------------------------------------------
class TestRegistryFable:
    def test_fable_resolves_to_anthropic_provider(self):
        from pacemaker.inference.registry import get_provider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        provider = get_provider("fable")
        assert isinstance(provider, AnthropicProvider)


# ---------------------------------------------------------------------------
# 4. competitive.py — _ANTHROPIC_MODELS contains "fable"
# ---------------------------------------------------------------------------
class TestCompetitiveFable:
    def test_fable_in_anthropic_models(self):
        from pacemaker.inference.competitive import _ANTHROPIC_MODELS

        assert "fable" in _ANTHROPIC_MODELS

    def test_parse_competitive_with_fable_reviewer(self):
        from pacemaker.inference.competitive import parse_competitive

        reviewers, synthesizer = parse_competitive("fable+opus->sonnet")
        assert "fable" in reviewers
        assert synthesizer == "sonnet"

    def test_parse_competitive_with_fable_synthesizer(self):
        from pacemaker.inference.competitive import parse_competitive

        reviewers, synthesizer = parse_competitive("sonnet+opus->fable")
        assert synthesizer == "fable"

    def test_parse_competitive_fable_as_both_reviewer_and_synthesizer_rejected(self):
        """fable cannot appear twice (duplicate check)."""
        from pacemaker.inference.competitive import parse_competitive

        with pytest.raises(ValueError, match="Duplicate"):
            parse_competitive("fable+fable->sonnet")


# ---------------------------------------------------------------------------
# 5. user_commands.py — CLI regex patterns recognise "fable"
# ---------------------------------------------------------------------------
class TestUserCommandsFable:
    def test_prefer_model_regex_accepts_fable(self):
        """Pattern 19 prefer-model regex should match fable."""
        pattern = r"^pace-maker\s+prefer-model\s+(opus|sonnet|haiku|fable|auto)$"
        assert re.match(pattern, "pace-maker prefer-model fable") is not None

    def test_hook_model_regex_accepts_fable(self):
        """Pattern 20 hook-model single regex should match fable."""
        pattern = (
            r"^pace-maker\s+hook-model\s+"
            r"(auto|sonnet|opus|haiku|fable|gpt-5\.5|gpt-5\.4|gpt-5|gpt|codex"
            r"|gemini-flash|gemini-pro|gem-flash|gem-pro)$"
        )
        assert re.match(pattern, "pace-maker hook-model fable") is not None

    def test_execute_hook_model_accepts_fable(self):
        """_execute_hook_model should write fable to config."""
        from pacemaker.user_commands import _execute_hook_model

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"hook_model": "auto"}, f)
            config_path = f.name
        try:
            result = _execute_hook_model(config_path, "fable")
            assert result["success"] is True
            with open(config_path) as f:
                config = json.load(f)
            assert config["hook_model"] == "fable"
        finally:
            os.unlink(config_path)

    def test_execute_prefer_model_accepts_fable(self):
        """_execute_prefer_model should write fable to config."""
        from pacemaker.user_commands import _execute_prefer_model

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"preferred_subagent_model": "auto"}, f)
            config_path = f.name
        try:
            result = _execute_prefer_model(config_path, "fable")
            assert result["success"] is True
            with open(config_path) as f:
                config = json.load(f)
            assert config["preferred_subagent_model"] == "fable"
        finally:
            os.unlink(config_path)
