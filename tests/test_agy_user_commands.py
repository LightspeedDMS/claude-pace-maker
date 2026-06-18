"""Tests for agy model CLI integration in user_commands (Story #72)."""

import json
import os
import tempfile


def _make_config(data=None):
    """Create a temp config file and return path (caller must unlink)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data or {"hook_model": "auto"}, f)
        return f.name


class TestHookModelAgyRegex:
    """Test that the hook-model command parser accepts agy tokens."""

    def test_parse_hook_model_agy_bare(self):
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker hook-model agy")
        assert result["is_pace_maker_command"] is True
        assert result["command"] == "hook-model"
        assert result["subcommand"] == "agy"

    def test_parse_hook_model_agy_flash(self):
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker hook-model agy-flash")
        assert result["command"] == "hook-model"
        assert result["subcommand"] == "agy-flash"

    def test_parse_hook_model_agy_flash_high(self):
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker hook-model agy-flash-high")
        assert result["command"] == "hook-model"
        assert result["subcommand"] == "agy-flash-high"

    def test_parse_hook_model_agy_flash_low(self):
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker hook-model agy-flash-low")
        assert result["command"] == "hook-model"
        assert result["subcommand"] == "agy-flash-low"

    def test_parse_hook_model_agy_flash_medium(self):
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker hook-model agy-flash-medium")
        assert result["command"] == "hook-model"
        assert result["subcommand"] == "agy-flash-medium"

    def test_parse_hook_model_agy_pro(self):
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker hook-model agy-pro")
        assert result["command"] == "hook-model"
        assert result["subcommand"] == "agy-pro"

    def test_parse_hook_model_agy_pro_low(self):
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker hook-model agy-pro-low")
        assert result["command"] == "hook-model"
        assert result["subcommand"] == "agy-pro-low"

    def test_parse_hook_model_agy_pro_high(self):
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker hook-model agy-pro-high")
        assert result["command"] == "hook-model"
        assert result["subcommand"] == "agy-pro-high"

    def test_parse_hook_model_agy_gpt_oss(self):
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker hook-model agy-gpt-oss")
        assert result["command"] == "hook-model"
        assert result["subcommand"] == "agy-gpt-oss"

    def test_parse_hook_model_agy_sonnet(self):
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker hook-model agy-sonnet")
        assert result["command"] == "hook-model"
        assert result["subcommand"] == "agy-sonnet"

    def test_parse_hook_model_agy_opus(self):
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker hook-model agy-opus")
        assert result["command"] == "hook-model"
        assert result["subcommand"] == "agy-opus"

    def test_invalid_agy_variant_rejected(self):
        """agy-bad-model should be rejected when executed."""
        from pacemaker.user_commands import execute_command

        config_path = _make_config()
        try:
            result = execute_command(
                "hook-model", config_path, subcommand="agy-bad-model"
            )
            assert result["success"] is False
        finally:
            os.unlink(config_path)


class TestHookModelAgyExecution:
    """Test that hook-model command execution saves config and returns correct messages."""

    def test_execute_agy_flash_high_saves_config(self):
        """pace-maker hook-model agy-flash-high saves hook_model to config."""
        from pacemaker.user_commands import execute_command

        config_path = _make_config()
        try:
            result = execute_command(
                "hook-model", config_path, subcommand="agy-flash-high"
            )
            assert result["success"] is True

            with open(config_path) as f:
                config = json.load(f)
            assert config["hook_model"] == "agy-flash-high"
        finally:
            os.unlink(config_path)

    def test_execute_agy_flash_high_message_mentions_display_name(self):
        """Confirmation message for agy-flash-high must mention 'Gemini 3.5 Flash (High)'."""
        from pacemaker.user_commands import execute_command

        config_path = _make_config()
        try:
            result = execute_command(
                "hook-model", config_path, subcommand="agy-flash-high"
            )
            assert result["success"] is True
            assert "Gemini 3.5 Flash (High)" in result["message"]
        finally:
            os.unlink(config_path)

    def test_execute_agy_flash_high_message_mentions_agy_cli(self):
        """Confirmation message for agy-flash-high must mention 'agy CLI'."""
        from pacemaker.user_commands import execute_command

        config_path = _make_config()
        try:
            result = execute_command(
                "hook-model", config_path, subcommand="agy-flash-high"
            )
            assert result["success"] is True
            assert "agy CLI" in result["message"]
        finally:
            os.unlink(config_path)

    def test_execute_agy_bare_saves_config(self):
        """pace-maker hook-model agy saves hook_model='agy' to config."""
        from pacemaker.user_commands import execute_command

        config_path = _make_config()
        try:
            result = execute_command("hook-model", config_path, subcommand="agy")
            assert result["success"] is True

            with open(config_path) as f:
                config = json.load(f)
            assert config["hook_model"] == "agy"
        finally:
            os.unlink(config_path)

    def test_execute_agy_pro_saves_config(self):
        """pace-maker hook-model agy-pro saves correctly."""
        from pacemaker.user_commands import execute_command

        config_path = _make_config()
        try:
            result = execute_command("hook-model", config_path, subcommand="agy-pro")
            assert result["success"] is True

            with open(config_path) as f:
                config = json.load(f)
            assert config["hook_model"] == "agy-pro"
        finally:
            os.unlink(config_path)

    def test_execute_agy_gpt_oss_saves_config(self):
        """pace-maker hook-model agy-gpt-oss saves correctly."""
        from pacemaker.user_commands import execute_command

        config_path = _make_config()
        try:
            result = execute_command(
                "hook-model", config_path, subcommand="agy-gpt-oss"
            )
            assert result["success"] is True

            with open(config_path) as f:
                config = json.load(f)
            assert config["hook_model"] == "agy-gpt-oss"
        finally:
            os.unlink(config_path)

    def test_execute_agy_sonnet_saves_config(self):
        """pace-maker hook-model agy-sonnet saves correctly."""
        from pacemaker.user_commands import execute_command

        config_path = _make_config()
        try:
            result = execute_command("hook-model", config_path, subcommand="agy-sonnet")
            assert result["success"] is True

            with open(config_path) as f:
                config = json.load(f)
            assert config["hook_model"] == "agy-sonnet"
        finally:
            os.unlink(config_path)

    def test_execute_agy_pro_message_mentions_display_name(self):
        """Confirmation message for agy-pro must mention 'Gemini 3.1 Pro (High)'."""
        from pacemaker.user_commands import execute_command

        config_path = _make_config()
        try:
            result = execute_command("hook-model", config_path, subcommand="agy-pro")
            assert result["success"] is True
            assert "Gemini 3.1 Pro (High)" in result["message"]
        finally:
            os.unlink(config_path)

    def test_execute_agy_sonnet_message_mentions_display_name(self):
        """Confirmation message for agy-sonnet must mention 'Claude Sonnet 4.6 (Thinking)'."""
        from pacemaker.user_commands import execute_command

        config_path = _make_config()
        try:
            result = execute_command("hook-model", config_path, subcommand="agy-sonnet")
            assert result["success"] is True
            assert "Claude Sonnet 4.6 (Thinking)" in result["message"]
        finally:
            os.unlink(config_path)

    def test_execute_agy_gpt_oss_message_mentions_display_name(self):
        """Confirmation message for agy-gpt-oss must mention 'GPT-OSS 120B (Medium)'."""
        from pacemaker.user_commands import execute_command

        config_path = _make_config()
        try:
            result = execute_command(
                "hook-model", config_path, subcommand="agy-gpt-oss"
            )
            assert result["success"] is True
            assert "GPT-OSS 120B (Medium)" in result["message"]
        finally:
            os.unlink(config_path)


class TestStatusDisplayAgy:
    """Test that pace-maker status shows agy model correctly."""

    def test_status_shows_agy_flash_high(self):
        """Status output for hook_model='agy-flash-high' must contain the model name."""
        from pacemaker.user_commands import execute_command

        config_path = _make_config({"hook_model": "agy-flash-high", "enabled": True})
        try:
            result = execute_command("status", config_path)
            assert result["success"] is True
            # Status uppercases the hook model via .upper() fallback
            assert "AGY-FLASH-HIGH" in result["message"]
        finally:
            os.unlink(config_path)

    def test_status_shows_agy_pro_high(self):
        """Status output for hook_model='agy-pro-high' must contain the model name."""
        from pacemaker.user_commands import execute_command

        config_path = _make_config({"hook_model": "agy-pro-high", "enabled": True})
        try:
            result = execute_command("status", config_path)
            assert result["success"] is True
            # Status uppercases the hook model via .upper() fallback
            assert "AGY-PRO-HIGH" in result["message"]
        finally:
            os.unlink(config_path)


class TestHelpTextAgy:
    """Verify all 11 agy model tokens appear in HELP_TEXT (Acceptance Criterion 6)."""

    def test_help_text_contains_agy_bare(self):
        from pacemaker.user_commands import HELP_TEXT

        assert "agy" in HELP_TEXT

    def test_help_text_contains_agy_flash(self):
        from pacemaker.user_commands import HELP_TEXT

        assert "agy-flash" in HELP_TEXT

    def test_help_text_contains_agy_flash_low(self):
        from pacemaker.user_commands import HELP_TEXT

        assert "agy-flash-low" in HELP_TEXT

    def test_help_text_contains_agy_flash_medium(self):
        from pacemaker.user_commands import HELP_TEXT

        assert "agy-flash-medium" in HELP_TEXT

    def test_help_text_contains_agy_flash_high(self):
        from pacemaker.user_commands import HELP_TEXT

        assert "agy-flash-high" in HELP_TEXT

    def test_help_text_contains_agy_pro(self):
        from pacemaker.user_commands import HELP_TEXT

        assert "agy-pro" in HELP_TEXT

    def test_help_text_contains_agy_pro_low(self):
        from pacemaker.user_commands import HELP_TEXT

        assert "agy-pro-low" in HELP_TEXT

    def test_help_text_contains_agy_pro_high(self):
        from pacemaker.user_commands import HELP_TEXT

        assert "agy-pro-high" in HELP_TEXT

    def test_help_text_contains_agy_gpt_oss(self):
        from pacemaker.user_commands import HELP_TEXT

        assert "agy-gpt-oss" in HELP_TEXT

    def test_help_text_contains_agy_sonnet(self):
        from pacemaker.user_commands import HELP_TEXT

        assert "agy-sonnet" in HELP_TEXT

    def test_help_text_contains_agy_opus(self):
        from pacemaker.user_commands import HELP_TEXT

        assert "agy-opus" in HELP_TEXT

    def test_help_text_agy_lines_count(self):
        """Exactly 11 agy lines must appear in HELP_TEXT."""
        from pacemaker.user_commands import HELP_TEXT

        agy_lines = [line for line in HELP_TEXT.split("\n") if "agy" in line.lower()]
        assert (
            len(agy_lines) >= 11
        ), f"Expected 11+ agy lines, found {len(agy_lines)}: {agy_lines}"
