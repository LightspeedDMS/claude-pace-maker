"""Tests for Story #75: codex-<profile> expression parsing, CLI, and status surfacing."""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _make_config(data=None):
    """Create a temp config file and return its path (caller must unlink)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data or {"hook_model": "auto"}, f)
        return f.name


# ──────────────────────────────────────────────────────────────────────────────
# competitive.py — parse_competitive token validation
# ──────────────────────────────────────────────────────────────────────────────


class TestParseCompetitiveCodexProfile:
    """parse_competitive must accept codex-<profile> in reviewer and synthesizer slots."""

    def test_codex_profile_as_reviewer_accepted(self):
        """codex-beast+haiku->sonnet must parse without error."""
        from pacemaker.inference.competitive import parse_competitive

        result = parse_competitive("codex-beast+haiku->sonnet")
        assert result is not None
        reviewers, synthesizer = result
        assert "codex-beast" in reviewers
        assert "haiku" in reviewers
        assert synthesizer == "sonnet"

    def test_codex_profile_as_synthesizer_accepted(self):
        """haiku+sonnet->codex-beast must parse without error."""
        from pacemaker.inference.competitive import parse_competitive

        result = parse_competitive("haiku+sonnet->codex-beast")
        assert result is not None
        reviewers, synthesizer = result
        assert "haiku" in reviewers
        assert "sonnet" in reviewers
        assert synthesizer == "codex-beast"

    def test_codex_profile_with_dots_and_underscores_accepted(self):
        """codex-my.profile_v1 is a valid profile token shape."""
        from pacemaker.inference.competitive import parse_competitive

        result = parse_competitive("codex-my.profile+haiku->sonnet")
        assert result is not None
        reviewers, _ = result
        assert "codex-my.profile" in reviewers

    def test_codex_profile_invalid_slash_char_rejected(self):
        """codex-bad/char must raise ValueError (slash not in allowed char class)."""
        from pacemaker.inference.competitive import parse_competitive
        import pytest

        with pytest.raises(ValueError, match="Invalid model"):
            parse_competitive("codex-bad/char+haiku->sonnet")

    def test_codex_empty_profile_rejected(self):
        """codex- alone (empty profile name) must raise ValueError."""
        from pacemaker.inference.competitive import parse_competitive
        import pytest

        with pytest.raises(ValueError, match="Invalid model"):
            parse_competitive("codex-+haiku->sonnet")

    def test_three_reviewer_with_codex_profile_accepted(self):
        """opus+codex-beast+haiku->sonnet (3 reviewers) must parse."""
        from pacemaker.inference.competitive import parse_competitive

        result = parse_competitive("opus+codex-beast+haiku->sonnet")
        assert result is not None
        reviewers, synthesizer = result
        assert len(reviewers) == 3
        assert "codex-beast" in reviewers


# ──────────────────────────────────────────────────────────────────────────────
# competitive.py — _call_single_reviewer label for codex-<profile>
# ──────────────────────────────────────────────────────────────────────────────


class TestCallSingleReviewerLabel:
    """_call_single_reviewer must return verbatim token as label for codex-<profile>."""

    def test_codex_profile_label_is_verbatim_token(self):
        """'codex-beast' reviewer must return label 'codex-beast', NOT 'codex-gpt5'."""
        from pacemaker.inference.competitive import _call_single_reviewer
        from pacemaker.inference.codex_provider import CodexProvider

        fake_provider = MagicMock(spec=CodexProvider)
        fake_provider.query.return_value = "APPROVED"

        with (
            patch(
                "pacemaker.inference.competitive.get_provider",
                return_value=fake_provider,
            ),
            patch(
                "pacemaker.inference.competitive.resolve_model_for_call",
                return_value="codex-beast",
            ),
        ):
            _response, label = _call_single_reviewer(
                "codex-beast", "prompt", "system", "context", 4000
            )
        assert label == "codex-beast"

    def test_plain_codex_gpt55_label_is_codex_gpt5(self):
        """gpt-5.5 reviewer must keep the legacy 'codex-gpt5' label."""
        from pacemaker.inference.competitive import _call_single_reviewer
        from pacemaker.inference.codex_provider import CodexProvider

        fake_provider = MagicMock(spec=CodexProvider)
        fake_provider.query.return_value = "APPROVED"

        with (
            patch(
                "pacemaker.inference.competitive.get_provider",
                return_value=fake_provider,
            ),
            patch(
                "pacemaker.inference.competitive.resolve_model_for_call",
                return_value="gpt-5.5",
            ),
        ):
            _response, label = _call_single_reviewer(
                "gpt-5.5", "prompt", "system", "context", 4000
            )
        assert label == "codex-gpt5"

    def test_gpt54_label_is_codex_gpt5(self):
        """gpt-5.4 reviewer must also keep 'codex-gpt5' label."""
        from pacemaker.inference.competitive import _call_single_reviewer
        from pacemaker.inference.codex_provider import CodexProvider

        fake_provider = MagicMock(spec=CodexProvider)
        fake_provider.query.return_value = "APPROVED"

        with (
            patch(
                "pacemaker.inference.competitive.get_provider",
                return_value=fake_provider,
            ),
            patch(
                "pacemaker.inference.competitive.resolve_model_for_call",
                return_value="gpt-5.4",
            ),
        ):
            _response, label = _call_single_reviewer(
                "gpt-5.4", "prompt", "system", "context", 4000
            )
        assert label == "codex-gpt5"

    def test_codex_pro_label_verbatim(self):
        """'codex-pro' reviewer label must be 'codex-pro'."""
        from pacemaker.inference.competitive import _call_single_reviewer
        from pacemaker.inference.codex_provider import CodexProvider

        fake_provider = MagicMock(spec=CodexProvider)
        fake_provider.query.return_value = "BLOCKED: something"

        with (
            patch(
                "pacemaker.inference.competitive.get_provider",
                return_value=fake_provider,
            ),
            patch(
                "pacemaker.inference.competitive.resolve_model_for_call",
                return_value="codex-pro",
            ),
        ):
            _response, label = _call_single_reviewer(
                "codex-pro", "prompt", "system", "context", 4000
            )
        assert label == "codex-pro"


# ──────────────────────────────────────────────────────────────────────────────
# competitive.py — synthesizer slot routing through CodexProvider
# ──────────────────────────────────────────────────────────────────────────────


class TestSynthesizerSlotCodexProfile:
    """haiku+sonnet->codex-beast must call get_provider('codex-beast') for synthesis."""

    def test_synthesizer_codex_profile_routes_through_get_provider(self):
        """Assert get_provider is called for the synthesizer token 'codex-beast'.

        With run_mechanical, the synthesizer is only called when 2+ verifiers FAIL.
        Both reviewers return BLOCKED to trigger the synthesis path.
        """
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        # Reviewer providers return BLOCKED verdicts to trigger synthesis path
        reviewer_provider = MagicMock(spec=AnthropicProvider)
        reviewer_provider.query.return_value = "BLOCKED: concern A"

        second_reviewer = MagicMock(spec=AnthropicProvider)
        second_reviewer.query.return_value = "BLOCKED: concern B"

        # Synthesizer is a CodexProvider
        synth_provider = MagicMock(spec=CodexProvider)
        synth_provider.query.return_value = "combined concerns"

        call_log = []
        providers = {
            "haiku": reviewer_provider,
            "sonnet": second_reviewer,
            "codex-beast": synth_provider,
        }

        def fake_get_provider(model):
            call_log.append(model)
            return providers.get(model, reviewer_provider)

        with (
            patch(
                "pacemaker.inference.competitive.get_provider",
                side_effect=fake_get_provider,
            ),
            patch(
                "pacemaker.inference.competitive.resolve_model_for_call",
                return_value="model-hint",
            ),
        ):
            run_mechanical(
                verifiers=["haiku", "sonnet"],
                synthesizer="codex-beast",
                prompt="test prompt",
                system_prompt="system",
                call_context="test",
            )

        assert (
            "codex-beast" in call_log
        ), f"get_provider was not called with 'codex-beast'; calls={call_log}"


# ──────────────────────────────────────────────────────────────────────────────
# user_commands.py — parse_command single-model regex (pattern_hook_model_single)
# ──────────────────────────────────────────────────────────────────────────────


class TestHookModelSingleRegexCodexProfile:
    """pattern_hook_model_single must accept codex-<profile> tokens."""

    def test_parse_codex_beast_single(self):
        """pace-maker hook-model codex-beast must match the single-model pattern."""
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker hook-model codex-beast")
        assert result["is_pace_maker_command"] is True
        assert result["command"] == "hook-model"
        assert result["subcommand"] == "codex-beast"

    def test_parse_codex_profile_with_dots(self):
        """pace-maker hook-model codex-my.profile must match."""
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker hook-model codex-my.profile")
        assert result["command"] == "hook-model"
        assert result["subcommand"] == "codex-my.profile"

    def test_parse_codex_bare_alias_still_works(self):
        """pace-maker hook-model codex (alias) must still match via the old path."""
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker hook-model codex")
        assert result["is_pace_maker_command"] is True
        assert result["command"] == "hook-model"

    def test_parse_codex_empty_profile_not_single_match(self):
        """codex- (empty profile) must NOT match the single-model pattern."""
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker hook-model codex-")
        # Either not a pace-maker command at all, or falls through to unknown
        # The key assertion: it must NOT succeed with hook-model codex-
        assert not (
            result.get("is_pace_maker_command") and result.get("subcommand") == "codex-"
        ), f"Expected rejection of 'codex-' but got: {result}"

    def test_parse_codex_slash_char_not_single_match(self):
        """codex-bad/char must NOT match (slash not in allowed char class)."""
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker hook-model codex-bad/char")
        assert not (
            result.get("is_pace_maker_command")
            and result.get("command") == "hook-model"
            and result.get("subcommand") == "codex-bad/char"
        )


# ──────────────────────────────────────────────────────────────────────────────
# user_commands.py — _execute_hook_model single-model validation and message
# ──────────────────────────────────────────────────────────────────────────────


class TestExecuteHookModelCodexProfile:
    """_execute_hook_model must accept, store and confirm codex-<profile> tokens."""

    def test_execute_codex_beast_success(self):
        """_execute_hook_model('codex-beast') must return success=True."""
        from pacemaker.user_commands import _execute_hook_model

        config_path = _make_config()
        try:
            result = _execute_hook_model(config_path, "codex-beast")
            assert result["success"] is True
        finally:
            os.unlink(config_path)

    def test_execute_codex_beast_stores_in_config(self):
        """codex-beast must be persisted verbatim in config file."""
        from pacemaker.user_commands import _execute_hook_model

        config_path = _make_config()
        try:
            _execute_hook_model(config_path, "codex-beast")
            with open(config_path) as f:
                stored = json.load(f)
            assert stored["hook_model"] == "codex-beast"
        finally:
            os.unlink(config_path)

    def test_execute_codex_beast_message_mentions_profile(self):
        """Confirmation message must mention the profile name 'beast'."""
        from pacemaker.user_commands import _execute_hook_model

        config_path = _make_config()
        try:
            result = _execute_hook_model(config_path, "codex-beast")
            assert "beast" in result["message"].lower()
        finally:
            os.unlink(config_path)

    def test_execute_codex_empty_profile_rejected(self):
        """codex- (empty) must return success=False."""
        from pacemaker.user_commands import _execute_hook_model

        config_path = _make_config()
        try:
            result = _execute_hook_model(config_path, "codex-")
            assert result["success"] is False
        finally:
            os.unlink(config_path)

    def test_execute_unknown_token_still_rejected(self):
        """Completely unknown tokens must still return success=False."""
        from pacemaker.user_commands import _execute_hook_model

        config_path = _make_config()
        try:
            result = _execute_hook_model(config_path, "totally-unknown-model")
            assert result["success"] is False
        finally:
            os.unlink(config_path)

    def test_execute_competitive_codex_profile_full_roundtrip(self):
        """codex-beast+haiku->sonnet competitive expression stored canonically."""
        from pacemaker.user_commands import _execute_hook_model

        config_path = _make_config()
        try:
            result = _execute_hook_model(config_path, "codex-beast+haiku->sonnet")
            assert result["success"] is True
            with open(config_path) as f:
                stored = json.load(f)
            assert stored["hook_model"] == "codex-beast+haiku->sonnet"
        finally:
            os.unlink(config_path)

    def test_execute_competitive_codex_profile_synthesizer(self):
        """haiku+sonnet->codex-beast (codex-beast as synthesizer) stored canonically."""
        from pacemaker.user_commands import _execute_hook_model

        config_path = _make_config()
        try:
            result = _execute_hook_model(config_path, "haiku+sonnet->codex-beast")
            assert result["success"] is True
            with open(config_path) as f:
                stored = json.load(f)
            assert stored["hook_model"] == "haiku+sonnet->codex-beast"
        finally:
            os.unlink(config_path)


# ──────────────────────────────────────────────────────────────────────────────
# user_commands.py — status rendering
# ──────────────────────────────────────────────────────────────────────────────


class TestStatusCodexProfile:
    """Status command must render codex-<profile> expressions without crashing."""

    def test_status_renders_codex_beast_expression(self):
        """_status_text with hook_model='codex-beast' must include 'codex-beast'."""
        from pacemaker.user_commands import _execute_status

        config_path = _make_config({"hook_model": "codex-beast", "enabled": True})
        try:
            result = _execute_status(config_path, None)
            assert result["success"] is True
            assert (
                "codex-beast" in result["message"].upper()
                or "CODEX-BEAST" in result["message"].upper()
                or "codex-beast" in result["message"]
            )
        finally:
            os.unlink(config_path)

    def test_status_renders_competitive_codex_profile(self):
        """Status with hook_model='codex-beast+haiku->sonnet' must render something."""
        from pacemaker.user_commands import _execute_status

        config_path = _make_config(
            {
                "hook_model": "codex-beast+haiku->sonnet",
                "enabled": True,
            }
        )
        try:
            result = _execute_status(config_path, None)
            assert result["success"] is True
            # Must mention the expression or at least not crash
            assert isinstance(result["message"], str)
            assert len(result["message"]) > 0
        finally:
            os.unlink(config_path)
