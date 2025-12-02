#!/usr/bin/env python3
"""
Unit tests for Phase 5: Post-Tool Hook Enhancement.

Tests the enhanced run_hook() function that adds code review validation
after the existing pacing logic.
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add src to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pacemaker import hook
from pacemaker.constants import DEFAULT_EXTENSION_REGISTRY_PATH


class TestPostToolHookEnhancement:
    """Test suite for enhanced post-tool hook with code review."""

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories for config and state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            yield {
                "tmpdir": tmpdir,
                "config_path": str(config_dir / "config.json"),
                "state_path": str(config_dir / "state.json"),
                "transcript_path": str(config_dir / "transcript.jsonl"),
                "test_file": str(config_dir / "test.py"),
            }

    @pytest.fixture
    def mock_config_disabled(self, temp_dirs):
        """Config with intent validation disabled."""
        config = {
            "enabled": True,
            "intent_validation_enabled": False,
        }
        with open(temp_dirs["config_path"], "w") as f:
            json.dump(config, f)
        return temp_dirs["config_path"]

    @pytest.fixture
    def mock_config_enabled(self, temp_dirs):
        """Config with intent validation enabled."""
        config = {
            "enabled": True,
            "intent_validation_enabled": True,
        }
        with open(temp_dirs["config_path"], "w") as f:
            json.dump(config, f)
        return temp_dirs["config_path"]

    @pytest.fixture
    def mock_transcript(self, temp_dirs):
        """Create mock transcript file."""
        transcript = [
            {
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Fix the bug in test.py"}],
                }
            },
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "I will modify test.py to fix the authentication bug by adding proper JWT validation.",
                        }
                    ],
                }
            },
        ]
        with open(temp_dirs["transcript_path"], "w") as f:
            for entry in transcript:
                f.write(json.dumps(entry) + "\n")
        return temp_dirs["transcript_path"]

    @pytest.fixture
    def mock_source_file(self, temp_dirs):
        """Create mock Python source file."""
        code = """def authenticate():
    # JWT validation
    return True
"""
        with open(temp_dirs["test_file"], "w") as f:
            f.write(code)
        return temp_dirs["test_file"]

    def test_backward_compatibility_when_disabled(
        self, temp_dirs, mock_config_disabled, capsys
    ):
        """
        Test that existing pacing behavior works when intent validation disabled.

        This ensures Phase 5 doesn't break existing functionality.
        """
        hook_data = {
            "session_id": "test-session",
            "tool_name": "Write",
            "tool_input": {"file_path": temp_dirs["test_file"], "content": "code"},
            "transcript_path": temp_dirs["transcript_path"],
        }

        # Mock stdin with hook data
        with patch("sys.stdin.read", return_value=json.dumps(hook_data)):
            # Mock pacing engine to return no throttling
            with patch("pacemaker.pacing_engine.run_pacing_check") as mock_pacing:
                mock_pacing.return_value = {
                    "decision": {"should_throttle": False},
                    "polled": False,
                }

                # Mock database initialization
                with patch("pacemaker.database.initialize_database"):
                    # Mock load_config to use our temp config
                    with patch(
                        "pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config_disabled
                    ):
                        # Run hook
                        hook.run_hook()

                        # Verify pacing ran
                        assert mock_pacing.called

        # Check stderr output - should show pacing logic only
        captured = capsys.readouterr()
        assert "[PACING]" in captured.err
        assert "No throttling needed" in captured.err

    def test_no_review_for_non_source_files(
        self, temp_dirs, mock_config_enabled, mock_transcript, capsys
    ):
        """
        Test that code review is NOT triggered for non-source files.

        Even with intent validation enabled, only source code files should
        trigger review.
        """
        # Create non-source file (markdown)
        non_source_file = str(Path(temp_dirs["tmpdir"]) / "README.md")
        with open(non_source_file, "w") as f:
            f.write("# Docs")

        hook_data = {
            "session_id": "test-session",
            "tool_name": "Write",
            "tool_input": {"file_path": non_source_file, "content": "# Docs"},
            "transcript_path": temp_dirs["transcript_path"],
        }

        with patch("sys.stdin.read", return_value=json.dumps(hook_data)):
            with patch("pacemaker.pacing_engine.run_pacing_check") as mock_pacing:
                mock_pacing.return_value = {
                    "decision": {"should_throttle": False},
                    "polled": False,
                }
                with patch("pacemaker.database.initialize_database"):
                    with patch(
                        "pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config_enabled
                    ):
                        # Mock code reviewer - should NOT be called
                        with patch(
                            "pacemaker.code_reviewer.validate_code_against_intent"
                        ) as mock_review:
                            hook.run_hook()

                            # Verify code review was NOT called
                            assert not mock_review.called

    def test_review_triggered_for_write_on_source_file(
        self,
        temp_dirs,
        mock_config_enabled,
        mock_transcript,
        mock_source_file,
        capsys,
    ):
        """
        Test that code review IS triggered for Write tool on source file.

        This is the core Phase 5 functionality.
        """
        hook_data = {
            "session_id": "test-session",
            "tool_name": "Write",
            "tool_input": {
                "file_path": mock_source_file,
                "content": "def test(): pass",
            },
            "transcript_path": mock_transcript,
        }

        with patch("sys.stdin.read", return_value=json.dumps(hook_data)):
            with patch("pacemaker.pacing_engine.run_pacing_check") as mock_pacing:
                mock_pacing.return_value = {
                    "decision": {"should_throttle": False},
                    "polled": False,
                }
                with patch("pacemaker.database.initialize_database"):
                    with patch(
                        "pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config_enabled
                    ):
                        # Mock extension registry path
                        with patch(
                            "pacemaker.hook.DEFAULT_EXTENSION_REGISTRY_PATH",
                            DEFAULT_EXTENSION_REGISTRY_PATH,
                        ):
                            # Mock code reviewer to return feedback
                            with patch(
                                "pacemaker.code_reviewer.validate_code_against_intent"
                            ) as mock_review:
                                mock_review.return_value = (
                                    "WARNING: Code doesn't match intent"
                                )

                                hook.run_hook()

                                # Verify code review was called
                                assert mock_review.called
                                # Verify it was called with correct arguments
                                call_args = mock_review.call_args
                                assert call_args[0][0] == mock_source_file  # file_path
                                assert isinstance(
                                    call_args[0][1], list
                                )  # messages list

        # Check stdout for feedback
        captured = capsys.readouterr()
        assert "WARNING: Code doesn't match intent" in captured.out

    def test_review_triggered_for_edit_on_source_file(
        self,
        temp_dirs,
        mock_config_enabled,
        mock_transcript,
        mock_source_file,
    ):
        """
        Test that code review IS triggered for Edit tool on source file.

        Edit tool should also trigger review, not just Write.
        """
        hook_data = {
            "session_id": "test-session",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": mock_source_file,
                "old_string": "old",
                "new_string": "new",
            },
            "transcript_path": mock_transcript,
        }

        with patch("sys.stdin.read", return_value=json.dumps(hook_data)):
            with patch("pacemaker.pacing_engine.run_pacing_check") as mock_pacing:
                mock_pacing.return_value = {
                    "decision": {"should_throttle": False},
                    "polled": False,
                }
                with patch("pacemaker.database.initialize_database"):
                    with patch(
                        "pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config_enabled
                    ):
                        with patch(
                            "pacemaker.hook.DEFAULT_EXTENSION_REGISTRY_PATH",
                            DEFAULT_EXTENSION_REGISTRY_PATH,
                        ):
                            with patch(
                                "pacemaker.code_reviewer.validate_code_against_intent"
                            ) as mock_review:
                                mock_review.return_value = ""

                                hook.run_hook()

                                # Verify code review was called
                                assert mock_review.called

    def test_no_feedback_when_code_matches_intent(
        self,
        temp_dirs,
        mock_config_enabled,
        mock_transcript,
        mock_source_file,
        capsys,
    ):
        """
        Test that no feedback is printed when code matches intent.

        Empty feedback = code is OK.
        """
        hook_data = {
            "session_id": "test-session",
            "tool_name": "Write",
            "tool_input": {
                "file_path": mock_source_file,
                "content": "def test(): pass",
            },
            "transcript_path": mock_transcript,
        }

        with patch("sys.stdin.read", return_value=json.dumps(hook_data)):
            with patch("pacemaker.pacing_engine.run_pacing_check") as mock_pacing:
                mock_pacing.return_value = {
                    "decision": {"should_throttle": False},
                    "polled": False,
                }
                with patch("pacemaker.database.initialize_database"):
                    with patch(
                        "pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config_enabled
                    ):
                        with patch(
                            "pacemaker.hook.DEFAULT_EXTENSION_REGISTRY_PATH",
                            DEFAULT_EXTENSION_REGISTRY_PATH,
                        ):
                            with patch(
                                "pacemaker.code_reviewer.validate_code_against_intent"
                            ) as mock_review:
                                # Empty feedback = code OK
                                mock_review.return_value = ""

                                hook.run_hook()

                                assert mock_review.called

        # Check stdout - should NOT contain review feedback
        captured = capsys.readouterr()
        assert "WARNING" not in captured.out

    def test_graceful_failure_on_review_error(
        self,
        temp_dirs,
        mock_config_enabled,
        mock_transcript,
        mock_source_file,
        capsys,
    ):
        """
        Test that hook fails open gracefully when code review throws error.

        Errors in review should NOT break pacing functionality.
        """
        hook_data = {
            "session_id": "test-session",
            "tool_name": "Write",
            "tool_input": {
                "file_path": mock_source_file,
                "content": "def test(): pass",
            },
            "transcript_path": mock_transcript,
        }

        with patch("sys.stdin.read", return_value=json.dumps(hook_data)):
            with patch("pacemaker.pacing_engine.run_pacing_check") as mock_pacing:
                mock_pacing.return_value = {
                    "decision": {"should_throttle": False},
                    "polled": False,
                }
                with patch("pacemaker.database.initialize_database"):
                    with patch(
                        "pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config_enabled
                    ):
                        with patch(
                            "pacemaker.hook.DEFAULT_EXTENSION_REGISTRY_PATH",
                            DEFAULT_EXTENSION_REGISTRY_PATH,
                        ):
                            with patch(
                                "pacemaker.code_reviewer.validate_code_against_intent"
                            ) as mock_review:
                                # Simulate SDK error
                                mock_review.side_effect = Exception(
                                    "SDK connection failed"
                                )

                                # Should NOT raise exception - graceful degradation
                                hook.run_hook()

                                # Pacing should still work
                                assert mock_pacing.called

        # Check that hook completed without crashing
        captured = capsys.readouterr()
        assert "[PACING]" in captured.err

    def test_review_reads_messages_from_transcript(
        self,
        temp_dirs,
        mock_config_enabled,
        mock_transcript,
        mock_source_file,
    ):
        """
        Test that hook reads last N assistant messages from transcript.

        This ensures intent extraction works correctly.
        """
        hook_data = {
            "session_id": "test-session",
            "tool_name": "Write",
            "tool_input": {
                "file_path": mock_source_file,
                "content": "def test(): pass",
            },
            "transcript_path": mock_transcript,
        }

        with patch("sys.stdin.read", return_value=json.dumps(hook_data)):
            with patch("pacemaker.pacing_engine.run_pacing_check") as mock_pacing:
                mock_pacing.return_value = {
                    "decision": {"should_throttle": False},
                    "polled": False,
                }
                with patch("pacemaker.database.initialize_database"):
                    with patch(
                        "pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config_enabled
                    ):
                        with patch(
                            "pacemaker.hook.DEFAULT_EXTENSION_REGISTRY_PATH",
                            DEFAULT_EXTENSION_REGISTRY_PATH,
                        ):
                            # Mock get_last_n_assistant_messages
                            with patch(
                                "pacemaker.hook.get_last_n_assistant_messages"
                            ) as mock_get_messages:
                                mock_get_messages.return_value = [
                                    "I will modify test.py to fix the authentication bug"
                                ]

                                with patch(
                                    "pacemaker.code_reviewer.validate_code_against_intent"
                                ) as mock_review:
                                    mock_review.return_value = ""

                                    hook.run_hook()

                                    # Verify get_last_n_assistant_messages was called
                                    assert mock_get_messages.called
                                    call_args = mock_get_messages.call_args
                                    assert call_args[0][0] == mock_transcript
                                    assert call_args[1]["n"] == 3  # Last 3 messages
