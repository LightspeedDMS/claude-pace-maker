"""Tests for codex-<profile> provider plumbing (Story #74).

Covers:
  - model_aliases.is_known_model()
  - codex_provider._parse_codex_target()
  - CodexProvider.query() argv in profile vs non-profile mode
  - registry.get_provider() routing for codex-<profile>
  - resolve_and_call_with_reviewer() reviewer label for codex-<profile>
"""

import subprocess
from unittest.mock import patch, MagicMock

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Part 1: model_aliases.py — is_known_model()
# ─────────────────────────────────────────────────────────────────────────────


class TestIsKnownModel:
    """Tests for is_known_model() — Story #74."""

    def test_all_known_models_accepted(self):
        """Every token currently in KNOWN_MODELS must be accepted."""
        from pacemaker.inference.model_aliases import is_known_model, KNOWN_MODELS

        for token in KNOWN_MODELS:
            assert is_known_model(
                token
            ), f"Expected is_known_model('{token}') to be True"

    # SHORT_ALIASES keys are valid CLI tokens but NOT in KNOWN_MODELS;
    # is_known_model must accept them so story #75 CLI does not regress.
    def test_codex_alias_accepted(self):
        from pacemaker.inference.model_aliases import is_known_model

        assert is_known_model("codex") is True

    def test_gpt5_alias_accepted(self):
        from pacemaker.inference.model_aliases import is_known_model

        assert is_known_model("gpt-5") is True

    def test_gpt_alias_accepted(self):
        from pacemaker.inference.model_aliases import is_known_model

        assert is_known_model("gpt") is True

    def test_gem_flash_alias_accepted(self):
        from pacemaker.inference.model_aliases import is_known_model

        assert is_known_model("gem-flash") is True

    def test_gem_pro_alias_accepted(self):
        from pacemaker.inference.model_aliases import is_known_model

        assert is_known_model("gem-pro") is True

    # codex-<profile> tokens — valid shapes
    def test_codex_beast_accepted(self):
        from pacemaker.inference.model_aliases import is_known_model

        assert is_known_model("codex-beast") is True

    def test_codex_local_llama_accepted(self):
        from pacemaker.inference.model_aliases import is_known_model

        assert is_known_model("codex-local-llama") is True

    def test_codex_profile_with_dots_accepted(self):
        from pacemaker.inference.model_aliases import is_known_model

        assert is_known_model("codex-my.profile") is True

    def test_codex_profile_with_underscores_accepted(self):
        from pacemaker.inference.model_aliases import is_known_model

        assert is_known_model("codex-my_profile") is True

    def test_codex_profile_alphanumeric_only_accepted(self):
        from pacemaker.inference.model_aliases import is_known_model

        assert is_known_model("codex-abc123") is True

    # codex-<profile> tokens — invalid shapes
    def test_codex_bare_hyphen_rejected(self):
        """'codex-' (empty profile part) must be rejected."""
        from pacemaker.inference.model_aliases import is_known_model

        assert is_known_model("codex-") is False

    def test_codex_profile_slash_rejected(self):
        """'codex-bad/char' must be rejected — slash not in allowed chars."""
        from pacemaker.inference.model_aliases import is_known_model

        assert is_known_model("codex-bad/char") is False

    def test_codex_profile_space_rejected(self):
        """'codex-my profile' must be rejected — space not in allowed chars."""
        from pacemaker.inference.model_aliases import is_known_model

        assert is_known_model("codex-my profile") is False

    def test_completely_unknown_token_rejected(self):
        from pacemaker.inference.model_aliases import is_known_model

        assert is_known_model("completely-unknown-xyz") is False

    def test_empty_string_rejected(self):
        from pacemaker.inference.model_aliases import is_known_model

        assert is_known_model("") is False


# ─────────────────────────────────────────────────────────────────────────────
# Part 2: codex_provider.py — _parse_codex_target()
# ─────────────────────────────────────────────────────────────────────────────


class TestParseCodexTarget:
    """Tests for _parse_codex_target() — Story #74."""

    def test_codex_beast_returns_profile_tuple(self):
        from pacemaker.inference.codex_provider import _parse_codex_target

        profile, model = _parse_codex_target("codex-beast")
        assert profile == "beast"
        assert model is None

    def test_codex_local_llama_returns_profile_tuple(self):
        from pacemaker.inference.codex_provider import _parse_codex_target

        profile, model = _parse_codex_target("codex-local-llama")
        assert profile == "local-llama"
        assert model is None

    def test_codex_multipart_profile_preserves_full_name(self):
        from pacemaker.inference.codex_provider import _parse_codex_target

        profile, model = _parse_codex_target("codex-my.special_profile")
        assert profile == "my.special_profile"
        assert model is None

    def test_gpt55_returns_model_tuple(self):
        from pacemaker.inference.codex_provider import _parse_codex_target

        profile, model = _parse_codex_target("gpt-5.5")
        assert profile is None
        assert model == "gpt-5.5"

    def test_codex_alias_resolves_to_gpt55(self):
        """Plain 'codex' alias → (None, 'gpt-5.5') via SHORT_ALIASES."""
        from pacemaker.inference.codex_provider import _parse_codex_target

        profile, model = _parse_codex_target("codex")
        assert profile is None
        assert model == "gpt-5.5"

    def test_gpt5_alias_resolves_to_gpt55(self):
        from pacemaker.inference.codex_provider import _parse_codex_target

        profile, model = _parse_codex_target("gpt-5")
        assert profile is None
        assert model == "gpt-5.5"

    def test_gpt54_passed_through(self):
        from pacemaker.inference.codex_provider import _parse_codex_target

        profile, model = _parse_codex_target("gpt-5.4")
        assert profile is None
        assert model == "gpt-5.4"

    def test_empty_hint_defaults_to_o3(self):
        from pacemaker.inference.codex_provider import _parse_codex_target

        profile, model = _parse_codex_target("")
        assert profile is None
        assert model == "o3"

    def test_unknown_hint_passed_through(self):
        """Unknown hint with no alias is passed through as-is."""
        from pacemaker.inference.codex_provider import _parse_codex_target

        profile, model = _parse_codex_target("o3-mini")
        assert profile is None
        assert model == "o3-mini"


# ─────────────────────────────────────────────────────────────────────────────
# Part 3: codex_provider.py — CodexProvider.query() argv construction
# ─────────────────────────────────────────────────────────────────────────────


class TestCodexProviderProfileArgv:
    """Tests for argv construction in profile mode vs non-profile mode — Story #74."""

    @staticmethod
    def _mock_success():
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "APPROVED"
        mock_result.stderr = ""
        return mock_result

    def test_codex_beast_uses_profile_flag(self):
        """codex-beast → --profile beast in argv."""
        from pacemaker.inference.codex_provider import CodexProvider

        provider = CodexProvider()
        with patch(
            "pacemaker.inference.codex_provider.subprocess.run",
            return_value=self._mock_success(),
        ) as mock_run:
            provider.query("test prompt", model_hint="codex-beast")
            cmd = mock_run.call_args[0][0]
        assert "--profile" in cmd
        assert cmd[cmd.index("--profile") + 1] == "beast"

    def test_codex_beast_has_no_m_flag(self):
        """codex-beast → NO -m flag in argv."""
        from pacemaker.inference.codex_provider import CodexProvider

        provider = CodexProvider()
        with patch(
            "pacemaker.inference.codex_provider.subprocess.run",
            return_value=self._mock_success(),
        ) as mock_run:
            provider.query("test prompt", model_hint="codex-beast")
            cmd = mock_run.call_args[0][0]
        assert "-m" not in cmd

    def test_codex_local_llama_profile_flag_is_local_llama(self):
        """codex-local-llama → --profile local-llama in argv."""
        from pacemaker.inference.codex_provider import CodexProvider

        provider = CodexProvider()
        with patch(
            "pacemaker.inference.codex_provider.subprocess.run",
            return_value=self._mock_success(),
        ) as mock_run:
            provider.query("test prompt", model_hint="codex-local-llama")
            cmd = mock_run.call_args[0][0]
        assert "--profile" in cmd
        assert cmd[cmd.index("--profile") + 1] == "local-llama"

    def test_profile_mode_argv_structure(self):
        """Profile-mode argv must be: codex exec - --profile <name> -s read-only."""
        from pacemaker.inference.codex_provider import CodexProvider

        provider = CodexProvider()
        with patch(
            "pacemaker.inference.codex_provider.subprocess.run",
            return_value=self._mock_success(),
        ) as mock_run:
            provider.query("test prompt", model_hint="codex-beast")
            cmd = mock_run.call_args[0][0]
        assert cmd[0] == "codex"
        assert "exec" in cmd
        assert "-" in cmd
        assert "-s" in cmd
        assert "read-only" in cmd

    def test_plain_gpt55_uses_m_flag_not_profile_flag(self):
        """gpt-5.5 → -m gpt-5.5, no --profile."""
        from pacemaker.inference.codex_provider import CodexProvider

        provider = CodexProvider()
        with patch(
            "pacemaker.inference.codex_provider.subprocess.run",
            return_value=self._mock_success(),
        ) as mock_run:
            provider.query("test prompt", model_hint="gpt-5.5")
            cmd = mock_run.call_args[0][0]
        assert "-m" in cmd
        assert cmd[cmd.index("-m") + 1] == "gpt-5.5"
        assert "--profile" not in cmd

    def test_codex_alias_uses_m_flag_resolves_to_gpt55(self):
        """Plain 'codex' alias → -m gpt-5.5, no --profile."""
        from pacemaker.inference.codex_provider import CodexProvider

        provider = CodexProvider()
        with patch(
            "pacemaker.inference.codex_provider.subprocess.run",
            return_value=self._mock_success(),
        ) as mock_run:
            provider.query("test prompt", model_hint="codex")
            cmd = mock_run.call_args[0][0]
        assert "-m" in cmd
        assert cmd[cmd.index("-m") + 1] == "gpt-5.5"
        assert "--profile" not in cmd

    def test_profile_mode_has_skip_git_repo_check_flag(self):
        """Profile-mode argv must include --skip-git-repo-check positioned right after 'exec'.

        Rationale: codex 0.139 refuses to run from directories not in its trusted list
        unless --skip-git-repo-check is passed.  The flag is safe here because codex is
        already invoked with -s read-only (sandbox), so the git-repo guard provides no
        additional protection.
        """
        from pacemaker.inference.codex_provider import CodexProvider

        provider = CodexProvider()
        with patch(
            "pacemaker.inference.codex_provider.subprocess.run",
            return_value=self._mock_success(),
        ) as mock_run:
            provider.query("test prompt", model_hint="codex-beast")
            cmd = mock_run.call_args[0][0]
        assert (
            "--skip-git-repo-check" in cmd
        ), "--skip-git-repo-check must be present in profile-mode argv"
        exec_idx = cmd.index("exec")
        assert (
            cmd[exec_idx + 1] == "--skip-git-repo-check"
        ), f"--skip-git-repo-check must immediately follow 'exec', but got: {cmd}"

    def test_non_profile_mode_has_skip_git_repo_check_flag(self):
        """Non-profile-mode argv must include --skip-git-repo-check positioned right after 'exec'.

        Same rationale as the profile-mode test: without this flag, codex 0.139 fails
        with exit 1 from non-trusted directories, causing silent SDK fallback.
        """
        from pacemaker.inference.codex_provider import CodexProvider

        provider = CodexProvider()
        with patch(
            "pacemaker.inference.codex_provider.subprocess.run",
            return_value=self._mock_success(),
        ) as mock_run:
            provider.query("test prompt", model_hint="gpt-5.5")
            cmd = mock_run.call_args[0][0]
        assert (
            "--skip-git-repo-check" in cmd
        ), "--skip-git-repo-check must be present in non-profile-mode argv"
        exec_idx = cmd.index("exec")
        assert (
            cmd[exec_idx + 1] == "--skip-git-repo-check"
        ), f"--skip-git-repo-check must immediately follow 'exec', but got: {cmd}"

    # All 5 ProviderError cases in profile mode
    def test_profile_mode_timeout_raises_provider_error(self):
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.provider import ProviderError

        provider = CodexProvider()
        with patch(
            "pacemaker.inference.codex_provider.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["codex"], timeout=120),
        ):
            with pytest.raises(ProviderError, match="timed out"):
                provider.query("prompt", model_hint="codex-beast")

    def test_profile_mode_file_not_found_raises_provider_error(self):
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.provider import ProviderError

        provider = CodexProvider()
        with patch(
            "pacemaker.inference.codex_provider.subprocess.run",
            side_effect=FileNotFoundError("codex not found"),
        ):
            with pytest.raises(ProviderError, match="not found"):
                provider.query("prompt", model_hint="codex-beast")

    def test_profile_mode_os_error_raises_provider_error(self):
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.provider import ProviderError

        provider = CodexProvider()
        with patch(
            "pacemaker.inference.codex_provider.subprocess.run",
            side_effect=OSError("permission denied"),
        ):
            with pytest.raises(ProviderError, match="OS error"):
                provider.query("prompt", model_hint="codex-beast")

    def test_profile_mode_nonzero_returncode_raises_provider_error(self):
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.provider import ProviderError

        provider = CodexProvider()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "unknown profile: beast"
        mock_result.stdout = ""
        with patch(
            "pacemaker.inference.codex_provider.subprocess.run",
            return_value=mock_result,
        ):
            with pytest.raises(ProviderError, match="exit 1"):
                provider.query("prompt", model_hint="codex-beast")

    def test_profile_mode_empty_stdout_raises_provider_error(self):
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.provider import ProviderError

        provider = CodexProvider()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        with patch(
            "pacemaker.inference.codex_provider.subprocess.run",
            return_value=mock_result,
        ):
            with pytest.raises(ProviderError, match="empty response"):
                provider.query("prompt", model_hint="codex-beast")


# ─────────────────────────────────────────────────────────────────────────────
# Part 4: registry.py — get_provider() routing
# ─────────────────────────────────────────────────────────────────────────────


class TestGetProviderCodexProfile:
    """Tests for get_provider() routing with codex-<profile> — Story #74."""

    def test_codex_beast_routes_to_codex_provider(self):
        from pacemaker.inference.registry import get_provider
        from pacemaker.inference.codex_provider import CodexProvider

        assert isinstance(get_provider("codex-beast"), CodexProvider)

    def test_codex_local_llama_routes_to_codex_provider(self):
        from pacemaker.inference.registry import get_provider
        from pacemaker.inference.codex_provider import CodexProvider

        assert isinstance(get_provider("codex-local-llama"), CodexProvider)

    def test_plain_codex_alias_still_routes_to_codex_provider(self):
        """Regression: plain 'codex' must not be captured by codex-<profile> branch."""
        from pacemaker.inference.registry import get_provider
        from pacemaker.inference.codex_provider import CodexProvider

        assert isinstance(get_provider("codex"), CodexProvider)

    def test_gpt55_still_routes_to_codex_provider(self):
        """Regression: gpt-5.5 must still route correctly."""
        from pacemaker.inference.registry import get_provider
        from pacemaker.inference.codex_provider import CodexProvider

        assert isinstance(get_provider("gpt-5.5"), CodexProvider)


# ─────────────────────────────────────────────────────────────────────────────
# Part 5: registry.py — resolve_and_call_with_reviewer() reviewer label
# ─────────────────────────────────────────────────────────────────────────────


class TestCodexProfileReviewerLabel:
    """Tests for reviewer label for codex-<profile> — Story #74."""

    def test_codex_beast_reviewer_label_is_verbatim(self):
        from pacemaker.inference.registry import resolve_and_call_with_reviewer
        from pacemaker.inference.codex_provider import CodexProvider

        with patch.object(CodexProvider, "query", return_value="APPROVED"):
            response, reviewer = resolve_and_call_with_reviewer(
                "codex-beast", "test prompt", "sys prompt", "intent_validation"
            )
        assert response == "APPROVED"
        assert reviewer == "codex-beast"

    def test_codex_local_llama_reviewer_label_is_verbatim(self):
        from pacemaker.inference.registry import resolve_and_call_with_reviewer
        from pacemaker.inference.codex_provider import CodexProvider

        with patch.object(CodexProvider, "query", return_value="BLOCKED"):
            response, reviewer = resolve_and_call_with_reviewer(
                "codex-local-llama", "prompt", "", "stage2_unified"
            )
        assert reviewer == "codex-local-llama"

    def test_plain_codex_still_returns_codex_gpt5_label(self):
        """Regression: plain 'codex' alias must still map to 'codex-gpt5' label."""
        from pacemaker.inference.registry import resolve_and_call_with_reviewer
        from pacemaker.inference.codex_provider import CodexProvider

        with patch.object(CodexProvider, "query", return_value="APPROVED"):
            response, reviewer = resolve_and_call_with_reviewer(
                "codex", "prompt", "", "intent_validation"
            )
        assert reviewer == "codex-gpt5"

    def test_gpt55_still_returns_codex_gpt5_label(self):
        """Regression: gpt-5.5 must still map to 'codex-gpt5' label."""
        from pacemaker.inference.registry import resolve_and_call_with_reviewer
        from pacemaker.inference.codex_provider import CodexProvider

        with patch.object(CodexProvider, "query", return_value="APPROVED"):
            response, reviewer = resolve_and_call_with_reviewer(
                "gpt-5.5", "prompt", "", "intent_validation"
            )
        assert reviewer == "codex-gpt5"

    def test_codex_profile_failure_falls_back_to_anthropic_sdk(self):
        """When CodexProvider raises ProviderError for codex-<profile>, fallback → anthropic-sdk."""
        from pacemaker.inference.registry import resolve_and_call_with_reviewer
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider
        from pacemaker.inference.provider import ProviderError

        with patch.object(
            CodexProvider, "query", side_effect=ProviderError("unknown profile: beast")
        ):
            with patch.object(AnthropicProvider, "query", return_value="fallback"):
                response, reviewer = resolve_and_call_with_reviewer(
                    "codex-beast", "prompt", "", "intent_validation"
                )
        assert response == "fallback"
        assert reviewer == "anthropic-sdk"
