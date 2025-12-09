#!/usr/bin/env python3
"""
Unit tests for run_pre_tool_hook() function in hook.py.
"""

import json
from unittest.mock import patch
from pacemaker.hook import run_pre_tool_hook


class TestPreToolHook:
    """Test run_pre_tool_hook() function."""

    @patch("pacemaker.hook.load_config")
    @patch("sys.stdin")
    def test_returns_continue_when_feature_disabled(self, mock_stdin, mock_load_config):
        """Should return continue=True when intent_validation_enabled is False."""
        hook_data = {
            "session_id": "test",
            "transcript_path": "/path/to/transcript.jsonl",
            "tool_name": "Write",
            "tool_input": {"file_path": "/path/to/test.py", "content": "code"},
        }
        mock_stdin.read.return_value = json.dumps(hook_data)
        mock_load_config.return_value = {"intent_validation_enabled": False}

        result = run_pre_tool_hook()

        assert result == {"continue": True}

    @patch("pacemaker.hook.load_config")
    @patch("pacemaker.extension_registry.load_extensions")
    @patch("pacemaker.extension_registry.is_source_code_file")
    @patch("sys.stdin")
    def test_returns_continue_for_non_source_files(
        self, mock_stdin, mock_is_source, mock_load_ext, mock_load_config
    ):
        """Should return continue=True for non-source code files."""
        hook_data = {
            "session_id": "test",
            "transcript_path": "/path/to/transcript.jsonl",
            "tool_name": "Write",
            "tool_input": {"file_path": "/path/to/readme.md", "content": "docs"},
        }
        mock_stdin.read.return_value = json.dumps(hook_data)
        mock_load_config.return_value = {"intent_validation_enabled": True}
        mock_load_ext.return_value = [".py", ".js"]
        mock_is_source.return_value = False

        result = run_pre_tool_hook()

        assert result == {"continue": True}

    @patch("pacemaker.hook.load_config")
    @patch("pacemaker.extension_registry.load_extensions")
    @patch("pacemaker.extension_registry.is_source_code_file")
    @patch("pacemaker.hook.get_last_n_messages_for_validation")
    @patch("pacemaker.intent_validator.validate_intent_and_code")
    @patch("sys.stdin")
    def test_blocks_when_validation_fails(
        self,
        mock_stdin,
        mock_validate,
        mock_get_messages,
        mock_is_source,
        mock_load_ext,
        mock_load_config,
    ):
        """Should block tool use when validation fails."""
        hook_data = {
            "session_id": "test",
            "transcript_path": "/path/to/transcript.jsonl",
            "tool_name": "Write",
            "tool_input": {"file_path": "/path/to/test.py", "content": "code"},
        }
        mock_stdin.read.return_value = json.dumps(hook_data)
        mock_load_config.return_value = {"intent_validation_enabled": True}
        mock_load_ext.return_value = [".py"]
        mock_is_source.return_value = True
        mock_get_messages.return_value = ["Some message"]
        mock_validate.return_value = {
            "approved": False,
            "feedback": "Intent declaration required",
        }

        result = run_pre_tool_hook()

        assert result["decision"] == "block"
        assert "reason" in result
        assert "Intent declaration required" in result["reason"]

    @patch("pacemaker.hook.load_config")
    @patch("pacemaker.extension_registry.load_extensions")
    @patch("pacemaker.extension_registry.is_source_code_file")
    @patch("pacemaker.hook.get_last_n_messages_for_validation")
    @patch("pacemaker.intent_validator.validate_intent_and_code")
    @patch("sys.stdin")
    def test_allows_when_validation_passes(
        self,
        mock_stdin,
        mock_validate,
        mock_get_messages,
        mock_is_source,
        mock_load_ext,
        mock_load_config,
    ):
        """Should allow tool use when validation passes."""
        hook_data = {
            "session_id": "test",
            "transcript_path": "/path/to/transcript.jsonl",
            "tool_name": "Write",
            "tool_input": {"file_path": "/path/to/test.py", "content": "code"},
        }
        mock_stdin.read.return_value = json.dumps(hook_data)
        mock_load_config.return_value = {"intent_validation_enabled": True}
        mock_load_ext.return_value = [".py"]
        mock_is_source.return_value = True
        mock_get_messages.return_value = ["I will modify test.py to add logging"]
        mock_validate.return_value = {"approved": True}

        result = run_pre_tool_hook()

        assert result == {"continue": True}

    @patch("pacemaker.hook.load_config")
    @patch("pacemaker.extension_registry.load_extensions")
    @patch("pacemaker.extension_registry.is_source_code_file")
    @patch("pacemaker.hook.get_last_n_messages_for_validation")
    @patch("pacemaker.intent_validator.validate_intent_and_code")
    @patch("sys.stdin")
    def test_reads_last_5_messages(
        self,
        mock_stdin,
        mock_validate,
        mock_get_messages,
        mock_is_source,
        mock_load_ext,
        mock_load_config,
    ):
        """Should read last 4 messages from transcript for validation (changed from 5)."""
        hook_data = {
            "session_id": "test",
            "transcript_path": "/tmp/transcript.jsonl",
            "tool_name": "Write",
            "tool_input": {"file_path": "/path/to/test.py", "content": "code"},
        }
        mock_stdin.read.return_value = json.dumps(hook_data)
        mock_load_config.return_value = {"intent_validation_enabled": True}
        mock_load_ext.return_value = [".py"]
        mock_is_source.return_value = True
        mock_get_messages.return_value = ["msg1", "msg2", "msg3", "msg4"]
        mock_validate.return_value = {"approved": True}

        run_pre_tool_hook()

        # Verify get_last_n_messages_for_validation was called with n=4
        mock_get_messages.assert_called_once_with("/tmp/transcript.jsonl", n=4)

    @patch("pacemaker.hook.load_config")
    @patch("pacemaker.extension_registry.load_extensions")
    @patch("pacemaker.extension_registry.is_source_code_file")
    @patch("pacemaker.hook.get_last_n_messages_for_validation")
    @patch("pacemaker.intent_validator.validate_intent_and_code")
    @patch("sys.stdin")
    def test_passes_correct_args_to_validate_intent_and_code(
        self,
        mock_stdin,
        mock_validate,
        mock_get_messages,
        mock_is_source,
        mock_load_ext,
        mock_load_config,
    ):
        """Should pass messages, code, file_path, and tool_name to validate_intent_and_code."""
        hook_data = {
            "session_id": "test",
            "transcript_path": "/tmp/transcript.jsonl",
            "tool_name": "Edit",
            "tool_input": {"file_path": "/path/to/config.py", "new_string": "new code"},
        }
        mock_stdin.read.return_value = json.dumps(hook_data)
        mock_load_config.return_value = {"intent_validation_enabled": True}
        mock_load_ext.return_value = [".py"]
        mock_is_source.return_value = True
        messages = ["I will edit config.py"]
        mock_get_messages.return_value = messages
        mock_validate.return_value = {"approved": True}

        run_pre_tool_hook()

        # Verify validate_intent_and_code received correct args
        mock_validate.assert_called_once_with(
            messages=messages,
            code="new code",
            file_path="/path/to/config.py",
            tool_name="Edit",
        )

    @patch("pacemaker.hook.load_config")
    @patch("pacemaker.extension_registry.load_extensions")
    @patch("pacemaker.extension_registry.is_source_code_file")
    @patch("pacemaker.hook.get_last_n_messages_for_validation")
    @patch("pacemaker.intent_validator.SDK_AVAILABLE", False)
    @patch("sys.stdin")
    def test_hook_fails_closed_when_sdk_unavailable(
        self,
        mock_stdin,
        mock_get_messages,
        mock_is_source,
        mock_load_ext,
        mock_load_config,
    ):
        """Should block Write with fail-closed message when SDK is unavailable."""
        hook_data = {
            "session_id": "test",
            "transcript_path": "/tmp/transcript.jsonl",
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/path/to/test.py",
                "content": "def foo(): pass",
            },
        }
        mock_stdin.read.return_value = json.dumps(hook_data)
        mock_load_config.return_value = {"intent_validation_enabled": True}
        mock_load_ext.return_value = [".py"]
        mock_is_source.return_value = True
        mock_get_messages.return_value = [
            "I will modify test.py to add foo() function that does nothing"
        ]

        result = run_pre_tool_hook()

        # Should block with fail-closed message when SDK unavailable
        assert result["decision"] == "block"
        assert "SDK is not available" in result["reason"]
