"""Tests for random selection and sequential failover hook model expressions (Story #67)."""

import os
import json
import tempfile

import pytest
from unittest.mock import patch, MagicMock

from pacemaker.inference.random_failover import (
    parse_random,
    parse_failover,
    run_random,
    run_failover,
)
from pacemaker.inference.provider import ProviderError


@pytest.fixture
def tmp_config():
    """Create a temp config file, yield its path, clean up after."""
    path = None

    def _make(data=None):
        nonlocal path
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data or {"hook_model": "auto"}, f)
            path = f.name
        return path

    yield _make
    if path and os.path.exists(path):
        os.unlink(path)


class TestParseRandom:
    def test_valid_two_models(self):
        assert parse_random("sonnet*opus") == ["sonnet", "opus"]

    def test_valid_three_models(self):
        assert parse_random("sonnet*opus*haiku") == ["sonnet", "opus", "haiku"]

    def test_alias_canonicalization(self):
        assert parse_random("gpt-5*gem-flash") == ["gpt-5.5", "gemini-flash"]

    def test_codex_alias(self):
        assert parse_random("codex*sonnet") == ["gpt-5.5", "sonnet"]

    def test_rejects_duplicates(self):
        with pytest.raises(ValueError, match="Duplicate"):
            parse_random("sonnet*sonnet")

    def test_rejects_alias_duplicates(self):
        with pytest.raises(ValueError, match="Duplicate"):
            parse_random("gpt-5*gpt-5.5")

    def test_rejects_unknown_model(self):
        with pytest.raises(ValueError, match="Invalid model"):
            parse_random("sonnet*unknown-model")

    def test_returns_none_for_single_model(self):
        assert parse_random("sonnet") is None

    def test_rejects_trailing_operator(self):
        with pytest.raises(ValueError, match="requires at least 2"):
            parse_random("sonnet*")

    def test_rejects_mixed_with_pipe(self):
        with pytest.raises(ValueError, match="Cannot mix"):
            parse_random("sonnet*opus|haiku")

    def test_rejects_mixed_with_plus(self):
        with pytest.raises(ValueError, match="Cannot mix"):
            parse_random("sonnet*opus+haiku")

    def test_rejects_mixed_with_arrow(self):
        with pytest.raises(ValueError, match="Cannot mix"):
            parse_random("sonnet*opus->haiku")

    def test_returns_none_for_empty(self):
        assert parse_random("") is None

    def test_returns_none_for_non_string(self):
        assert parse_random(None) is None
        assert parse_random(123) is None


class TestParseFailover:
    def test_valid_two_models(self):
        assert parse_failover("sonnet|opus") == ["sonnet", "opus"]

    def test_valid_three_models(self):
        assert parse_failover("gpt-5.4|gemini-flash|sonnet") == [
            "gpt-5.4",
            "gemini-flash",
            "sonnet",
        ]

    def test_alias_canonicalization(self):
        assert parse_failover("gem-pro|gpt-5|opus") == ["gemini-pro", "gpt-5.5", "opus"]

    def test_rejects_duplicates(self):
        with pytest.raises(ValueError, match="Duplicate"):
            parse_failover("sonnet|sonnet|opus")

    def test_rejects_unknown_model(self):
        with pytest.raises(ValueError, match="Invalid model"):
            parse_failover("sonnet|bad-model")

    def test_returns_none_for_single_model(self):
        assert parse_failover("sonnet") is None

    def test_rejects_trailing_operator(self):
        with pytest.raises(ValueError, match="requires at least 2"):
            parse_failover("sonnet|")

    def test_rejects_mixed_with_star(self):
        with pytest.raises(ValueError, match="Cannot mix"):
            parse_failover("sonnet|opus*haiku")

    def test_rejects_mixed_with_plus(self):
        with pytest.raises(ValueError, match="Cannot mix"):
            parse_failover("sonnet|opus+haiku")

    def test_rejects_mixed_with_arrow(self):
        with pytest.raises(ValueError, match="Cannot mix"):
            parse_failover("sonnet|opus->haiku")

    def test_returns_none_for_empty(self):
        assert parse_failover("") is None

    def test_returns_none_for_non_string(self):
        assert parse_failover(None) is None


class TestRunRandom:
    def test_dispatches_to_chosen_model(self):
        mock_provider = MagicMock()
        mock_provider.query.return_value = "APPROVED"
        with (
            patch(
                "pacemaker.inference.random_failover.get_provider",
                return_value=mock_provider,
            ),
            patch(
                "pacemaker.inference.random_failover._random.choice",
                return_value="sonnet",
            ),
        ):
            result, reviewer = run_random(
                ["sonnet", "opus"], "prompt", "system", "intent_validation"
            )
        assert result == "APPROVED"
        assert reviewer == "anthropic-sdk"

    def test_sdk_fallback_on_provider_error(self):
        failing = MagicMock()
        failing.query.side_effect = ProviderError("down")
        fallback = MagicMock()
        fallback.query.return_value = "SDK_RESPONSE"
        with (
            patch(
                "pacemaker.inference.random_failover.get_provider",
                return_value=failing,
            ),
            patch(
                "pacemaker.inference.random_failover._random.choice",
                return_value="gpt-5.4",
            ),
            patch(
                "pacemaker.inference.anthropic_provider.AnthropicProvider",
                return_value=fallback,
            ),
        ):
            result, reviewer = run_random(
                ["gpt-5.4", "sonnet"], "prompt", "system", "intent_validation"
            )
        assert result == "SDK_RESPONSE"
        assert reviewer == "anthropic-sdk"

    def test_sdk_fallback_on_timeout(self):
        failing = MagicMock()
        failing.query.side_effect = TimeoutError("timed out")
        fallback = MagicMock()
        fallback.query.return_value = "SDK_RESPONSE"
        with (
            patch(
                "pacemaker.inference.random_failover.get_provider",
                return_value=failing,
            ),
            patch(
                "pacemaker.inference.random_failover._random.choice",
                return_value="sonnet",
            ),
            patch(
                "pacemaker.inference.anthropic_provider.AnthropicProvider",
                return_value=fallback,
            ),
        ):
            result, reviewer = run_random(
                ["sonnet", "opus"], "prompt", "system", "intent_validation"
            )
        assert result == "SDK_RESPONSE"
        assert reviewer == "anthropic-sdk"

    def test_rejects_empty_models(self):
        with pytest.raises(ValueError, match="non-empty"):
            run_random([], "prompt", "system", "intent_validation")


class TestRunFailover:
    def test_primary_succeeds(self):
        mock_provider = MagicMock()
        mock_provider.query.return_value = "APPROVED"
        with patch(
            "pacemaker.inference.random_failover.get_provider",
            return_value=mock_provider,
        ):
            result, reviewer = run_failover(
                ["sonnet", "opus"], "prompt", "system", "intent_validation"
            )
        assert result == "APPROVED"
        assert reviewer == "anthropic-sdk"
        assert mock_provider.query.call_count == 1

    def test_advances_on_primary_failure(self):
        fail_p = MagicMock()
        fail_p.query.side_effect = ProviderError("down")
        ok_p = MagicMock()
        ok_p.query.return_value = "SECOND_OK"
        calls = {"n": 0}

        def fake_get(model):
            calls["n"] += 1
            return fail_p if calls["n"] == 1 else ok_p

        with patch(
            "pacemaker.inference.random_failover.get_provider", side_effect=fake_get
        ):
            result, reviewer = run_failover(
                ["gpt-5.4", "sonnet"], "prompt", "system", "intent_validation"
            )
        assert result == "SECOND_OK"

    def test_sdk_fallback_when_all_fail(self):
        fail_p = MagicMock()
        fail_p.query.side_effect = ProviderError("down")
        fallback = MagicMock()
        fallback.query.return_value = "SDK_LAST_RESORT"
        with (
            patch(
                "pacemaker.inference.random_failover.get_provider", return_value=fail_p
            ),
            patch(
                "pacemaker.inference.anthropic_provider.AnthropicProvider",
                return_value=fallback,
            ),
        ):
            result, reviewer = run_failover(
                ["gpt-5.4", "gemini-flash"], "prompt", "system", "intent_validation"
            )
        assert result == "SDK_LAST_RESORT"
        assert reviewer == "anthropic-sdk"

    def test_advances_on_timeout(self):
        timeout_p = MagicMock()
        timeout_p.query.side_effect = TimeoutError("slow")
        ok_p = MagicMock()
        ok_p.query.return_value = "OK"
        calls = {"n": 0}

        def fake_get(model):
            calls["n"] += 1
            return timeout_p if calls["n"] == 1 else ok_p

        with patch(
            "pacemaker.inference.random_failover.get_provider", side_effect=fake_get
        ):
            result, _ = run_failover(
                ["gpt-5.4", "sonnet"], "prompt", "system", "intent_validation"
            )
        assert result == "OK"

    def test_rejects_empty_models(self):
        with pytest.raises(ValueError, match="non-empty"):
            run_failover([], "prompt", "system", "intent_validation")


class TestRegistryRouting:
    def test_invalid_random_falls_back_to_auto(self):
        mock_p = MagicMock()
        mock_p.query.return_value = "AUTO_OK"
        with patch("pacemaker.inference.registry.get_provider", return_value=mock_p):
            from pacemaker.inference.registry import resolve_and_call_with_reviewer

            result, _ = resolve_and_call_with_reviewer(
                "sonnet*unknown", "prompt", "system", "intent_validation"
            )
        assert result == "AUTO_OK"


class TestCLIIntegration:
    def test_mixed_operators_rejected(self, tmp_config):
        from pacemaker.user_commands import execute_command

        path = tmp_config()
        result = execute_command("hook-model", path, subcommand="sonnet*opus|haiku")
        assert result["success"] is False
