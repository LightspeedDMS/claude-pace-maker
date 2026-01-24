#!/usr/bin/env python3
"""
Tests for pace-maker version command.
"""

import pytest
from pacemaker import user_commands


class TestVersionCommand:
    """Test version command parsing and execution."""

    def test_parse_version_command(self):
        """'pace-maker version' should be recognized."""
        result = user_commands.parse_command("pace-maker version")
        assert result["is_pace_maker_command"] is True
        assert result["command"] == "version"

    def test_parse_version_command_with_whitespace(self):
        """Handle extra whitespace in version command."""
        result = user_commands.parse_command("pace-maker  version")
        assert result["is_pace_maker_command"] is True
        assert result["command"] == "version"

    def test_execute_version_command(self):
        """Execute version command returns version string."""
        result = user_commands.execute_command(
            "version", "/tmp/config.json", "/tmp/db.sqlite"
        )
        assert result["success"] is True
        assert "1.4.0" in result["message"]
        assert "Claude Pace Maker" in result["message"]

    def test_handle_user_prompt_version(self):
        """Full integration test for version command."""
        result = user_commands.handle_user_prompt(
            "pace-maker version", "/tmp/config.json", "/tmp/db.sqlite"
        )
        assert result["intercepted"] is True
        assert "1.4.0" in result["output"]

    def test_help_includes_version_command(self):
        """Help text should document version command."""
        result = user_commands.handle_user_prompt(
            "pace-maker help", "/tmp/config.json", "/tmp/db.sqlite"
        )
        assert result["intercepted"] is True
        assert "pace-maker version" in result["output"]
        assert "version" in result["output"].lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
