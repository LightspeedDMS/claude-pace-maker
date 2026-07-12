"""Tests for Story #85: gpt-5.6-sol, gpt-5.6-terra, gpt-5.6-luna, gpt-5.4-mini
Codex models as selectable hook-model reviewers (single-model AND competitive
reviewer/synthesizer slots), with reviewer identity still rendering as
"codex-gpt5" (which claude-usage maps to [Codex]/yellow).

Covers:
  - model_aliases.py: KNOWN_MODELS, SHORT_ALIASES re-point (gpt-5/gpt/codex -> gpt-5.6-sol)
  - registry.py: get_provider() routing, reviewer label ("codex-gpt5")
  - competitive.py: parse_competitive() reviewer/synthesizer slots, _call_single_reviewer label
  - codex_provider.py: _parse_codex_target() passthrough, argv construction
  - user_commands.py: single-model CLI regex, _execute_hook_model validation/message/storage
  - Regression: gpt-5.5/gpt-5.4 unaffected; unknown gpt-5.7-nova rejected
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest


NEW_MODELS = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.4-mini"]


def _make_config(data=None):
    """Create a temp config file and return its path (caller must unlink)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data or {"hook_model": "auto"}, f)
        return f.name


# ─────────────────────────────────────────────────────────────────────────────
# Part 1: model_aliases.py
# ─────────────────────────────────────────────────────────────────────────────


class TestKnownModelsIncludesNewGpt56Models:
    @pytest.mark.parametrize("model", NEW_MODELS)
    def test_new_model_in_known_models(self, model):
        from pacemaker.inference.model_aliases import KNOWN_MODELS

        assert model in KNOWN_MODELS

    @pytest.mark.parametrize("model", NEW_MODELS)
    def test_is_known_model_accepts_new_model(self, model):
        from pacemaker.inference.model_aliases import is_known_model

        assert is_known_model(model) is True


class TestShortAliasesRepointToGpt56Sol:
    @pytest.mark.parametrize("alias", ["gpt-5", "gpt", "codex"])
    def test_alias_resolves_to_gpt56_sol(self, alias):
        from pacemaker.inference.model_aliases import SHORT_ALIASES

        assert SHORT_ALIASES[alias] == "gpt-5.6-sol"


class TestModelAliasesRegressionUnaffected:
    def test_gpt55_still_in_known_models(self):
        from pacemaker.inference.model_aliases import KNOWN_MODELS

        assert "gpt-5.5" in KNOWN_MODELS

    def test_gpt54_still_in_known_models(self):
        from pacemaker.inference.model_aliases import KNOWN_MODELS

        assert "gpt-5.4" in KNOWN_MODELS


# ─────────────────────────────────────────────────────────────────────────────
# Part 2: registry.py — get_provider() routing
# ─────────────────────────────────────────────────────────────────────────────


class TestGetProviderRoutesNewModelsToCodex:
    @pytest.mark.parametrize("model", NEW_MODELS)
    def test_routes_to_codex_provider(self, model):
        from pacemaker.inference.registry import get_provider
        from pacemaker.inference.codex_provider import CodexProvider

        assert isinstance(get_provider(model), CodexProvider)


# ─────────────────────────────────────────────────────────────────────────────
# Part 3: registry.py — resolve_and_call_with_reviewer() reviewer label
# ─────────────────────────────────────────────────────────────────────────────


class TestReviewerLabelForNewModels:
    @pytest.mark.parametrize("model", NEW_MODELS)
    def test_reviewer_label_is_codex_gpt5(self, model):
        from pacemaker.inference.registry import resolve_and_call_with_reviewer
        from pacemaker.inference.codex_provider import CodexProvider

        with patch.object(CodexProvider, "query", return_value="APPROVED"):
            response, reviewer = resolve_and_call_with_reviewer(
                model, "test prompt", "sys prompt", "intent_validation"
            )
        assert response == "APPROVED"
        assert reviewer == "codex-gpt5"


# ─────────────────────────────────────────────────────────────────────────────
# Part 4: competitive.py — parse_competitive() reviewer + synthesizer slots
# ─────────────────────────────────────────────────────────────────────────────


class TestParseCompetitiveAcceptsNewModels:
    def test_new_model_as_reviewer(self):
        from pacemaker.inference.competitive import parse_competitive

        result = parse_competitive("gpt-5.6-terra+haiku->sonnet")
        assert result is not None
        reviewers, synthesizer = result
        assert "gpt-5.6-terra" in reviewers
        assert synthesizer == "sonnet"

    def test_new_model_as_synthesizer(self):
        from pacemaker.inference.competitive import parse_competitive

        result = parse_competitive("haiku+sonnet->gpt-5.6-luna")
        assert result is not None
        reviewers, synthesizer = result
        assert "haiku" in reviewers
        assert "sonnet" in reviewers
        assert synthesizer == "gpt-5.6-luna"

    @pytest.mark.parametrize("model", NEW_MODELS)
    def test_each_new_model_accepted_as_reviewer(self, model):
        from pacemaker.inference.competitive import parse_competitive

        result = parse_competitive(f"{model}+haiku->sonnet")
        assert result is not None
        reviewers, _synth = result
        assert model in reviewers

    @pytest.mark.parametrize("model", NEW_MODELS)
    def test_each_new_model_accepted_as_synthesizer(self, model):
        from pacemaker.inference.competitive import parse_competitive

        result = parse_competitive(f"haiku+sonnet->{model}")
        assert result is not None
        _reviewers, synth = result
        assert synth == model


# ─────────────────────────────────────────────────────────────────────────────
# Part 5: competitive.py — _call_single_reviewer label
# ─────────────────────────────────────────────────────────────────────────────


class TestCallSingleReviewerLabelForNewModels:
    @pytest.mark.parametrize("model", NEW_MODELS)
    def test_label_is_codex_gpt5(self, model):
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
                return_value=model,
            ),
        ):
            _response, label = _call_single_reviewer(
                model, "prompt", "system", "context", 4000
            )
        assert label == "codex-gpt5"


# ─────────────────────────────────────────────────────────────────────────────
# Part 6: codex_provider.py — _parse_codex_target() passthrough / alias resolution
# ─────────────────────────────────────────────────────────────────────────────


class TestParseCodexTargetPassthroughForNewModels:
    @pytest.mark.parametrize("model", NEW_MODELS)
    def test_new_model_passed_through_unchanged(self, model):
        from pacemaker.inference.codex_provider import _parse_codex_target

        profile, resolved_model = _parse_codex_target(model)
        assert profile is None
        assert resolved_model == model


class TestParseCodexTargetAliasResolution:
    @pytest.mark.parametrize("alias", ["gpt-5", "gpt", "codex"])
    def test_alias_resolves_to_gpt56_sol(self, alias):
        from pacemaker.inference.codex_provider import _parse_codex_target

        profile, model = _parse_codex_target(alias)
        assert profile is None
        assert model == "gpt-5.6-sol"


# ─────────────────────────────────────────────────────────────────────────────
# Part 7: codex_provider.py — CodexProvider.query() argv construction
# ─────────────────────────────────────────────────────────────────────────────


class TestCodexProviderArgvForNewModels:
    @staticmethod
    def _mock_success():
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "APPROVED"
        mock_result.stderr = ""
        return mock_result

    @pytest.mark.parametrize("model", NEW_MODELS)
    def test_argv_uses_m_flag_with_model(self, model):
        from pacemaker.inference.codex_provider import CodexProvider

        provider = CodexProvider()
        with patch(
            "pacemaker.inference.codex_provider.subprocess.run",
            return_value=self._mock_success(),
        ) as mock_run:
            provider.query("test prompt", model_hint=model)
            cmd = mock_run.call_args[0][0]
        assert cmd == [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "-",
            "-m",
            model,
            "-s",
            "read-only",
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Part 8: user_commands.py — parse_command single-model regex
# ─────────────────────────────────────────────────────────────────────────────


class TestParseCommandAcceptsNewModels:
    @pytest.mark.parametrize("model", NEW_MODELS)
    def test_single_model_regex_matches(self, model):
        from pacemaker.user_commands import parse_command

        result = parse_command(f"pace-maker hook-model {model}")
        assert result["is_pace_maker_command"] is True
        assert result["command"] == "hook-model"
        assert result["subcommand"] == model

    def test_gpt54_mini_not_shadowed_by_gpt54(self):
        """gpt-5.4-mini must match fully, not be truncated to gpt-5.4."""
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker hook-model gpt-5.4-mini")
        assert result["subcommand"] == "gpt-5.4-mini"

    def test_gpt56sol_not_shadowed_by_gpt5(self):
        """gpt-5.6-sol must match fully, not be truncated by the bare gpt-5 alias."""
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker hook-model gpt-5.6-sol")
        assert result["subcommand"] == "gpt-5.6-sol"


class TestParseCommandRejectsUnknownGpt57Nova:
    def test_gpt57_nova_not_recognized(self):
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker hook-model gpt-5.7-nova")
        assert result["is_pace_maker_command"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Part 9: user_commands.py — _execute_hook_model
# ─────────────────────────────────────────────────────────────────────────────


class TestExecuteHookModelNewModels:
    @pytest.mark.parametrize("model", NEW_MODELS)
    def test_success(self, model):
        from pacemaker.user_commands import _execute_hook_model

        config_path = _make_config()
        try:
            result = _execute_hook_model(config_path, model)
            assert result["success"] is True
        finally:
            os.unlink(config_path)

    @pytest.mark.parametrize("model", NEW_MODELS)
    def test_stores_canonical_in_config(self, model):
        from pacemaker.user_commands import _execute_hook_model

        config_path = _make_config()
        try:
            _execute_hook_model(config_path, model)
            with open(config_path) as f:
                stored = json.load(f)
            assert stored["hook_model"] == model
        finally:
            os.unlink(config_path)

    @pytest.mark.parametrize("model", NEW_MODELS)
    def test_confirmation_names_model(self, model):
        from pacemaker.user_commands import _execute_hook_model

        config_path = _make_config()
        try:
            result = _execute_hook_model(config_path, model)
            assert model in result["message"]
        finally:
            os.unlink(config_path)

    def test_gpt57_nova_rejected(self):
        from pacemaker.user_commands import _execute_hook_model

        config_path = _make_config()
        try:
            result = _execute_hook_model(config_path, "gpt-5.7-nova")
            assert result["success"] is False
            assert "Invalid model" in result["message"]
        finally:
            os.unlink(config_path)

    def test_gpt57_nova_usage_message_mentions_latest_model(self):
        """The rejection usage hint should surface the new latest model."""
        from pacemaker.user_commands import _execute_hook_model

        config_path = _make_config()
        try:
            result = _execute_hook_model(config_path, "gpt-5.7-nova")
            assert "gpt-5.6-sol" in result["message"]
        finally:
            os.unlink(config_path)


class TestExecuteHookModelConfirmationMessageDetail:
    """Dedicated confirmation messages (not the generic fallback) for new models."""

    @pytest.mark.parametrize(
        "model,display",
        [
            ("gpt-5.6-sol", "GPT-5.6-SOL"),
            ("gpt-5.6-terra", "GPT-5.6-TERRA"),
            ("gpt-5.6-luna", "GPT-5.6-LUNA"),
            ("gpt-5.4-mini", "GPT-5.4-MINI"),
        ],
    )
    def test_message_names_uppercase_display_form(self, model, display):
        from pacemaker.user_commands import _execute_hook_model

        config_path = _make_config()
        try:
            result = _execute_hook_model(config_path, model)
            assert display in result["message"]
        finally:
            os.unlink(config_path)

    @pytest.mark.parametrize("model", NEW_MODELS)
    def test_message_mentions_codex_cli(self, model):
        from pacemaker.user_commands import _execute_hook_model

        config_path = _make_config()
        try:
            result = _execute_hook_model(config_path, model)
            assert "Codex CLI" in result["message"]
        finally:
            os.unlink(config_path)


class TestExecuteHookModelAliasRepoint:
    @pytest.mark.parametrize("alias", ["gpt-5", "gpt", "codex"])
    def test_alias_resolves_and_stores_gpt56_sol(self, alias):
        from pacemaker.user_commands import _execute_hook_model

        config_path = _make_config()
        try:
            result = _execute_hook_model(config_path, alias)
            assert result["success"] is True
            with open(config_path) as f:
                stored = json.load(f)
            assert stored["hook_model"] == "gpt-5.6-sol"
            assert "gpt-5.6-sol" in result["message"]
        finally:
            os.unlink(config_path)


class TestHelpTextMentionsNewModels:
    """CLI help text (HELP_TEXT) must document the new models and the alias repoint."""

    @pytest.mark.parametrize("model", NEW_MODELS)
    def test_help_text_lists_model(self, model):
        from pacemaker.user_commands import HELP_TEXT

        assert f"hook-model {model}" in HELP_TEXT

    def test_help_text_alias_description_names_gpt56_sol(self):
        from pacemaker.user_commands import HELP_TEXT

        assert "Alias for gpt-5.6-sol" in HELP_TEXT


# ─────────────────────────────────────────────────────────────────────────────
# Part 10: Regression — gpt-5.5 / gpt-5.4 unaffected
# ─────────────────────────────────────────────────────────────────────────────


class TestRegressionExistingCodexModelsStillWork:
    @pytest.mark.parametrize("model", ["gpt-5.5", "gpt-5.4"])
    def test_still_routes_to_codex_provider(self, model):
        from pacemaker.inference.registry import get_provider
        from pacemaker.inference.codex_provider import CodexProvider

        assert isinstance(get_provider(model), CodexProvider)

    @pytest.mark.parametrize("model", ["gpt-5.5", "gpt-5.4"])
    def test_still_accepted_by_cli(self, model):
        from pacemaker.user_commands import parse_command

        result = parse_command(f"pace-maker hook-model {model}")
        assert result["is_pace_maker_command"] is True
        assert result["subcommand"] == model
