"""Tests for AgyProvider inference provider (Story #72)."""

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from pacemaker.inference.agy_provider import AgyProvider, _MODEL_MAP
from pacemaker.inference.provider import ProviderError


class TestAgyProviderModelMap:
    """Tests for the MODEL_MAP entries."""

    def test_bare_agy_maps_to_none(self):
        assert _MODEL_MAP["agy"] is None

    def test_agy_flash_maps_to_gemini_35_flash_medium(self):
        assert _MODEL_MAP["agy-flash"] == "Gemini 3.5 Flash (Medium)"

    def test_agy_flash_low_maps_correctly(self):
        assert _MODEL_MAP["agy-flash-low"] == "Gemini 3.5 Flash (Low)"

    def test_agy_flash_medium_maps_correctly(self):
        assert _MODEL_MAP["agy-flash-medium"] == "Gemini 3.5 Flash (Medium)"

    def test_agy_flash_high_maps_correctly(self):
        assert _MODEL_MAP["agy-flash-high"] == "Gemini 3.5 Flash (High)"

    def test_agy_pro_maps_to_gemini_31_pro_high(self):
        assert _MODEL_MAP["agy-pro"] == "Gemini 3.1 Pro (High)"

    def test_agy_pro_low_maps_correctly(self):
        assert _MODEL_MAP["agy-pro-low"] == "Gemini 3.1 Pro (Low)"

    def test_agy_pro_high_maps_correctly(self):
        assert _MODEL_MAP["agy-pro-high"] == "Gemini 3.1 Pro (High)"

    def test_agy_gpt_oss_maps_correctly(self):
        assert _MODEL_MAP["agy-gpt-oss"] == "GPT-OSS 120B (Medium)"

    def test_agy_sonnet_maps_correctly(self):
        assert _MODEL_MAP["agy-sonnet"] == "Claude Sonnet 4.6 (Thinking)"

    def test_agy_opus_maps_correctly(self):
        assert _MODEL_MAP["agy-opus"] == "Claude Opus 4.6 (Thinking)"

    def test_all_11_entries_present(self):
        expected_keys = {
            "agy",
            "agy-flash",
            "agy-flash-low",
            "agy-flash-medium",
            "agy-flash-high",
            "agy-pro",
            "agy-pro-low",
            "agy-pro-high",
            "agy-gpt-oss",
            "agy-sonnet",
            "agy-opus",
        }
        assert set(_MODEL_MAP.keys()) == expected_keys


class TestAgyProviderCommandConstruction:
    """Tests for correct CLI command construction."""

    def test_bare_agy_command_has_no_model_flag(self):
        """Bare 'agy' model hint must NOT include --model flag."""
        provider = AgyProvider()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "APPROVED"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            provider.query("test prompt", model_hint="agy")
            cmd = mock_run.call_args[0][0]
            assert "--model" not in cmd
            assert cmd[0] == "agy"
            assert cmd[1] == "--print"

    def test_agy_flash_high_command_includes_model_flag(self):
        """agy-flash-high must include --model 'Gemini 3.5 Flash (High)'."""
        provider = AgyProvider()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "APPROVED"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            provider.query("test prompt", model_hint="agy-flash-high")
            cmd = mock_run.call_args[0][0]
            assert "--model" in cmd
            model_idx = cmd.index("--model")
            assert cmd[model_idx + 1] == "Gemini 3.5 Flash (High)"

    def test_agy_pro_command_includes_model_flag(self):
        """agy-pro must include --model 'Gemini 3.1 Pro (High)'."""
        provider = AgyProvider()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "APPROVED"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            provider.query("test prompt", model_hint="agy-pro")
            cmd = mock_run.call_args[0][0]
            assert "--model" in cmd
            model_idx = cmd.index("--model")
            assert cmd[model_idx + 1] == "Gemini 3.1 Pro (High)"

    def test_agy_gpt_oss_command_includes_model_flag(self):
        """agy-gpt-oss must include --model 'GPT-OSS 120B (Medium)'."""
        provider = AgyProvider()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "APPROVED"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            provider.query("test prompt", model_hint="agy-gpt-oss")
            cmd = mock_run.call_args[0][0]
            assert "--model" in cmd
            model_idx = cmd.index("--model")
            assert cmd[model_idx + 1] == "GPT-OSS 120B (Medium)"

    def test_prompt_passed_as_positional_arg_to_print(self):
        """Prompt must be passed directly after --print."""
        provider = AgyProvider()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "APPROVED"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            provider.query("my test prompt", model_hint="agy-flash")
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "agy"
            assert cmd[1] == "--print"
            assert cmd[2] == "my test prompt"

    def test_unknown_model_hint_defaults_to_flash_medium(self):
        """Unknown model_hint must use 'Gemini 3.5 Flash (Medium)' as default."""
        provider = AgyProvider()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "APPROVED"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            provider.query("test prompt", model_hint="totally-unknown-model")
            cmd = mock_run.call_args[0][0]
            assert "--model" in cmd
            model_idx = cmd.index("--model")
            assert cmd[model_idx + 1] == "Gemini 3.5 Flash (Medium)"


class TestAgyProviderSystemPrompt:
    """Tests for system prompt embedding."""

    def test_system_prompt_embedded_when_provided(self):
        """System prompt must be prepended to user prompt."""
        provider = AgyProvider()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "APPROVED"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            provider.query(
                "user request", system_prompt="system instructions", model_hint="agy"
            )
            cmd = mock_run.call_args[0][0]
            full_prompt = cmd[2]  # after agy --print
            assert "SYSTEM INSTRUCTIONS:" in full_prompt
            assert "system instructions" in full_prompt
            assert "USER REQUEST:" in full_prompt
            assert "user request" in full_prompt

    def test_no_system_prompt_uses_raw_prompt(self):
        """Without system prompt, raw prompt must be passed directly."""
        provider = AgyProvider()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "APPROVED"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            provider.query("just the user prompt", model_hint="agy")
            cmd = mock_run.call_args[0][0]
            full_prompt = cmd[2]
            assert full_prompt == "just the user prompt"
            assert "SYSTEM INSTRUCTIONS:" not in full_prompt

    def test_empty_system_prompt_uses_raw_prompt(self):
        """Empty string system_prompt must be treated same as no system prompt."""
        provider = AgyProvider()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "APPROVED"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            provider.query("plain prompt", system_prompt="", model_hint="agy")
            cmd = mock_run.call_args[0][0]
            full_prompt = cmd[2]
            assert full_prompt == "plain prompt"


class TestAgyProviderFailureModes:
    """Tests for all 5 ProviderError failure modes."""

    def test_timeout_raises_provider_error(self):
        """TimeoutExpired must raise ProviderError with 'timed out' message."""
        provider = AgyProvider()
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["agy"], timeout=120),
        ):
            with pytest.raises(ProviderError, match="timed out"):
                provider.query("prompt", model_hint="agy-flash")

    def test_file_not_found_raises_provider_error(self):
        """FileNotFoundError must raise ProviderError with 'not installed' message."""
        provider = AgyProvider()
        with patch("subprocess.run", side_effect=FileNotFoundError("agy not found")):
            with pytest.raises(ProviderError, match="not installed"):
                provider.query("prompt", model_hint="agy-flash")

    def test_os_error_raises_provider_error(self):
        """Generic OSError must raise ProviderError."""
        provider = AgyProvider()
        with patch("subprocess.run", side_effect=OSError("permission denied")):
            with pytest.raises(ProviderError, match="OS error"):
                provider.query("prompt", model_hint="agy")

    def test_nonzero_returncode_raises_provider_error(self):
        """Non-zero returncode must raise ProviderError with exit code."""
        provider = AgyProvider()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error output"
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(ProviderError, match="exit 1"):
                provider.query("prompt", model_hint="agy-pro")

    def test_empty_stdout_raises_provider_error(self):
        """Empty stdout (returncode=0) must raise ProviderError."""
        provider = AgyProvider()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(ProviderError, match="empty response"):
                provider.query("prompt", model_hint="agy-flash-high")

    def test_whitespace_only_stdout_raises_provider_error(self):
        """Whitespace-only stdout must raise ProviderError (strip makes it empty)."""
        provider = AgyProvider()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "   \n   "
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(ProviderError, match="empty response"):
                provider.query("prompt", model_hint="agy")


class TestAgyProviderSuccessPath:
    """Tests for successful response handling."""

    def test_successful_response_stripped(self):
        """Response is stripped of leading/trailing whitespace."""
        provider = AgyProvider()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "  APPROVED  \n"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = provider.query("prompt", model_hint="agy-sonnet")
            assert result == "APPROVED"

    def test_returns_full_response_text(self):
        """Full response text is returned, not just first line."""
        provider = AgyProvider()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Line1\nLine2\nLine3"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = provider.query("prompt", model_hint="agy-opus")
            assert result == "Line1\nLine2\nLine3"
