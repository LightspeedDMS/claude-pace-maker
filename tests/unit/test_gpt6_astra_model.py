"""Issue #136 — gpt-6-astra must be selectable as a hook model.

The model is live on the account (`codex exec -m gpt-6-astra` returns exit 0)
but pace-maker rejected the token, so it could not be set via the CLI, used in
a competitive expression, or routed to a provider.
"""

import json
import os
import pathlib
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from pacemaker.inference.codex_provider import CodexProvider, _parse_codex_target
from pacemaker.inference.model_aliases import (
    KNOWN_MODELS,
    SHORT_ALIASES,
    is_known_model,
)
from pacemaker.inference.registry import get_provider

ASTRA = "gpt-6-astra"


def _make_config():
    """A throwaway config file for CLI execution tests."""
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump({"hook_model": "auto"}, f)
    return path


class TestTokenRecognised:
    def test_is_known_model_accepts_astra(self):
        assert is_known_model(ASTRA) is True

    def test_present_in_known_models(self):
        assert ASTRA in KNOWN_MODELS

    def test_not_wired_into_short_aliases(self):
        """gpt-5/gpt keep pointing at gpt-5.6-sol — repointing them would
        silently change the reviewer for every existing config."""
        assert ASTRA not in SHORT_ALIASES.values()
        assert SHORT_ALIASES.get("gpt-5") != ASTRA
        assert SHORT_ALIASES.get("gpt") != ASTRA


class TestProviderRouting:
    def test_routes_to_codex_provider(self):
        assert isinstance(get_provider(ASTRA), CodexProvider)

    def test_parsed_as_model_not_profile(self):
        """A model token, not a codex-<profile> token: no --profile is used."""
        profile, model = _parse_codex_target(ASTRA)
        assert profile is None
        assert model == ASTRA

    def test_argv_carries_model_flag(self):
        with patch("subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stdout="APPROVED", stderr="")
            CodexProvider().query("prompt", "system", model_hint=ASTRA)
            argv = run.call_args[0][0]
        assert "-m" in argv
        assert argv[argv.index("-m") + 1] == ASTRA
        assert "--profile" not in argv
        # Regression guard: the codex 0.139 trusted-directory flag stays.
        assert "--skip-git-repo-check" in argv


class TestCompetitiveExpressions:
    @pytest.mark.parametrize(
        "expression",
        [
            f"haiku+{ASTRA}->codex-beast",
            f"{ASTRA}+haiku->sonnet",
            f"haiku+sonnet->{ASTRA}",
        ],
    )
    def test_accepted_in_every_slot(self, expression):
        from pacemaker.inference.competitive import parse_competitive

        assert parse_competitive(expression) is not None


class TestCliSelection:
    def test_cli_regex_matches_astra(self):
        from pacemaker.user_commands import parse_command

        result = parse_command(f"pace-maker hook-model {ASTRA}")
        assert result["is_pace_maker_command"] is True

    def test_cli_execute_succeeds(self):
        from pacemaker.user_commands import _execute_hook_model

        config_path = _make_config()
        try:
            assert _execute_hook_model(config_path, ASTRA)["success"] is True
        finally:
            os.unlink(config_path)

    def test_cli_execute_sets_hook_model(self):
        """The selected token must actually be persisted to config."""
        from pacemaker.user_commands import _execute_hook_model

        config_path = _make_config()
        try:
            # Assert success first: otherwise a failed execution surfaces as a
            # confusing assertion on stale config instead of the real cause.
            assert _execute_hook_model(config_path, ASTRA)["success"] is True
            with open(config_path) as f:
                assert json.load(f)["hook_model"] == ASTRA
        finally:
            os.unlink(config_path)

    def test_help_text_lists_astra(self):
        from pacemaker import user_commands

        src = pathlib.Path(user_commands.__file__).read_text()
        assert f"hook-model {ASTRA}" in src


class TestReviewerLabelUnchanged:
    def test_label_stays_codex_gpt5(self):
        """Deliberate: the label denotes 'served by the Codex CLI', not a model
        generation. A new label would render untagged in the claude-usage
        governance feed, whose fallback only matches a 'codex-' prefix.
        """
        from pacemaker.inference import registry

        assert registry._REVIEWER_CODEX == "codex-gpt5"
