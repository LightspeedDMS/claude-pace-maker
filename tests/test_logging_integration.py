#!/usr/bin/env python3
"""Tests for centralized logging integration across modules."""

import os
import tempfile
from unittest.mock import patch


class TestLoggingIntegration:
    """Test that modules properly log errors."""

    def test_hook_load_config_logs_on_error(self):
        """hook.load_config should log warning on parse error."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("invalid json {{{")
            bad_config = f.name

        try:
            with patch("pacemaker.hook.log_warning") as mock_log:
                from pacemaker import hook

                # Force reimport to use our bad config
                result = hook.load_config(bad_config)
                # Should return defaults and log warning
                assert result.get("enabled") is not None
                mock_log.assert_called()
        finally:
            os.unlink(bad_config)

    def test_hook_load_state_logs_on_error(self):
        """hook.load_state should log warning on parse error."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("invalid json {{{")
            bad_state = f.name

        try:
            with patch("pacemaker.hook.log_warning") as mock_log:
                from pacemaker import hook

                result = hook.load_state(bad_state)
                # Should return defaults and log warning
                assert result.get("session_id") is not None
                mock_log.assert_called()
        finally:
            os.unlink(bad_state)

    def test_hook_save_state_logs_on_error(self):
        """hook.save_state should log warning on write error."""
        with patch("pacemaker.hook.log_warning") as mock_log:
            with patch("builtins.open", side_effect=PermissionError("denied")):
                from pacemaker import hook

                state = {"session_id": "test-123"}
                # Should catch exception and log warning
                hook.save_state(state, "/nonexistent/path/state.json")
                mock_log.assert_called()

    def test_hook_get_last_assistant_message_logs_on_error(self):
        """hook.get_last_assistant_message should log warning on error."""
        with patch("pacemaker.hook.log_warning") as mock_log:
            from pacemaker import hook

            result = hook.get_last_assistant_message("/nonexistent/transcript.jsonl")
            # Should return empty string and log warning
            assert result == ""
            mock_log.assert_called()

    def test_hook_get_last_n_messages_logs_on_error(self):
        """hook.get_last_n_messages should log warning on error."""
        with patch("pacemaker.hook.log_warning") as mock_log:
            from pacemaker import hook

            result = hook.get_last_n_messages("/nonexistent/transcript.jsonl", n=5)
            # Should return empty list and log warning
            assert result == []
            mock_log.assert_called()

    def test_api_client_logs_on_token_error(self):
        """api_client.load_access_token should log on error."""
        with patch("pacemaker.api_client.log_warning") as mock_log:
            with patch("pacemaker.api_client.Path") as mock_path:
                # Simulate file read error
                mock_path.home.return_value.__truediv__.return_value.exists.return_value = (
                    True
                )
                with patch("builtins.open", side_effect=PermissionError("denied")):
                    from pacemaker import api_client

                    result = api_client.load_access_token()
                    assert result is None
                    mock_log.assert_called()

    def test_api_client_parse_usage_logs_on_error(self):
        """api_client.parse_usage_response should log on error."""
        with patch("pacemaker.api_client.log_warning") as mock_log:
            from pacemaker import api_client

            # Invalid datetime format will trigger exception
            result = api_client.parse_usage_response(
                {"five_hour": {"resets_at": "invalid-datetime-format"}}
            )
            # Should return None and log warning
            assert result is None
            mock_log.assert_called()

    def test_api_client_fetch_usage_logs_on_error(self):
        """api_client.fetch_usage should log on error."""
        with patch("pacemaker.api_client.log_warning") as mock_log:
            with patch("pacemaker.api_client.requests.get") as mock_get:
                mock_get.side_effect = Exception("Network error")
                from pacemaker import api_client

                result = api_client.fetch_usage("fake-token")
                assert result is None
                mock_log.assert_called()

    def test_database_logs_error_on_initialize_failure(self):
        """database.initialize_database should log error on failure."""
        with patch("pacemaker.database.log_error") as mock_log:
            with patch("pacemaker.database.sqlite3.connect") as mock_connect:
                mock_connect.side_effect = Exception("db error")
                from pacemaker import database

                result = database.initialize_database("/nonexistent/path.db")
                assert result is False
                mock_log.assert_called()

    def test_database_logs_error_on_insert_failure(self):
        """database.insert_usage_snapshot should log error on failure."""
        with patch("pacemaker.database.log_error") as mock_log:
            with patch("pacemaker.database.sqlite3.connect") as mock_connect:
                mock_connect.side_effect = Exception("db error")
                from pacemaker import database
                from datetime import datetime

                result = database.insert_usage_snapshot(
                    "/nonexistent/path.db",
                    datetime.now(),
                    50.0,
                    None,
                    30.0,
                    None,
                    "test-session",
                )
                assert result is False
                mock_log.assert_called()

    def test_database_query_snapshots_logs_on_error(self):
        """database.query_recent_snapshots should log warning on error."""
        with patch("pacemaker.database.log_warning") as mock_log:
            with patch("pacemaker.database.sqlite3.connect") as mock_connect:
                mock_connect.side_effect = Exception("db error")
                from pacemaker import database

                result = database.query_recent_snapshots("/nonexistent/path.db")
                assert result == []
                mock_log.assert_called()

    def test_database_cleanup_logs_on_error(self):
        """database.cleanup_old_snapshots should log error on failure."""
        with patch("pacemaker.database.log_error") as mock_log:
            with patch("pacemaker.database.sqlite3.connect") as mock_connect:
                mock_connect.side_effect = Exception("db error")
                from pacemaker import database

                result = database.cleanup_old_snapshots("/nonexistent/path.db")
                assert result == -1
                mock_log.assert_called()

    def test_database_insert_decision_logs_on_error(self):
        """database.insert_pacing_decision should log error on failure."""
        with patch("pacemaker.database.log_error") as mock_log:
            with patch("pacemaker.database.sqlite3.connect") as mock_connect:
                mock_connect.side_effect = Exception("db error")
                from pacemaker import database
                from datetime import datetime

                result = database.insert_pacing_decision(
                    "/nonexistent/path.db",
                    datetime.now(),
                    True,
                    30,
                    "test-session",
                )
                assert result is False
                mock_log.assert_called()

    def test_database_get_decision_logs_on_error(self):
        """database.get_last_pacing_decision should log warning on error."""
        with patch("pacemaker.database.log_warning") as mock_log:
            with patch("pacemaker.database.sqlite3.connect") as mock_connect:
                mock_connect.side_effect = Exception("db error")
                from pacemaker import database

                result = database.get_last_pacing_decision(
                    "/nonexistent/path.db", "test-session"
                )
                assert result is None
                mock_log.assert_called()

    def test_transcript_reader_get_all_user_messages_logs_on_error(self):
        """transcript_reader.get_all_user_messages should log warning on error."""
        with patch("pacemaker.transcript_reader.log_warning") as mock_log:
            from pacemaker import transcript_reader

            result = transcript_reader.get_all_user_messages(
                "/nonexistent/transcript.jsonl"
            )
            assert result == []
            mock_log.assert_called()

    def test_transcript_reader_get_assistant_messages_logs_on_error(self):
        """transcript_reader.get_last_n_assistant_messages should log warning on error."""
        with patch("pacemaker.transcript_reader.log_warning") as mock_log:
            from pacemaker import transcript_reader

            result = transcript_reader.get_last_n_assistant_messages(
                "/nonexistent/transcript.jsonl"
            )
            assert result == []
            mock_log.assert_called()

    def test_transcript_reader_get_messages_for_validation_logs_on_error(self):
        """transcript_reader.get_last_n_messages_for_validation should log warning on error."""
        with patch("pacemaker.transcript_reader.log_warning") as mock_log:
            from pacemaker import transcript_reader

            result = transcript_reader.get_last_n_messages_for_validation(
                "/nonexistent/transcript.jsonl"
            )
            assert result == []
            mock_log.assert_called()

    def test_transcript_reader_build_context_logs_on_error(self):
        """transcript_reader.build_stop_hook_context should log warning on error."""
        with patch("pacemaker.transcript_reader.log_warning") as mock_log:
            from pacemaker import transcript_reader

            result = transcript_reader.build_stop_hook_context(
                "/nonexistent/transcript.jsonl"
            )
            # Should return empty context and log warning
            assert result["first_pairs"] == []
            assert result["backwards_messages"] == []
            mock_log.assert_called()

    def test_extension_registry_logs_on_error(self):
        """extension_registry.load_extensions should log warning on error."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("invalid json {{{")
            bad_config = f.name

        try:
            with patch("pacemaker.extension_registry.log_warning") as mock_log:
                from pacemaker import extension_registry

                result = extension_registry.load_extensions(bad_config)
                # Should return default extensions and log warning
                assert ".py" in result
                mock_log.assert_called()
        finally:
            os.unlink(bad_config)

    def test_code_reviewer_logs_on_file_not_found(self):
        """code_reviewer.validate_code_against_intent should log info on file not found."""
        with patch("pacemaker.code_reviewer.log_info") as mock_log:
            from pacemaker import code_reviewer

            result = code_reviewer.validate_code_against_intent(
                "/nonexistent/file.py", ["test message"]
            )
            # Should return empty string and log info
            assert result == ""
            mock_log.assert_called()

    def test_code_reviewer_logs_on_read_error(self):
        """code_reviewer.validate_code_against_intent should log warning on read error."""
        with patch("pacemaker.code_reviewer.log_warning") as mock_log:
            with patch("builtins.open", side_effect=PermissionError("denied")):
                from pacemaker import code_reviewer

                result = code_reviewer.validate_code_against_intent(
                    "/fake/file.py", ["test message"]
                )
                # Should return empty string and log warning
                assert result == ""
                mock_log.assert_called()

    def test_intent_validator_logs_on_validation_failure(self):
        """intent_validator functions should log warnings on SDK failures."""
        with patch("pacemaker.intent_validator.log_warning") as mock_log:
            with patch(
                "pacemaker.intent_validator._call_sdk_intent_validation"
            ) as mock_sdk:
                mock_sdk.side_effect = Exception("SDK error")
                from pacemaker import intent_validator

                result = intent_validator.validate_intent_declared(
                    ["message 1"], "/fake/file.py", "Write"
                )
                # Should return False and log warning
                assert result["intent_found"] is False
                mock_log.assert_called()

    def test_user_commands_get_latest_usage_logs_on_error(self):
        """user_commands._get_latest_usage should log warning on error."""
        with patch("pacemaker.user_commands.log_warning") as mock_log:
            with patch("sqlite3.connect") as mock_connect:
                mock_connect.side_effect = Exception("db error")
                from pacemaker import user_commands

                result = user_commands._get_latest_usage("/nonexistent/path.db")
                assert result is None
                mock_log.assert_called()
