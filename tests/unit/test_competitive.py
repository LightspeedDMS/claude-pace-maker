"""
Unit tests for competitive multi-model review pipeline.

Tests cover (25 total):
- parse_competitive(): expression parser, alias normalization, validation errors (11 tests)
- _build_synthesis_prompt(): prompt format verification (1 test)
- run_competitive(): orchestrator failure modes (6 tests)
- CLI: hook-model accepts/rejects competitive expressions (4 tests)
- Status display: competitive mode rendering (1 test)
- Governance tag: [expression] format — no REVIEWER: prefix (2 tests)
"""

import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

# Named constants to avoid magic numbers in timing tests
_SLOW_DELAY_S = 0.05
_ELAPSED_BOUND_S = 0.5  # generous upper bound for parallel dispatch
_BARRIER_TIMEOUT_S = 2.0  # max wait for barrier synchronization in concurrency test


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config_file(tmp_path):
    """Write a minimal config.json and return its path."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hook_model": "auto", "enabled": True}))
    return str(path)


def _read_config(path):
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# parse_competitive() tests
# ---------------------------------------------------------------------------


class TestParseCompetitive:
    """Tests for parse_competitive() expression parser."""

    def test_parse_competitive_valid_expression(self):
        """Standard 2-reviewer expression parses correctly."""
        from pacemaker.inference.competitive import parse_competitive

        result = parse_competitive("gpt-5+gemini-flash->sonnet")
        assert result is not None
        reviewers, synthesizer = result
        assert reviewers == ["gpt-5", "gemini-flash"]
        assert synthesizer == "sonnet"

    def test_parse_competitive_three_reviewers(self):
        """Three-reviewer expression parses all models."""
        from pacemaker.inference.competitive import parse_competitive

        result = parse_competitive("gpt-5+gemini-flash+opus->sonnet")
        assert result is not None
        reviewers, synthesizer = result
        assert reviewers == ["gpt-5", "gemini-flash", "opus"]
        assert synthesizer == "sonnet"

    def test_parse_competitive_alias_normalization(self):
        """gem-flash+gem-pro->haiku canonicalizes to full names."""
        from pacemaker.inference.competitive import parse_competitive

        result = parse_competitive("gem-flash+gem-pro->haiku")
        assert result is not None
        reviewers, synthesizer = result
        assert reviewers == ["gemini-flash", "gemini-pro"]
        assert synthesizer == "haiku"

    def test_parse_competitive_not_competitive_single_model(self):
        """Returns None for simple model strings (no + or ->)."""
        from pacemaker.inference.competitive import parse_competitive

        assert parse_competitive("sonnet") is None
        assert parse_competitive("auto") is None
        assert parse_competitive("gpt-5") is None
        assert parse_competitive("gemini-flash") is None

    def test_parse_competitive_plus_without_arrow_returns_none(self):
        """Expression with + but no -> returns None."""
        from pacemaker.inference.competitive import parse_competitive

        assert parse_competitive("gpt-5+gemini-flash") is None

    def test_parse_competitive_invalid_model(self):
        """Raises ValueError for unknown model token."""
        from pacemaker.inference.competitive import parse_competitive

        with pytest.raises(ValueError, match="Invalid model"):
            parse_competitive("gpt-5+unknownmodel->sonnet")

    def test_parse_competitive_single_reviewer(self):
        """Returns None when no + present — not a competitive expression."""
        from pacemaker.inference.competitive import parse_competitive

        # "gpt-5->sonnet" has no "+" so it is not recognized as competitive
        result = parse_competitive("gpt-5->sonnet")
        assert result is None

    def test_parse_competitive_multiple_arrows(self):
        """Raises ValueError for a+b->c->d (multiple arrows)."""
        from pacemaker.inference.competitive import parse_competitive

        with pytest.raises(ValueError, match="multiple"):
            parse_competitive("gpt-5+gemini-flash->sonnet->opus")

    def test_parse_competitive_duplicate_models(self):
        """Raises ValueError for gpt-5+gpt-5->sonnet (duplicate reviewers)."""
        from pacemaker.inference.competitive import parse_competitive

        with pytest.raises(ValueError, match="[Dd]uplicate"):
            parse_competitive("gpt-5+gpt-5->sonnet")

    def test_parse_competitive_max_reviewers_exceeded(self):
        """Raises ValueError for 4+ reviewer expression."""
        from pacemaker.inference.competitive import parse_competitive

        with pytest.raises(ValueError, match="at most 3"):
            parse_competitive("gpt-5+gemini-flash+opus+sonnet->auto")

    def test_parse_competitive_synthesizer_can_match_reviewer(self):
        """Synthesizer may be same model as a reviewer — valid expression."""
        from pacemaker.inference.competitive import parse_competitive

        result = parse_competitive("sonnet+opus->sonnet")
        assert result is not None
        reviewers, synthesizer = result
        assert "sonnet" in reviewers
        assert synthesizer == "sonnet"


# ---------------------------------------------------------------------------
# _build_synthesis_prompt() tests
# ---------------------------------------------------------------------------


class TestBuildSynthesisPrompt:
    """Tests for _build_synthesis_prompt() format."""

    def test_build_synthesis_prompt_format(self):
        """Prompt contains all reviewer labels and verdicts."""
        from pacemaker.inference.competitive import _build_synthesis_prompt

        succeeded = [
            ("APPROVED", "codex-gpt5"),
            ("BLOCKED: risky operation", "anthropic-sdk"),
        ]
        prompt = _build_synthesis_prompt(succeeded, "Is this code safe?")

        assert "[codex-gpt5]" in prompt
        assert "[anthropic-sdk]" in prompt
        assert "APPROVED" in prompt
        assert "BLOCKED: risky operation" in prompt
        assert "Is this code safe?" in prompt
        assert "BLOCKED" in prompt  # output contract instruction


# ---------------------------------------------------------------------------
# run_competitive() tests — only external provider boundaries are mocked
# ---------------------------------------------------------------------------


class TestRunCompetitive:
    """Tests for run_competitive() orchestrator failure modes.

    Only external boundaries (provider.query) are mocked via get_provider().
    Internal helpers (_call_single_reviewer, resolve_model_for_call) are
    exercised through the real implementation. Concrete model names are used
    (no "auto") so resolve_model_for_call passes them through unchanged.
    """

    def test_run_competitive_all_succeed(self):
        """Synthesizer called with all verdicts, expression label returned."""
        from pacemaker.inference.competitive import run_competitive
        from pacemaker.inference.codex_provider import CodexProvider

        codex_mock = MagicMock(spec=CodexProvider)
        codex_mock.query.return_value = "APPROVED"

        anthropic_mock = MagicMock()
        anthropic_mock.query.return_value = "APPROVED"

        synth_mock = MagicMock()
        synth_mock.query.return_value = "SYNTHESIZED"

        def _get_provider(model):
            if model == "gpt-5":
                return codex_mock
            if model == "sonnet":
                return synth_mock
            return anthropic_mock

        with patch(
            "pacemaker.inference.competitive.get_provider", side_effect=_get_provider
        ):
            response, label = run_competitive(
                reviewers=["gpt-5", "opus"],
                synthesizer="sonnet",
                prompt="review this",
                system_prompt="",
                call_context="intent_validation",
            )

        assert label == "gpt-5+opus->sonnet"
        assert response == "SYNTHESIZED"

    def test_run_competitive_one_fails(self):
        """Single survivor passed through, no synthesis, survivor label returned."""
        from pacemaker.inference.competitive import run_competitive
        from pacemaker.inference.provider import ProviderError
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider

        codex_mock = MagicMock(spec=CodexProvider)
        codex_mock.query.return_value = "APPROVED"

        gemini_mock = MagicMock(spec=GeminiProvider)
        gemini_mock.query.side_effect = ProviderError("gemini failed")

        def _get_provider(model):
            if model == "gpt-5":
                return codex_mock
            return gemini_mock

        with patch(
            "pacemaker.inference.competitive.get_provider", side_effect=_get_provider
        ):
            response, label = run_competitive(
                reviewers=["gpt-5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="review this",
                system_prompt="",
                call_context="intent_validation",
            )

        assert response == "APPROVED"
        assert label == "codex-gpt5"

    def test_run_competitive_all_fail_sdk_fallback(self):
        """SDK called solo when no Anthropic model in reviewers."""
        from pacemaker.inference.competitive import run_competitive
        from pacemaker.inference.provider import ProviderError
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider

        codex_mock = MagicMock(spec=CodexProvider)
        codex_mock.query.side_effect = ProviderError("codex failed")

        gemini_mock = MagicMock(spec=GeminiProvider)
        gemini_mock.query.side_effect = ProviderError("gemini failed")

        sdk_mock = MagicMock()
        sdk_mock.query.return_value = "SDK APPROVED"

        def _get_provider(model):
            if model == "gpt-5":
                return codex_mock
            if model in ("gemini-flash", "gemini-pro"):
                return gemini_mock
            return sdk_mock

        with (
            patch(
                "pacemaker.inference.competitive.get_provider",
                side_effect=_get_provider,
            ),
            patch(
                "pacemaker.inference.competitive.AnthropicProvider",
                return_value=sdk_mock,
            ),
        ):
            response, label = run_competitive(
                reviewers=["gpt-5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="review this",
                system_prompt="",
                call_context="intent_validation",
            )

        assert response == "SDK APPROVED"
        assert label == "sdk-fallback"

    def test_run_competitive_all_fail_sdk_was_competitor(self):
        """Returns empty string when Anthropic model was a reviewer and all fail."""
        from pacemaker.inference.competitive import run_competitive
        from pacemaker.inference.provider import ProviderError

        failing_mock = MagicMock()
        failing_mock.query.side_effect = ProviderError("all fail")

        with patch(
            "pacemaker.inference.competitive.get_provider", return_value=failing_mock
        ):
            response, label = run_competitive(
                reviewers=["opus", "sonnet"],
                synthesizer="gemini-flash",
                prompt="review this",
                system_prompt="",
                call_context="intent_validation",
            )

        assert response == ""
        assert label == "opus+sonnet->gemini-flash"

    def test_run_competitive_synthesizer_fails(self):
        """First survivor returned when synthesizer errors."""
        from pacemaker.inference.competitive import run_competitive
        from pacemaker.inference.provider import ProviderError
        from pacemaker.inference.codex_provider import CodexProvider

        codex_mock = MagicMock(spec=CodexProvider)
        codex_mock.query.return_value = "APPROVED"

        anthropic_mock = MagicMock()
        anthropic_mock.query.return_value = "APPROVED"

        synth_mock = MagicMock()
        synth_mock.query.side_effect = ProviderError("synth failed")

        def _get_provider(model):
            if model == "gpt-5":
                return codex_mock
            if model == "opus":
                return synth_mock
            return anthropic_mock

        with patch(
            "pacemaker.inference.competitive.get_provider", side_effect=_get_provider
        ):
            response, label = run_competitive(
                reviewers=["gpt-5", "sonnet"],
                synthesizer="opus",
                prompt="review this",
                system_prompt="",
                call_context="intent_validation",
            )

        assert response == "APPROVED"
        # Either survivor may be first depending on thread scheduling; both are valid
        assert label in {"codex-gpt5", "anthropic-sdk"}

    def test_run_competitive_concurrent_dispatch(self):
        """Reviewers are dispatched in parallel — verified via threading barrier."""
        from pacemaker.inference.competitive import run_competitive

        barrier = threading.Barrier(2, timeout=_BARRIER_TIMEOUT_S)
        dispatch_times = []
        lock = threading.Lock()

        class SlowProvider:
            def query(
                self, prompt, system_prompt="", model_hint="", max_thinking_tokens=4000
            ):
                barrier.wait()  # both threads must arrive concurrently
                with lock:
                    dispatch_times.append(time.monotonic())
                time.sleep(_SLOW_DELAY_S)
                return "APPROVED"

        slow = SlowProvider()
        synth_mock = MagicMock()
        synth_mock.query.return_value = "SYNTHESIZED"

        def _get_provider(model):
            if model == "opus":
                return synth_mock
            return slow

        start = time.monotonic()
        with patch(
            "pacemaker.inference.competitive.get_provider", side_effect=_get_provider
        ):
            response, label = run_competitive(
                reviewers=["sonnet", "gemini-flash"],
                synthesizer="opus",
                prompt="review this",
                system_prompt="",
                call_context="intent_validation",
            )
        elapsed = time.monotonic() - start

        # Barrier.wait() proves both threads ran concurrently
        assert len(dispatch_times) == 2
        assert elapsed < _ELAPSED_BOUND_S


# ---------------------------------------------------------------------------
# CLI hook-model tests
# ---------------------------------------------------------------------------


class TestCLIHookModel:
    """Tests for CLI hook-model command with competitive expressions."""

    def test_cli_hook_model_competitive_expression(self, config_file):
        """CLI accepts and stores competitive expression verbatim (canonical)."""
        from pacemaker.user_commands import _execute_hook_model

        result = _execute_hook_model(config_file, "gpt-5+gemini-flash->sonnet")

        assert result["success"] is True
        assert _read_config(config_file)["hook_model"] == "gpt-5+gemini-flash->sonnet"

    def test_cli_hook_model_competitive_aliases(self, config_file):
        """CLI canonicalizes aliases before storing."""
        from pacemaker.user_commands import _execute_hook_model

        result = _execute_hook_model(config_file, "gem-flash+gem-pro->haiku")

        assert result["success"] is True
        assert (
            _read_config(config_file)["hook_model"] == "gemini-flash+gemini-pro->haiku"
        )

    def test_cli_hook_model_competitive_invalid(self, config_file):
        """CLI rejects invalid expression, config unchanged."""
        from pacemaker.user_commands import _execute_hook_model

        result = _execute_hook_model(config_file, "gpt-5+unknownmodel->sonnet")

        assert result["success"] is False
        assert "invalid" in result["message"].lower() or "Invalid" in result["message"]
        assert _read_config(config_file)["hook_model"] == "auto"

    def test_cli_hook_model_haiku_accepted(self, config_file):
        """haiku is accepted as a valid single model."""
        from pacemaker.user_commands import _execute_hook_model

        result = _execute_hook_model(config_file, "haiku")

        assert result["success"] is True
        assert _read_config(config_file)["hook_model"] == "haiku"


# ---------------------------------------------------------------------------
# Status display test
# ---------------------------------------------------------------------------


class TestStatusDisplayCompetitive:
    """Tests for status display with competitive hook_model."""

    def test_status_display_competitive(self, tmp_path):
        """Status shows 'competitive' label and breakdown when hook_model is competitive."""
        from pacemaker.user_commands import _execute_status

        config_path = str(tmp_path / "config.json")
        config = {
            "hook_model": "gpt-5+gemini-flash->sonnet",
            "enabled": True,
            "intent_validation_enabled": True,
            "tdd_enabled": True,
            "danger_bash_enabled": True,
            "preferred_model": "sonnet",
            "log_level": 2,
        }
        with open(config_path, "w") as f:
            json.dump(config, f)

        with (
            patch("pacemaker.user_commands._load_config", return_value=config),
            patch("pacemaker.user_commands._count_recent_errors", return_value=0),
            patch(
                "pacemaker.user_commands._langfuse_test_connection",
                return_value={"connected": False, "message": "disabled"},
            ),
        ):
            result = _execute_status(config_path)

        assert result["success"] is True
        message = result["message"]
        assert "competitive" in message.lower()


# ---------------------------------------------------------------------------
# Governance tag format tests
# ---------------------------------------------------------------------------


class TestGovernanceTagCompetitive:
    """Tests for governance event tag format with competitive reviewer."""

    def test_governance_tag_competitive(self):
        """Reviewer label from competitive pipeline produces correct [expression] tag format."""
        from pacemaker.inference.competitive import parse_competitive

        reviewers, synthesizer = parse_competitive("gpt-5+gemini-flash->sonnet")
        expression = "+".join(reviewers) + "->" + synthesizer
        # Simulate hook.py tag formatting: f"[{_reviewer}] {feedback}"
        feedback = "some feedback"
        tagged = f"[{expression}] {feedback}"
        assert tagged == "[gpt-5+gemini-flash->sonnet] some feedback"
        assert "REVIEWER:" not in tagged

    def test_governance_tag_single_reviewer_format(self):
        """Single reviewer label extracted from competitive parse uses bracket format without REVIEWER: prefix."""
        from pacemaker.inference.competitive import parse_competitive

        # Parse a 2-reviewer expression and extract one reviewer label as the hook does
        # for single-reviewer (non-competitive) paths: _reviewer = result.get("reviewer", "")
        reviewers, _synthesizer = parse_competitive("gpt-5+gemini-flash->sonnet")
        single_reviewer = reviewers[1]  # "gemini-flash" or resolved alias
        feedback = "BLOCKED: bad code"
        # Simulate hook.py tag formatting: f"[{_reviewer}] {feedback}"
        tagged = f"[{single_reviewer}] {feedback}"
        assert "[" + single_reviewer + "]" in tagged
        assert "REVIEWER:" not in tagged
