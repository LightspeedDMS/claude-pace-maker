"""
Unit tests for the mechanical N-verifier review pipeline (Story #77).

Tests cover:
- parse_competitive(): expression parser (migrated, unchanged behavior)
- run_mechanical(): mechanical decision engine (Story #77 behavior)
  - Truth table N=2 and N=3
  - Synthesizer-cannot-flip (core safety test — THE heart of Story #77)
  - Synthesizer NOT called when all pass
  - Synthesizer NOT called with single failure
  - Synthesizer error/timeout/empty → concatenated raw feedback fallback
  - Pre-tool fail-closed: missing verifier → BLOCKED
  - Zero survivors → empty string (gate semantics drive fail-open/closed)
  - Stop-gate matrix: all pass, one fail, zero survivors, COMPLETE:, BLOCKED: wins
  - Concurrent dispatch (parallelism verification)
- _call_single_reviewer(): AgyProvider label fix (Story #77 correction)
- CLI tests (migrated — unchanged behavior)
- Status display (migrated — unchanged behavior)
- Governance tag format (migrated — unchanged behavior)
- gpt-5.4 and gpt-5.5 support tests (migrated — unchanged behavior)

Mock target: pacemaker.inference.competitive.get_provider (never the registry submodule)
"""

import threading
import time
import json
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
# parse_competitive() tests (migrated — unchanged behavior)
# ---------------------------------------------------------------------------


class TestParseCompetitive:
    """Tests for parse_competitive() expression parser."""

    def test_parse_competitive_valid_expression(self):
        """Standard 2-reviewer expression parses correctly; gpt-5 alias resolves to gpt-5.6-sol."""
        from pacemaker.inference.competitive import parse_competitive

        result = parse_competitive("gpt-5+gemini-flash->sonnet")
        assert result is not None
        reviewers, synthesizer = result
        assert reviewers == ["gpt-5.6-sol", "gemini-flash"]
        assert synthesizer == "sonnet"

    def test_parse_competitive_three_reviewers(self):
        """Three-reviewer expression parses all models; gpt-5 alias resolves to gpt-5.6-sol."""
        from pacemaker.inference.competitive import parse_competitive

        result = parse_competitive("gpt-5+gemini-flash+opus->sonnet")
        assert result is not None
        reviewers, synthesizer = result
        assert reviewers == ["gpt-5.6-sol", "gemini-flash", "opus"]
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
# run_mechanical() tests — core Story #77 behavior
# ---------------------------------------------------------------------------


class TestRunMechanical:
    """Tests for run_mechanical() N-verifier mechanical decision engine.

    Mock target: pacemaker.inference.competitive.get_provider
    Provider specs drive label assignment:
    - MagicMock(spec=CodexProvider) + model "gpt-5.5" → label "codex-gpt5"
    - MagicMock(spec=GeminiProvider) + model "gemini-flash" → label "gem-flash"
    - MagicMock(spec=AnthropicProvider) → label "anthropic-sdk"
    - MagicMock(spec=AgyProvider) + model "agy-flash" → label "agy-flash"
    """

    # ---- Truth table N=2 ----

    def test_n2_all_pass_returns_approved(self):
        """N=2: both verifiers pass → APPROVED; synthesizer NOT called."""
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "APPROVED"
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.return_value = "APPROVED"
        synth = MagicMock(spec=AnthropicProvider)
        synth.query.return_value = "should not be called"

        def _get(model):
            if model == "gpt-5.5":
                return v1
            if model == "gemini-flash":
                return v2
            return synth

        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="review this",
                system_prompt="",
                call_context="intent_validation",
            )

        assert response == "APPROVED"
        assert label == "gpt-5.5+gemini-flash->sonnet"
        synth.query.assert_not_called()

    def test_n2_pass_fail_returns_blocked_raw_message(self):
        """N=2: one passes, one fails → BLOCKED with raw failing message; synthesizer NOT called."""
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "APPROVED"
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.return_value = "BLOCKED: unsafe shell command"
        synth = MagicMock(spec=AnthropicProvider)

        def _get(model):
            if model == "gpt-5.5":
                return v1
            if model == "gemini-flash":
                return v2
            return synth

        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="review this",
                system_prompt="",
                call_context="intent_validation",
            )

        assert response.startswith("BLOCKED:")
        assert "unsafe shell command" in response
        assert label == "gpt-5.5+gemini-flash->sonnet"
        synth.query.assert_not_called()

    def test_n2_all_fail_calls_synthesizer_for_message(self):
        """N=2: both fail → synthesizer IS called to format message; result is BLOCKED."""
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "BLOCKED: concern A"
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.return_value = "BLOCKED: concern B"
        synth = MagicMock(spec=AnthropicProvider)
        synth.query.return_value = "concern A and B combined"

        def _get(model):
            if model == "gpt-5.5":
                return v1
            if model == "gemini-flash":
                return v2
            return synth  # synthesizer

        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="review this",
                system_prompt="",
                call_context="intent_validation",
            )

        assert response.startswith("BLOCKED:")
        assert "concern A and B combined" in response
        assert label == "gpt-5.5+gemini-flash->sonnet"
        synth.query.assert_called_once()

    # ---- Core safety test: synthesizer-cannot-flip ----

    def test_synthesizer_cannot_flip_blocked_to_approved(self):
        """CORE SAFETY TEST: synthesizer returning APPROVED cannot flip a BLOCKED decision.

        This proves the demoted synthesizer cannot override a BLOCK verdict.
        The 'BLOCKED:' prefix is applied mechanically in code — the synthesizer
        output is only used as the message body.
        """
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "BLOCKED: dangerous operation detected"
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.return_value = "BLOCKED: violates safety policy"
        synth = MagicMock(spec=AnthropicProvider)
        synth.query.return_value = "APPROVED"  # synthesizer tries to flip!

        def _get(model):
            if model == "gpt-5.5":
                return v1
            if model == "gemini-flash":
                return v2
            return synth  # synthesizer (sonnet)

        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="delete /usr/local",
                system_prompt="",
                call_context="intent_validation",
            )

        # CRITICAL: must still be BLOCKED despite synthesizer returning APPROVED
        assert response.startswith(
            "BLOCKED:"
        ), f"Synthesizer-cannot-flip VIOLATED: expected BLOCKED:..., got {response!r}"
        assert label == "gpt-5.5+gemini-flash->sonnet"
        # Synthesizer WAS called (to format the message body)
        synth.query.assert_called_once()

    # ---- Synthesizer invocation guards ----

    def test_synthesizer_not_called_when_all_pass(self):
        """Synthesizer mock .query is NOT called when all verifiers pass."""
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "APPROVED"
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.return_value = "APPROVED"
        synth = MagicMock(spec=AnthropicProvider)

        def _get(model):
            if model == "gpt-5.5":
                return v1
            if model == "gemini-flash":
                return v2
            return synth

        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="p",
                system_prompt="",
                call_context="intent_validation",
            )

        synth.query.assert_not_called()

    def test_single_failing_verifier_skips_synthesizer(self):
        """Exactly 1 failing verifier: synthesizer NOT called; raw message used."""
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "APPROVED"
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.return_value = "BLOCKED: risky deletion detected"
        synth = MagicMock(spec=AnthropicProvider)

        def _get(model):
            if model == "gpt-5.5":
                return v1
            if model == "gemini-flash":
                return v2
            return synth

        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, _label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="p",
                system_prompt="",
                call_context="intent_validation",
            )

        # Synthesizer NOT called (single failure → raw message path)
        synth.query.assert_not_called()
        assert response.startswith("BLOCKED:")
        assert "risky deletion detected" in response

    # ---- Issue #88: double "BLOCKED: BLOCKED:" prefix regression ----

    def test_single_failing_verifier_no_double_blocked_prefix(self):
        """Issue #88: verifier's raw response already starts with 'BLOCKED:' —
        the final result must have exactly ONE 'BLOCKED:' prefix, not two.
        """
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "APPROVED"
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.return_value = "BLOCKED: some reason"
        synth = MagicMock(spec=AnthropicProvider)

        def _get(model):
            if model == "gpt-5.5":
                return v1
            if model == "gemini-flash":
                return v2
            return synth

        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, _label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="p",
                system_prompt="",
                call_context="intent_validation",
            )

        assert response == "BLOCKED: some reason"
        assert "BLOCKED: BLOCKED:" not in response
        synth.query.assert_not_called()

    def test_single_failing_verifier_case_insensitive_no_double_prefix(self):
        """Issue #88: verifier's raw response starts with lowercase 'blocked:' —
        stripping must be case-insensitive, and the result must have exactly
        one (canonically-cased) 'BLOCKED:' prefix.
        """
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "APPROVED"
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.return_value = "blocked: reason"
        synth = MagicMock(spec=AnthropicProvider)

        def _get(model):
            if model == "gpt-5.5":
                return v1
            if model == "gemini-flash":
                return v2
            return synth

        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, _label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="p",
                system_prompt="",
                call_context="intent_validation",
            )

        assert response == "BLOCKED: reason"
        assert "blocked:" not in response.lower().replace("blocked: reason", "")
        synth.query.assert_not_called()

    def test_synthesizer_message_with_blocked_prefix_not_doubled(self):
        """Issue #88 defensive case: 2+ failing verifiers, synthesizer's formatted
        message itself happens to start with 'BLOCKED:' — must not double up.
        """
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "BLOCKED: concern A"
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.return_value = "BLOCKED: concern B"
        synth = MagicMock(spec=AnthropicProvider)
        synth.query.return_value = "BLOCKED: concern A and B combined"

        def _get(model):
            if model == "gpt-5.5":
                return v1
            if model == "gemini-flash":
                return v2
            return synth

        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, _label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="p",
                system_prompt="",
                call_context="intent_validation",
            )

        assert response == "BLOCKED: concern A and B combined"
        assert "BLOCKED: BLOCKED:" not in response
        synth.query.assert_called_once()

    # ---- Synthesizer failure fallback ----

    def test_synthesizer_error_falls_back_to_concat(self):
        """Synthesizer raises exception → concatenated raw feedbacks; still BLOCKED:."""
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider
        from pacemaker.inference.provider import ProviderError

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "BLOCKED: concern X"
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.return_value = "BLOCKED: concern Y"
        synth = MagicMock(spec=AnthropicProvider)
        synth.query.side_effect = ProviderError("synthesizer unavailable")

        def _get(model):
            if model == "gpt-5.5":
                return v1
            if model == "gemini-flash":
                return v2
            return synth

        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="p",
                system_prompt="",
                call_context="intent_validation",
            )

        assert response.startswith("BLOCKED:")
        # Concatenated raw feedbacks should be present
        assert "concern X" in response or "concern Y" in response
        assert label == "gpt-5.5+gemini-flash->sonnet"

    def test_synthesizer_timeout_falls_back_to_concat(self):
        """Synthesizer timeout → concatenated raw feedbacks; still BLOCKED:."""
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        _SYNTH_SLEEP_S = 3  # Must be > patched timeout of 1s

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "BLOCKED: concern X"
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.return_value = "BLOCKED: concern Y"

        def _slow_synth(*args, **kwargs):
            time.sleep(_SYNTH_SLEEP_S)
            return "SHOULD NOT REACH"

        synth = MagicMock(spec=AnthropicProvider)
        synth.query.side_effect = _slow_synth

        def _get(model):
            if model == "gpt-5.5":
                return v1
            if model == "gemini-flash":
                return v2
            return synth

        with (
            patch("pacemaker.inference.competitive.get_provider", side_effect=_get),
            patch("pacemaker.inference.competitive.SYNTHESIS_TIMEOUT_SEC", 1),
        ):
            response, label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="p",
                system_prompt="",
                call_context="intent_validation",
            )

        assert response.startswith("BLOCKED:")
        assert "concern X" in response or "concern Y" in response

    def test_synthesizer_empty_response_falls_back_to_concat(self):
        """Synthesizer returns empty string → concatenated raw feedbacks; still BLOCKED:."""
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "BLOCKED: concern X"
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.return_value = "BLOCKED: concern Y"
        synth = MagicMock(spec=AnthropicProvider)
        synth.query.return_value = ""  # empty response

        def _get(model):
            if model == "gpt-5.5":
                return v1
            if model == "gemini-flash":
                return v2
            return synth

        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="p",
                system_prompt="",
                call_context="intent_validation",
            )

        assert response.startswith("BLOCKED:")
        assert "concern X" in response or "concern Y" in response

    # ---- Pre-tool fail-closed ----

    def test_pretool_missing_verifier_infra_fail_blocked(self):
        """Pre-tool: one verifier raises ProviderError → BLOCKED (fail-closed).

        The present verifier passed (APPROVED) but the missing one causes fail-closed.
        Message: 'a required verifier did not respond (fail-closed)'.
        """
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider
        from pacemaker.inference.provider import ProviderError

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "APPROVED"  # passes! but other verifier missing
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.side_effect = ProviderError("gemini unavailable")

        def _get(model):
            if model == "gpt-5.5":
                return v1
            return v2

        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="p",
                system_prompt="",
                call_context="intent_validation",
            )

        assert response.startswith("BLOCKED:")
        assert "did not respond" in response or "fail-closed" in response
        assert label == "gpt-5.5+gemini-flash->sonnet"

    def test_pretool_zero_survivors_returns_empty_string(self):
        """Pre-tool: zero survivors → empty string (verdict_passes('') = False → gate blocks)."""
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.provider import ProviderError

        failing = MagicMock()
        failing.query.side_effect = ProviderError("all down")

        with patch(
            "pacemaker.inference.competitive.get_provider", return_value=failing
        ):
            response, label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="p",
                system_prompt="",
                call_context="intent_validation",
            )

        assert response == ""
        assert label == "gpt-5.5+gemini-flash->sonnet"

    # ---- Truth table N=3 ----

    def test_n3_all_pass_returns_approved(self):
        """N=3: all 3 verifiers pass → APPROVED; synthesizer NOT called."""
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "APPROVED"
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.return_value = "APPROVED"
        v3 = MagicMock(spec=AnthropicProvider)
        v3.query.return_value = "APPROVED"
        synth = MagicMock(spec=AnthropicProvider)

        def _get(model):
            if model == "gpt-5.5":
                return v1
            if model == "gemini-flash":
                return v2
            if model == "sonnet":
                return v3
            return synth  # synthesizer (haiku)

        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash", "sonnet"],
                synthesizer="haiku",
                prompt="p",
                system_prompt="",
                call_context="intent_validation",
            )

        assert response == "APPROVED"
        assert label == "gpt-5.5+gemini-flash+sonnet->haiku"
        synth.query.assert_not_called()

    def test_n3_all_fail_calls_synthesizer(self):
        """N=3: all 3 verifiers fail → synthesizer called → BLOCKED:."""
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "BLOCKED: A"
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.return_value = "BLOCKED: B"
        v3 = MagicMock(spec=AnthropicProvider)
        v3.query.return_value = "BLOCKED: C"
        synth = MagicMock(spec=AnthropicProvider)
        synth.query.return_value = "merged: A B C"

        def _get(model):
            if model == "gpt-5.5":
                return v1
            if model == "gemini-flash":
                return v2
            if model == "sonnet":
                return v3
            return synth  # synthesizer (haiku)

        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash", "sonnet"],
                synthesizer="haiku",
                prompt="p",
                system_prompt="",
                call_context="intent_validation",
            )

        assert response.startswith("BLOCKED:")
        assert "merged: A B C" in response
        synth.query.assert_called_once()

    def test_n3_one_missing_pretool_blocked(self):
        """N=3 pre-tool: one verifier infra-fails → BLOCKED (fail-closed)."""
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider
        from pacemaker.inference.provider import ProviderError

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "APPROVED"
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.return_value = "APPROVED"
        v3 = MagicMock(spec=AnthropicProvider)
        v3.query.side_effect = ProviderError("v3 down")

        def _get(model):
            if model == "gpt-5.5":
                return v1
            if model == "gemini-flash":
                return v2
            return v3

        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash", "sonnet"],
                synthesizer="haiku",
                prompt="p",
                system_prompt="",
                call_context="intent_validation",
            )

        # Pre-tool: missing verifier = fail-closed
        assert response.startswith("BLOCKED:")
        assert "did not respond" in response or "fail-closed" in response

    # ---- Stop-gate matrix ----

    def test_stop_gate_all_pass_returns_approved(self):
        """Stop: PASS/PASS → APPROVED."""
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "APPROVED"
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.return_value = "APPROVED"
        synth = MagicMock(spec=AnthropicProvider)

        def _get(model):
            if model == "gpt-5.5":
                return v1
            if model == "gemini-flash":
                return v2
            return synth

        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="p",
                system_prompt="",
                call_context="stop_hook",
            )

        assert response == "APPROVED"
        synth.query.assert_not_called()

    def test_stop_gate_one_fail_returns_blocked(self):
        """Stop: one verifier returns non-positive → BLOCKED."""
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "APPROVED"
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.return_value = "BLOCKED: work incomplete"
        synth = MagicMock(spec=AnthropicProvider)

        def _get(model):
            if model == "gpt-5.5":
                return v1
            if model == "gemini-flash":
                return v2
            return synth

        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="p",
                system_prompt="",
                call_context="stop_hook",
            )

        assert response.startswith("BLOCKED:")

    def test_stop_gate_zero_survivors_returns_empty_string(self):
        """Stop: zero survivors → '' — fail-open to avoid infinite stop loop (OQ-1)."""
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.provider import ProviderError

        failing = MagicMock()
        failing.query.side_effect = ProviderError("all down")

        with patch(
            "pacemaker.inference.competitive.get_provider", return_value=failing
        ):
            response, label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="p",
                system_prompt="",
                call_context="stop_hook",
            )

        # Empty string → parse_sdk_response("") → {"continue": True} — fail-open
        assert response == ""
        assert label == "gpt-5.5+gemini-flash->sonnet"

    def test_stop_gate_complete_counts_as_positive(self):
        """Stop: COMPLETE: counts as positive (both return COMPLETE: → APPROVED)."""
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "COMPLETE: task finished"
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.return_value = "COMPLETE: all done"
        synth = MagicMock(spec=AnthropicProvider)

        def _get(model):
            if model == "gpt-5.5":
                return v1
            if model == "gemini-flash":
                return v2
            return synth

        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="p",
                system_prompt="",
                call_context="stop_hook",
            )

        assert response == "APPROVED"
        synth.query.assert_not_called()

    def test_stop_gate_blocked_wins_over_complete(self):
        """Stop: BLOCKED: wins even when other verifier returns COMPLETE:."""
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "COMPLETE: task finished"
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.return_value = "BLOCKED: tests still failing"
        synth = MagicMock(spec=AnthropicProvider)

        def _get(model):
            if model == "gpt-5.5":
                return v1
            if model == "gemini-flash":
                return v2
            return synth

        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="p",
                system_prompt="",
                call_context="stop_hook",
            )

        assert response.startswith("BLOCKED:")

    def test_stop_gate_missing_verifier_ignored(self):
        """Stop: one verifier infra-fails but present one passed → APPROVED (missing ignored)."""
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider
        from pacemaker.inference.provider import ProviderError

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "APPROVED"
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.side_effect = ProviderError("gemini down")

        def _get(model):
            if model == "gpt-5.5":
                return v1
            return v2

        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="p",
                system_prompt="",
                call_context="stop_hook",
            )

        # Stop gate: missing verifier ignored; present one passed → APPROVED
        assert response == "APPROVED"

    # ---- Concurrent dispatch ----

    def test_concurrent_dispatch(self):
        """Verifiers are dispatched in parallel — verified via threading barrier."""
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.anthropic_provider import AnthropicProvider

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
        synth = MagicMock(spec=AnthropicProvider)
        synth.query.return_value = "synth result"

        def _get(model):
            if model == "haiku":
                return synth
            return slow

        start = time.monotonic()
        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, label = run_mechanical(
                verifiers=["sonnet", "gemini-flash"],
                synthesizer="haiku",
                prompt="p",
                system_prompt="",
                call_context="intent_validation",
            )
        elapsed = time.monotonic() - start

        # Barrier.wait() proves both threads ran concurrently
        assert len(dispatch_times) == 2
        assert elapsed < _ELAPSED_BOUND_S


# ---------------------------------------------------------------------------
# AgyProvider label in competitive pipeline tests (Story #77 fix)
# ---------------------------------------------------------------------------


class TestAgyProviderLabelInCompetitive:
    """Tests that AgyProvider gets the model alias as label, not 'anthropic-sdk'.

    Bug fixed in Story #77: _call_single_reviewer() handled CodexProvider and
    GeminiProvider but fell through to label = _REVIEWER_SDK for AgyProvider.
    """

    def test_agy_provider_label_is_model_alias_in_competitive(self):
        """AgyProvider in competitive pipeline must return model alias as label, not 'anthropic-sdk'."""
        from pacemaker.inference.competitive import _call_single_reviewer
        from pacemaker.inference.agy_provider import AgyProvider

        agy_mock = MagicMock(spec=AgyProvider)
        agy_mock.query.return_value = "APPROVED"

        with patch(
            "pacemaker.inference.competitive.get_provider", return_value=agy_mock
        ):
            response, label = _call_single_reviewer(
                "agy-gpt-oss", "review this", "", "intent_validation", 4000
            )

        assert response == "APPROVED"
        assert label == "agy-gpt-oss", f"Expected 'agy-gpt-oss', got '{label}'"

    def test_agy_flash_high_label_in_competitive(self):
        """AgyProvider with 'agy-flash-high' model returns 'agy-flash-high' as label."""
        from pacemaker.inference.competitive import _call_single_reviewer
        from pacemaker.inference.agy_provider import AgyProvider

        agy_mock = MagicMock(spec=AgyProvider)
        agy_mock.query.return_value = "BLOCKED: unsafe"

        with patch(
            "pacemaker.inference.competitive.get_provider", return_value=agy_mock
        ):
            response, label = _call_single_reviewer(
                "agy-flash-high", "review this", "", "intent_validation", 4000
            )

        assert label == "agy-flash-high", f"Expected 'agy-flash-high', got '{label}'"

    def test_agy_bare_label_in_competitive(self):
        """AgyProvider with bare 'agy' model returns 'agy' as label."""
        from pacemaker.inference.competitive import _call_single_reviewer
        from pacemaker.inference.agy_provider import AgyProvider

        agy_mock = MagicMock(spec=AgyProvider)
        agy_mock.query.return_value = "APPROVED"

        with patch(
            "pacemaker.inference.competitive.get_provider", return_value=agy_mock
        ):
            response, label = _call_single_reviewer(
                "agy", "review this", "", "intent_validation", 4000
            )

        assert label == "agy", f"Expected 'agy', got '{label}'"


# ---------------------------------------------------------------------------
# CLI hook-model tests (migrated — unchanged behavior)
# ---------------------------------------------------------------------------


class TestCLIHookModel:
    """Tests for CLI hook-model command with competitive expressions."""

    def test_cli_hook_model_competitive_expression(self, config_file):
        """CLI accepts competitive expression and normalizes gpt-5 alias to gpt-5.6-sol."""
        from pacemaker.user_commands import _execute_hook_model

        result = _execute_hook_model(config_file, "gpt-5+gemini-flash->sonnet")

        assert result["success"] is True
        assert (
            _read_config(config_file)["hook_model"]
            == "gpt-5.6-sol+gemini-flash->sonnet"
        )

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
# Status display test (migrated — unchanged behavior)
# ---------------------------------------------------------------------------


class TestStatusDisplayCompetitive:
    """Tests for status display with competitive hook_model."""

    def test_status_display_competitive(self, tmp_path):
        """Status shows expression and full hook_model when hook_model is competitive."""
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
        assert "gpt-5+gemini-flash->sonnet" in message
        assert "reviewers:" not in message.lower()


# ---------------------------------------------------------------------------
# Governance tag format tests (migrated — unchanged behavior)
# ---------------------------------------------------------------------------


class TestGovernanceTagCompetitive:
    """Tests for governance event tag format with competitive reviewer."""

    def test_governance_tag_competitive(self):
        """Reviewer label from competitive pipeline produces correct [expression] tag format."""
        from pacemaker.inference.competitive import parse_competitive

        reviewers, synthesizer = parse_competitive("gpt-5+gemini-flash->sonnet")
        expression = "+".join(reviewers) + "->" + synthesizer
        feedback = "some feedback"
        tagged = f"[{expression}] {feedback}"
        assert tagged == "[gpt-5.6-sol+gemini-flash->sonnet] some feedback"
        assert "REVIEWER:" not in tagged

    def test_governance_tag_single_reviewer_format(self):
        """Single reviewer label uses bracket format without REVIEWER: prefix."""
        from pacemaker.inference.competitive import parse_competitive

        reviewers, _synthesizer = parse_competitive("gpt-5+gemini-flash->sonnet")
        single_reviewer = reviewers[1]  # "gemini-flash"
        feedback = "BLOCKED: bad code"
        tagged = f"[{single_reviewer}] {feedback}"
        assert "[" + single_reviewer + "]" in tagged
        assert "REVIEWER:" not in tagged


# ---------------------------------------------------------------------------
# gpt-5.4 support tests (migrated — unchanged behavior)
# ---------------------------------------------------------------------------


class TestGpt54Support:
    """Tests for gpt-5.4 as canonical model and gpt-5 backward-compat alias."""

    def test_parse_competitive_gpt54_canonical(self):
        """parse_competitive accepts gpt-5.4 as a valid canonical model token."""
        from pacemaker.inference.competitive import parse_competitive

        result = parse_competitive("gpt-5.4+gemini-flash->haiku")
        assert result is not None
        reviewers, synthesizer = result
        assert "gpt-5.4" in reviewers
        assert synthesizer == "haiku"

    def test_parse_competitive_gpt5_alias_normalizes_to_gpt55(self):
        """gpt-5 in parse_competitive is treated as alias for gpt-5.6-sol."""
        from pacemaker.inference.competitive import parse_competitive

        result = parse_competitive("gpt-5+gemini-flash->haiku")
        assert result is not None
        reviewers, synthesizer = result
        assert "gpt-5.6-sol" in reviewers
        assert "gpt-5" not in reviewers

    @pytest.mark.parametrize(
        "model_hint,expected_model",
        [
            ("gpt-5", "gpt-5.6-sol"),  # alias resolves to gpt-5.6-sol
            ("gpt-5.4", "gpt-5.4"),  # canonical gpt-5.4 passes through unchanged
            ("gpt-5.5", "gpt-5.5"),  # canonical gpt-5.5 passes through unchanged
        ],
    )
    def test_codex_provider_uses_correct_model_for_hint(
        self, model_hint, expected_model
    ):
        """CodexProvider passes the correct -m flag to subprocess for each model hint."""
        from unittest.mock import patch, MagicMock
        from pacemaker.inference.codex_provider import CodexProvider

        provider = CodexProvider()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "APPROVED"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            provider.query("check this", "", model_hint, 4000)

        call_args = mock_run.call_args
        cmd = call_args[0][0]
        m_index = cmd.index("-m")
        assert cmd[m_index + 1] == expected_model

    def test_cli_accepts_gpt54_as_single_model(self, config_file):
        """_execute_hook_model accepts gpt-5.4 and stores it in config."""
        from pacemaker.user_commands import _execute_hook_model

        result = _execute_hook_model(config_file, "gpt-5.4")

        assert result["success"] is True
        assert _read_config(config_file)["hook_model"] == "gpt-5.4"

    def test_cli_gpt5_backward_compat_stores_gpt55(self, config_file):
        """_execute_hook_model accepts legacy gpt-5 and stores gpt-5.6-sol as canonical."""
        from pacemaker.user_commands import _execute_hook_model

        result = _execute_hook_model(config_file, "gpt-5")

        assert result["success"] is True
        assert _read_config(config_file)["hook_model"] == "gpt-5.6-sol"

    def test_get_provider_gpt54_returns_codex_provider(self):
        """get_provider('gpt-5.4') returns a CodexProvider instance."""
        from pacemaker.inference.registry import get_provider
        from pacemaker.inference.codex_provider import CodexProvider

        provider = get_provider("gpt-5.4")
        assert isinstance(provider, CodexProvider)

    def test_get_provider_gpt5_still_returns_codex_provider(self):
        """get_provider('gpt-5') still returns CodexProvider (backward compat)."""
        from pacemaker.inference.registry import get_provider
        from pacemaker.inference.codex_provider import CodexProvider

        provider = get_provider("gpt-5")
        assert isinstance(provider, CodexProvider)

    def test_parse_command_accepts_gpt54_competitive_expression(self):
        """parse_command recognizes gpt-5.4 in competitive expressions."""
        from pacemaker.user_commands import parse_command

        r = parse_command("pace-maker hook-model gpt-5.4+gemini-flash->haiku")
        assert r["is_pace_maker_command"] is True
        assert r["command"] == "hook-model"
        assert r["subcommand"] == "gpt-5.4+gemini-flash->haiku"

    def test_parse_command_accepts_gpt5_legacy_competitive_expression(self):
        """parse_command recognizes legacy gpt-5 alias in competitive expressions."""
        from pacemaker.user_commands import parse_command

        r = parse_command("pace-maker hook-model gpt-5+gemini-flash->haiku")
        assert r["is_pace_maker_command"] is True
        assert r["command"] == "hook-model"
        assert r["subcommand"] == "gpt-5+gemini-flash->haiku"

    def test_parse_command_accepts_gpt54_three_way_competitive(self):
        """parse_command recognizes gpt-5.4 in 3-reviewer competitive expressions."""
        from pacemaker.user_commands import parse_command

        r = parse_command("pace-maker hook-model opus+gpt-5.4+gemini-pro->sonnet")
        assert r["is_pace_maker_command"] is True
        assert r["command"] == "hook-model"
        assert r["subcommand"] == "opus+gpt-5.4+gemini-pro->sonnet"


# ---------------------------------------------------------------------------
# gpt-5.5 support tests (migrated — unchanged behavior)
# ---------------------------------------------------------------------------


class TestGpt55Support:
    """Tests for gpt-5.5 as the preferred Codex model."""

    def test_known_models_contains_gpt55_and_gpt54(self):
        """gpt-5.5 is in KNOWN_MODELS and gpt-5.4 is still present (regression guard)."""
        from pacemaker.inference.model_aliases import KNOWN_MODELS

        assert "gpt-5.5" in KNOWN_MODELS
        assert "gpt-5.4" in KNOWN_MODELS  # must not be removed

    @pytest.mark.parametrize(
        "alias,expected",
        [
            ("gpt-5", "gpt-5.6-sol"),
            ("gpt", "gpt-5.6-sol"),
            ("codex", "gpt-5.6-sol"),
        ],
    )
    def test_short_aliases_resolve_to_gpt55(self, alias, expected):
        """SHORT_ALIASES maps gpt-5, gpt, and codex to gpt-5.6-sol."""
        from pacemaker.inference.model_aliases import SHORT_ALIASES

        assert SHORT_ALIASES[alias] == expected

    @pytest.mark.parametrize("model_input", ["gpt-5.5", "gpt", "codex"])
    def test_get_provider_returns_codex_for_gpt55_and_aliases(self, model_input):
        """get_provider returns CodexProvider for gpt-5.5 canonical and gpt/codex aliases."""
        from pacemaker.inference.registry import get_provider
        from pacemaker.inference.codex_provider import CodexProvider

        assert isinstance(get_provider(model_input), CodexProvider)

    def test_cli_accepts_gpt55_as_single_model(self, config_file):
        """_execute_hook_model accepts gpt-5.5 directly and stores it in config."""
        from pacemaker.user_commands import _execute_hook_model

        result = _execute_hook_model(config_file, "gpt-5.5")

        assert result["success"] is True
        assert _read_config(config_file)["hook_model"] == "gpt-5.5"

    @pytest.mark.parametrize("alias", ["gpt", "codex"])
    def test_cli_gpt_and_codex_aliases_store_gpt55(self, alias, config_file):
        """_execute_hook_model accepts gpt and codex aliases and stores gpt-5.6-sol as canonical."""
        from pacemaker.user_commands import _execute_hook_model

        result = _execute_hook_model(config_file, alias)

        assert result["success"] is True
        assert _read_config(config_file)["hook_model"] == "gpt-5.6-sol"

    def test_parse_competitive_gpt55_canonical_and_codex_alias(self):
        """parse_competitive accepts gpt-5.5 as canonical and normalizes codex alias to gpt-5.6-sol."""
        from pacemaker.inference.competitive import parse_competitive

        result = parse_competitive("gpt-5.5+gemini-flash->haiku")
        assert result is not None
        reviewers, _ = result
        assert "gpt-5.5" in reviewers

        result2 = parse_competitive("opus+codex->haiku")
        assert result2 is not None
        reviewers2, _ = result2
        assert "gpt-5.6-sol" in reviewers2
        assert "codex" not in reviewers2
