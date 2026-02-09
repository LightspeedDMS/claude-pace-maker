"""
Unit tests for Stop hook secret parsing.

Tests that the Stop hook parses secrets from Claude's response BEFORE sanitizing
and pushing to Langfuse, ensuring secrets are in the database for masking.

Following strict TDD methodology - these tests are written FIRST and will FAIL
until the orchestrator is fixed.
"""

import json
import os
import tempfile
from unittest.mock import patch

import pytest

from src.pacemaker.langfuse.orchestrator import handle_stop_finalize
from src.pacemaker.secrets.database import list_secrets


@pytest.fixture
def temp_db():
    """Create a temporary database file for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    # Cleanup
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def temp_state_dir():
    """Create a temporary state directory for testing."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    # Cleanup
    import shutil

    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)


@pytest.fixture
def temp_transcript():
    """Create a temporary transcript file for testing."""
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    yield path
    # Cleanup
    if os.path.exists(path):
        os.remove(path)


class TestStopHookSecretParsing:
    """Test that Stop hook parses secrets before sanitization."""

    def test_stop_hook_parses_secrets_before_sanitization(
        self, temp_db, temp_state_dir, temp_transcript
    ):
        """
        Test that secrets declared in Claude's response are parsed BEFORE
        sanitization, ensuring they're in the database for masking.
        """
        # Setup: Create transcript with secret declaration
        secret_value = "sk-test-abc123def456"
        session_id = "test-session"
        transcript_content = [
            {
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Show me the API key"}],
                }
            },
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Here is your API key: 🔐 SECRET_TEXT: {secret_value}",
                        }
                    ],
                }
            },
        ]

        with open(temp_transcript, "w") as f:
            for entry in transcript_content:
                f.write(json.dumps(entry) + "\n")

        # Setup: Create state file with current trace
        trace_id = "test-trace-123"
        state = {
            "metadata": {
                "current_trace_id": trace_id,
                "trace_start_line": 0,
            }
        }

        state_file = os.path.join(temp_state_dir, f"{session_id}.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        # Setup: Mock config
        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://test.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
            "db_path": temp_db,
        }

        # Mock push to capture what gets sent to Langfuse
        pushed_batch = None

        def capture_push(base_url, public_key, secret_key, batch, timeout):
            nonlocal pushed_batch
            pushed_batch = batch
            return (True, 1)

        with patch(
            "src.pacemaker.langfuse.orchestrator.push.push_batch_events",
            side_effect=capture_push,
        ):
            # Execute: Run stop hook
            success = handle_stop_finalize(
                config=config,
                session_id=session_id,
                transcript_path=temp_transcript,
                state_dir=temp_state_dir,
            )

            # Verify: Hook succeeded
            assert success is True

            # Verify: Secret was parsed and stored in database
            stored_secrets = list_secrets(temp_db)
            assert len(stored_secrets) == 1
            assert stored_secrets[0]["value"] == secret_value

            # Verify: Pushed batch has secret masked
            assert pushed_batch is not None
            assert len(pushed_batch) == 1

            trace_update = pushed_batch[0]["body"]
            output_content = trace_update.get("output", "")

            # Secret should be masked in output
            assert secret_value not in output_content
            assert "*** MASKED ***" in output_content

    def test_stop_hook_parses_multiple_secrets(
        self, temp_db, temp_state_dir, temp_transcript
    ):
        """Test that multiple secrets are all parsed and masked."""
        # Setup: Transcript with multiple secrets
        secret1 = "password123"
        secret2 = "api-key-xyz"
        session_id = "test-session-2"
        transcript_content = [
            {
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Show me credentials"}],
                }
            },
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Password: 🔐 SECRET_TEXT: {secret1}\nAPI Key: 🔐 SECRET_TEXT: {secret2}",
                        }
                    ],
                }
            },
        ]

        with open(temp_transcript, "w") as f:
            for entry in transcript_content:
                f.write(json.dumps(entry) + "\n")

        # Setup: State file
        trace_id = "test-trace-456"
        state = {
            "metadata": {
                "current_trace_id": trace_id,
                "trace_start_line": 0,
            }
        }

        state_file = os.path.join(temp_state_dir, f"{session_id}.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        # Setup: Config
        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://test.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
            "db_path": temp_db,
        }

        # Mock push
        pushed_batch = None

        def capture_push(base_url, public_key, secret_key, batch, timeout):
            nonlocal pushed_batch
            pushed_batch = batch
            return (True, 1)

        with patch(
            "src.pacemaker.langfuse.orchestrator.push.push_batch_events",
            side_effect=capture_push,
        ):
            # Execute
            success = handle_stop_finalize(
                config=config,
                session_id=session_id,
                transcript_path=temp_transcript,
                state_dir=temp_state_dir,
            )

            # Verify: Success
            assert success is True

            # Verify: Both secrets stored
            stored_secrets = list_secrets(temp_db)
            assert len(stored_secrets) == 2
            stored_values = {s["value"] for s in stored_secrets}
            assert secret1 in stored_values
            assert secret2 in stored_values

            # Verify: Both secrets masked in output
            assert pushed_batch is not None
            trace_update = pushed_batch[0]["body"]
            output_content = trace_update.get("output", "")

            assert secret1 not in output_content
            assert secret2 not in output_content

    def test_stop_hook_handles_file_secrets(
        self, temp_db, temp_state_dir, temp_transcript
    ):
        """Test that file secrets are also parsed and masked."""
        # Setup: Transcript with file secret
        file_secret = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA"
        session_id = "test-session-3"
        transcript_content = [
            {
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Show me the key file"}],
                }
            },
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Here is the key:\n🔐 SECRET_FILE_START\n{file_secret}\n🔐 SECRET_FILE_END",
                        }
                    ],
                }
            },
        ]

        with open(temp_transcript, "w") as f:
            for entry in transcript_content:
                f.write(json.dumps(entry) + "\n")

        # Setup: State
        trace_id = "test-trace-789"
        state = {
            "metadata": {
                "current_trace_id": trace_id,
                "trace_start_line": 0,
            }
        }

        state_file = os.path.join(temp_state_dir, f"{session_id}.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        # Setup: Config
        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://test.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
            "db_path": temp_db,
        }

        # Mock push
        pushed_batch = None

        def capture_push(base_url, public_key, secret_key, batch, timeout):
            nonlocal pushed_batch
            pushed_batch = batch
            return (True, 1)

        with patch(
            "src.pacemaker.langfuse.orchestrator.push.push_batch_events",
            side_effect=capture_push,
        ):
            # Execute
            success = handle_stop_finalize(
                config=config,
                session_id=session_id,
                transcript_path=temp_transcript,
                state_dir=temp_state_dir,
            )

            # Verify: Success
            assert success is True

            # Verify: File secret stored
            stored_secrets = list_secrets(temp_db)
            assert len(stored_secrets) == 1
            assert file_secret in stored_secrets[0]["value"]

            # Verify: Secret masked in output
            assert pushed_batch is not None
            trace_update = pushed_batch[0]["body"]
            output_content = trace_update.get("output", "")

            assert "BEGIN RSA PRIVATE KEY" not in output_content
            assert "MIIEpAIBAAKCAQEA" not in output_content

    def test_stop_hook_gracefully_handles_parse_failures(
        self, temp_db, temp_state_dir, temp_transcript
    ):
        """Test that parse failures don't break the stop hook."""
        # Setup: Valid transcript
        session_id = "test-session-4"
        transcript_content = [
            {
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Test request"}],
                }
            },
            {
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Normal response"}],
                }
            },
        ]

        with open(temp_transcript, "w") as f:
            for entry in transcript_content:
                f.write(json.dumps(entry) + "\n")

        # Setup: State
        trace_id = "test-trace-error"
        state = {
            "metadata": {
                "current_trace_id": trace_id,
                "trace_start_line": 0,
            }
        }

        state_file = os.path.join(temp_state_dir, f"{session_id}.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        # Setup: Config
        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://test.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
            "db_path": temp_db,
        }

        # Mock parse_assistant_response to raise exception
        with patch(
            "src.pacemaker.langfuse.orchestrator.push.push_batch_events",
            return_value=(True, 1),
        ):
            with patch(
                "src.pacemaker.secrets.parser.parse_assistant_response",
                side_effect=Exception("Parse error"),
            ):
                # Execute: Should not crash despite parse error
                success = handle_stop_finalize(
                    config=config,
                    session_id=session_id,
                    transcript_path=temp_transcript,
                    state_dir=temp_state_dir,
                )

                # Verify: Still succeeds (graceful degradation)
                assert success is True


class TestStopHookSecretParsingOrder:
    """Test that secret parsing happens BEFORE sanitization."""

    def test_parsing_happens_before_sanitization(
        self, temp_db, temp_state_dir, temp_transcript
    ):
        """
        Test that secrets are in database BEFORE sanitize_trace is called.

        This ensures the sanitizer can actually mask them.
        """
        # Setup: Transcript with secret
        secret_value = "super-secret-123"
        session_id = "test-session-5"
        transcript_content = [
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Secret: 🔐 SECRET_TEXT: {secret_value}",
                        }
                    ],
                }
            },
        ]

        with open(temp_transcript, "w") as f:
            for entry in transcript_content:
                f.write(json.dumps(entry) + "\n")

        # Setup: State
        trace_id = "test-trace-order"
        state = {
            "metadata": {
                "current_trace_id": trace_id,
                "trace_start_line": 0,
            }
        }

        state_file = os.path.join(temp_state_dir, f"{session_id}.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        # Setup: Config
        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://test.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
            "db_path": temp_db,
        }

        # Track call order
        call_order = []

        original_parse = __import__(
            "src.pacemaker.secrets.parser", fromlist=["parse_assistant_response"]
        ).parse_assistant_response
        original_sanitize = __import__(
            "src.pacemaker.secrets.sanitizer", fromlist=["sanitize_trace"]
        ).sanitize_trace

        def tracked_parse(response, db_path):
            call_order.append("parse")
            return original_parse(response, db_path)

        def tracked_sanitize(trace_data, db_path):
            call_order.append("sanitize")
            return original_sanitize(trace_data, db_path)

        with patch(
            "src.pacemaker.langfuse.orchestrator.push.push_batch_events",
            return_value=(True, 1),
        ):
            with patch(
                "src.pacemaker.secrets.parser.parse_assistant_response",
                side_effect=tracked_parse,
            ):
                with patch(
                    "src.pacemaker.langfuse.orchestrator.sanitize_trace",
                    side_effect=tracked_sanitize,
                ):
                    # Execute
                    success = handle_stop_finalize(
                        config=config,
                        session_id=session_id,
                        transcript_path=temp_transcript,
                        state_dir=temp_state_dir,
                    )

                    # Verify: Success
                    assert success is True

                    # Verify: Parse was called BEFORE sanitize
                    assert "parse" in call_order
                    assert "sanitize" in call_order
                    parse_idx = call_order.index("parse")
                    sanitize_idx = call_order.index("sanitize")
                    assert (
                        parse_idx < sanitize_idx
                    ), "Secret parsing must happen BEFORE sanitization"
