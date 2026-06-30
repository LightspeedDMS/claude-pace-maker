#!/usr/bin/env python3
"""
Unit tests for run_pre_tool_hook() function in hook.py.
"""

import json
import os
import tempfile
from unittest.mock import patch
from pacemaker.hook import run_pre_tool_hook


def _write_transcript_file(entries):
    """Write JSONL transcript entries to a temp file, return (path, file object).
    Caller must close/unlink the file after use.
    """
    f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w")
    for entry in entries:
        f.write(json.dumps(entry) + "\n")
    f.flush()
    f.close()
    return f.name


class TestPreToolHook:
    """Test run_pre_tool_hook() function."""

    @patch("sys.stdin")
    def test_fails_open_on_invalid_stdin_json_no_unbound_local_error(self, mock_stdin):
        """Should return continue=True (fail-open) on invalid JSON stdin.

        Regression test: before the fix, an UnboundLocalError was raised because
        _csa_result was initialised AFTER json.loads(), so any exception between
        the top of the try block and that line (e.g. malformed JSON) caused the
        outer except handler to crash referencing an undefined variable.
        After the fix, _csa_result is initialised as the FIRST statement of the
        try block, guaranteeing it is always bound when the outer except fires.
        """
        mock_stdin.read.return_value = "not-valid-json"

        # Must NOT raise UnboundLocalError (or any other exception)
        result = run_pre_tool_hook()

        assert isinstance(result, dict), "Expected a dict response for fail-open"
        assert (
            result.get("continue") is True
        ), f"Expected fail-open {{continue: True}}, got {result}"

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
        """Should block tool use when validation fails.

        Transcript must contain the matching Write tool_use so the tool-matched
        anchor (bug #83 fix) finds the current turn and doesn't short-circuit.
        The text entry has no INTENT, so current_message_override="" and
        validate_intent_and_code is reached via the n-back path.
        """
        transcript_path = _write_transcript_file(
            [
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "I will write some code."}
                        ],
                    }
                },
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Write",
                                "input": {
                                    "file_path": "/path/to/test.py",
                                    "content": "code",
                                },
                            }
                        ],
                    }
                },
            ]
        )
        try:
            hook_data = {
                "session_id": "test",
                "transcript_path": transcript_path,
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
            # Verify the mock was actually called — not a vacuous continue-True return
            mock_validate.assert_called_once()
        finally:
            os.unlink(transcript_path)

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
        """Should allow tool use when validation passes.

        Transcript must contain the matching Write tool_use so the tool-matched
        anchor finds the current turn. validate_intent_and_code must actually be
        called (not a vacuous fail-open short-circuit).
        """
        transcript_path = _write_transcript_file(
            [
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": "I will modify test.py to add logging",
                            }
                        ],
                    }
                },
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Write",
                                "input": {
                                    "file_path": "/path/to/test.py",
                                    "content": "code",
                                },
                            }
                        ],
                    }
                },
            ]
        )
        try:
            hook_data = {
                "session_id": "test",
                "transcript_path": transcript_path,
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
            # Verify validation actually ran — not a vacuous fail-open pass
            mock_validate.assert_called_once()
        finally:
            os.unlink(transcript_path)

    @patch("pacemaker.hook.load_config")
    @patch("pacemaker.extension_registry.load_extensions")
    @patch("pacemaker.extension_registry.is_source_code_file")
    @patch("pacemaker.hook.get_last_n_messages_for_validation")
    @patch("pacemaker.intent_validator.validate_intent_and_code")
    @patch("pacemaker.hook.get_transcript_path")
    @patch("sys.stdin")
    def test_reads_last_2_messages(
        self,
        mock_stdin,
        mock_get_transcript_path,
        mock_validate,
        mock_get_messages,
        mock_is_source,
        mock_load_ext,
        mock_load_config,
    ):
        """Should read last 2 messages (text + tool_use are separate transcript entries)."""
        hook_data = {
            "session_id": "test-session",
            "tool_name": "Write",
            "tool_input": {"file_path": "/path/to/test.py", "content": "code"},
        }
        mock_stdin.read.return_value = json.dumps(hook_data)
        mock_load_config.return_value = {"intent_validation_enabled": True}
        mock_load_ext.return_value = [".py"]
        mock_is_source.return_value = True
        mock_get_transcript_path.return_value = "/tmp/transcript.jsonl"
        mock_get_messages.return_value = ["INTENT: Modify test.py to add code"]
        mock_validate.return_value = {"approved": True}

        run_pre_tool_hook()

        # Verify get_last_n_messages_for_validation was called with n=2
        mock_get_messages.assert_called_once_with("/tmp/transcript.jsonl", n=2)

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
        """Should pass messages, code, file_path, and tool_name to validate_intent_and_code.

        The transcript contains the matching Edit tool_use (so the tool-matched
        anchor finds the current turn) but the text entry has NO INTENT marker.
        Therefore get_current_turn_message_for_validation returns "" (tool found,
        no INTENT in its turn text), and current_message_override="" is passed
        to validate_intent_and_code — the n-back heuristic takes over.
        """
        transcript_path = _write_transcript_file(
            [
                {
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "I will edit config.py"}],
                    }
                },
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Edit",
                                "input": {
                                    "file_path": "/path/to/config.py",
                                    "old_string": "old",
                                    "new_string": "new code",
                                },
                            }
                        ],
                    }
                },
            ]
        )
        try:
            hook_data = {
                "session_id": "test",
                "transcript_path": transcript_path,
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "/path/to/config.py",
                    "new_string": "new code",
                },
            }
            mock_stdin.read.return_value = json.dumps(hook_data)
            mock_load_config.return_value = {"intent_validation_enabled": True}
            mock_load_ext.return_value = [".py"]
            mock_is_source.return_value = True
            messages = ["I will edit config.py"]
            mock_get_messages.return_value = messages
            mock_validate.return_value = {"approved": True}

            run_pre_tool_hook()

            # verify validate_intent_and_code received correct args.
            # The transcript text has no INTENT, so the tool-matched anchor
            # returns "" (no-INTENT fallback) → current_message_override="".
            mock_validate.assert_called_once_with(
                messages=messages,
                code="new code",
                file_path="/path/to/config.py",
                tool_name="Edit",
                hook_model="auto",
                current_message_override="",
            )
        finally:
            os.unlink(transcript_path)

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
        """Should block Write with fail-closed message when SDK is unavailable.

        Transcript contains the matching Write tool_use (no INTENT in transcript
        text) so the tool-matched anchor returns "" and validate_intent_and_code
        is reached. The mocked n-back messages include INTENT so Stage 1 passes;
        then the SDK availability gate fires and blocks fail-closed.
        """
        transcript_path = _write_transcript_file(
            [
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "Writing the foo function."}
                        ],
                    }
                },
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Write",
                                "input": {
                                    "file_path": "/path/to/test.py",
                                    "content": "def foo(): pass",
                                },
                            }
                        ],
                    }
                },
            ]
        )
        try:
            hook_data = {
                "session_id": "test",
                "transcript_path": transcript_path,
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
            # Message must include INTENT: marker so Stage 1 passes and execution
            # reaches the SDK availability gate (which then blocks fail-closed).
            mock_get_messages.return_value = [
                "INTENT: Modify test.py to add foo() function that does nothing. "
                "Test coverage: tests/test_foo.py::test_foo_returns_none"
            ]

            result = run_pre_tool_hook()

            # Should block with fail-closed message when SDK unavailable
            assert result["decision"] == "block"
            assert "SDK is not available" in result["reason"]
        finally:
            os.unlink(transcript_path)
