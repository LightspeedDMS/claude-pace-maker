"""
Unit tests for secrets stats display in status output.

Tests that the status display shows:
- Masked (24h): count of secrets masked in last 24 hours
- Stored: count of secrets currently in database
"""

import os
import tempfile
from unittest.mock import patch


class TestFormatSecretsStats:
    """Test the _format_secrets_stats helper function."""

    def test_format_secrets_stats_shows_masked_count(self):
        """Test that masked count from 24h metrics is displayed."""
        from src.pacemaker.user_commands import _format_secrets_stats

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        try:
            with patch(
                "src.pacemaker.user_commands.get_24h_secrets_metrics",
                return_value={"secrets_masked": 42},
            ):
                with patch(
                    "src.pacemaker.user_commands.list_secrets",
                    return_value=[],
                ):
                    result = _format_secrets_stats(db_path)

            assert "Masked (24h):" in result
            assert "42" in result
        finally:
            os.remove(db_path)

    def test_format_secrets_stats_shows_stored_count(self):
        """Test that stored secrets count is displayed."""
        from src.pacemaker.user_commands import _format_secrets_stats

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        try:
            mock_secrets = [
                {"id": i, "type": "text", "value": f"secret{i}"} for i in range(1, 6)
            ]

            with patch(
                "src.pacemaker.user_commands.get_24h_secrets_metrics",
                return_value={"secrets_masked": 0},
            ):
                with patch(
                    "src.pacemaker.user_commands.list_secrets",
                    return_value=mock_secrets,
                ):
                    result = _format_secrets_stats(db_path)

            assert "Stored:" in result
            assert "5" in result
        finally:
            os.remove(db_path)

    def test_format_secrets_stats_handles_no_db_path(self):
        """Test that None db_path is handled gracefully."""
        from src.pacemaker.user_commands import _format_secrets_stats

        result = _format_secrets_stats(None)
        assert "Secrets" in result

    def test_format_secrets_stats_handles_errors_gracefully(self):
        """Test that database errors don't crash the function."""
        from src.pacemaker.user_commands import _format_secrets_stats

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        try:
            with patch(
                "src.pacemaker.user_commands.get_24h_secrets_metrics",
                side_effect=Exception("Database error"),
            ):
                result = _format_secrets_stats(db_path)

            assert "Secrets" in result
        finally:
            os.remove(db_path)

    def test_format_secrets_stats_section_format(self):
        """Test that the output format matches other status sections."""
        from src.pacemaker.user_commands import _format_secrets_stats

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        try:
            with patch(
                "src.pacemaker.user_commands.get_24h_secrets_metrics",
                return_value={"secrets_masked": 10},
            ):
                with patch(
                    "src.pacemaker.user_commands.list_secrets",
                    return_value=[{"id": 1, "type": "text", "value": "x"}],
                ):
                    result = _format_secrets_stats(db_path)

            assert result.startswith("\n")
            assert "Secrets:" in result
            masked_pos = result.find("Masked")
            stored_pos = result.find("Stored")
            assert masked_pos < stored_pos, "Masked should appear before Stored"
        finally:
            os.remove(db_path)


class TestStatusIncludesSecrets:
    """Test that _execute_status includes secrets stats."""

    @patch(
        "src.pacemaker.user_commands.list_secrets",
        return_value=[{"id": 1}, {"id": 2}, {"id": 3}],
    )
    @patch(
        "src.pacemaker.user_commands.get_24h_secrets_metrics",
        return_value={"secrets_masked": 25},
    )
    @patch(
        "src.pacemaker.user_commands._langfuse_test_connection",
        return_value={"connected": True, "message": "OK"},
    )
    @patch("src.pacemaker.user_commands._count_recent_errors", return_value=0)
    @patch("src.pacemaker.user_commands._get_latest_usage", return_value=None)
    def test_status_output_contains_secrets_section(self, *mocks):
        """Test that the main status output includes secrets stats."""
        from src.pacemaker.user_commands import _execute_status
        import json

        fd, config_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        try:
            with open(config_path, "w") as f:
                json.dump({"enabled": True}, f)

            result = _execute_status(config_path, db_path)

            assert result["success"] is True
            message = result["message"]
            assert "Secrets:" in message
            assert "Masked" in message
            assert "Stored" in message
        finally:
            os.remove(config_path)
            os.remove(db_path)
