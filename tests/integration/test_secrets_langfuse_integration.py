"""
Integration tests for secrets and Langfuse trace sanitization.

Following strict TDD methodology - these tests are written FIRST and will FAIL
until the integration is implemented.

Tests verify that:
1. Traces are NOT pushed immediately in user_prompt_submit
2. Traces ARE sanitized and pushed in post_tool_use
3. Secrets in traces are properly masked before push
"""

import os
import json
import tempfile
import pytest
from unittest.mock import patch

from src.pacemaker.secrets.database import create_secret
from src.pacemaker.langfuse.orchestrator import (
    handle_user_prompt_submit,
    handle_post_tool_use,
)


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
    """Create a temporary state directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def temp_transcript():
    """Create a temporary transcript file."""
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)

    # Write minimal transcript content
    with open(path, "w") as f:
        f.write('{"type":"conversation_start","user_id":"test-user"}\n')
        f.write('{"type":"user_message","text":"test prompt"}\n')

    yield path

    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def langfuse_config(temp_db):
    """Create Langfuse configuration with secrets db path."""
    return {
        "langfuse_enabled": True,
        "langfuse_base_url": "https://test.langfuse.example.com",
        "langfuse_public_key": "pk-test",
        "langfuse_secret_key": "sk-test",
        "db_path": temp_db,
    }


class TestPushTimingRefactor:
    """Test that traces are NOT pushed immediately, but deferred."""

    @patch("src.pacemaker.langfuse.push.push_batch_events")
    def test_user_prompt_submit_does_not_push_immediately(
        self, mock_push, langfuse_config, temp_state_dir, temp_transcript
    ):
        """Test that user_prompt_submit does NOT push trace to Langfuse."""
        # Call user_prompt_submit
        result = handle_user_prompt_submit(
            config=langfuse_config,
            session_id="test-session",
            transcript_path=temp_transcript,
            state_dir=temp_state_dir,
            user_message="test message",
        )

        # Should succeed
        assert result is True

        # Should NOT have called push
        assert mock_push.call_count == 0, "Trace should not be pushed immediately"

    @patch("src.pacemaker.langfuse.push.push_batch_events")
    def test_user_prompt_submit_stores_pending_trace(
        self, mock_push, langfuse_config, temp_state_dir, temp_transcript
    ):
        """Test that user_prompt_submit stores trace as pending."""
        handle_user_prompt_submit(
            config=langfuse_config,
            session_id="test-session",
            transcript_path=temp_transcript,
            state_dir=temp_state_dir,
            user_message="test message",
        )

        # Check state file for pending_trace
        state_file = os.path.join(temp_state_dir, "test-session.json")
        assert os.path.exists(state_file), "State file should exist"

        with open(state_file, "r") as f:
            state = json.load(f)

        # Should have pending_trace field
        assert "pending_trace" in state, "State should contain pending_trace"
        assert state["pending_trace"] is not None

    @patch("src.pacemaker.langfuse.push.push_batch_events")
    def test_post_tool_use_pushes_pending_trace(
        self, mock_push, langfuse_config, temp_state_dir, temp_transcript
    ):
        """Test that post_tool_use pushes the pending trace."""
        mock_push.return_value = True

        # First: user_prompt_submit stores pending trace
        handle_user_prompt_submit(
            config=langfuse_config,
            session_id="test-session",
            transcript_path=temp_transcript,
            state_dir=temp_state_dir,
            user_message="test message",
        )

        # Reset mock
        mock_push.reset_mock()

        # Second: post_tool_use should push the pending trace
        handle_post_tool_use(
            config=langfuse_config,
            session_id="test-session",
            transcript_path=temp_transcript,
            state_dir=temp_state_dir,
        )

        # Should have pushed trace
        assert mock_push.call_count > 0, "post_tool_use should push pending trace"


class TestTraceSanitization:
    """Test that traces are sanitized before push."""

    @patch("src.pacemaker.langfuse.push.push_batch_events")
    def test_pending_trace_is_sanitized_before_push(
        self, mock_push, langfuse_config, temp_state_dir, temp_transcript, temp_db
    ):
        """Test that secrets are masked in trace before pushing."""
        mock_push.return_value = True

        # Store a secret
        create_secret(temp_db, "text", "secret-api-key-12345")

        # Create trace with secret in it
        handle_user_prompt_submit(
            config=langfuse_config,
            session_id="test-session",
            transcript_path=temp_transcript,
            state_dir=temp_state_dir,
            user_message="Use this key: secret-api-key-12345",
        )

        mock_push.reset_mock()

        # Push via post_tool_use
        handle_post_tool_use(
            config=langfuse_config,
            session_id="test-session",
            transcript_path=temp_transcript,
            state_dir=temp_state_dir,
        )

        # Verify push was called
        assert mock_push.call_count > 0

        # Get the pushed batch
        push_call_args = mock_push.call_args
        batch = push_call_args[0][3]  # 4th positional argument is the batch

        # Check that batch was sanitized (secret should be masked)
        batch_str = json.dumps(batch)
        assert (
            "secret-api-key-12345" not in batch_str
        ), "Secret should be masked in pushed trace"
        assert "*** MASKED ***" in batch_str, "Masked placeholder should be present"

    @patch("src.pacemaker.langfuse.push.push_batch_events")
    def test_multiple_secrets_all_masked(
        self, mock_push, langfuse_config, temp_state_dir, temp_transcript, temp_db
    ):
        """Test that multiple secrets are all masked."""
        mock_push.return_value = True

        # Store multiple secrets
        create_secret(temp_db, "text", "password123")
        create_secret(temp_db, "text", "token-xyz")

        # Create trace with secrets
        handle_user_prompt_submit(
            config=langfuse_config,
            session_id="test-session",
            transcript_path=temp_transcript,
            state_dir=temp_state_dir,
            user_message="Password: password123, Token: token-xyz",
        )

        mock_push.reset_mock()

        # Push
        handle_post_tool_use(
            config=langfuse_config,
            session_id="test-session",
            transcript_path=temp_transcript,
            state_dir=temp_state_dir,
        )

        # Verify both secrets masked
        batch = mock_push.call_args[0][3]
        batch_str = json.dumps(batch)

        assert "password123" not in batch_str
        assert "token-xyz" not in batch_str
        assert batch_str.count("*** MASKED ***") >= 2

    @patch("src.pacemaker.langfuse.push.push_batch_events")
    def test_trace_without_secrets_unchanged(
        self, mock_push, langfuse_config, temp_state_dir, temp_transcript, temp_db
    ):
        """Test that traces without secrets are pushed unchanged."""
        mock_push.return_value = True

        # No secrets stored

        # Create trace
        handle_user_prompt_submit(
            config=langfuse_config,
            session_id="test-session",
            transcript_path=temp_transcript,
            state_dir=temp_state_dir,
            user_message="Normal message without secrets",
        )

        mock_push.reset_mock()

        # Push
        handle_post_tool_use(
            config=langfuse_config,
            session_id="test-session",
            transcript_path=temp_transcript,
            state_dir=temp_state_dir,
        )

        # Verify message is present unchanged
        batch = mock_push.call_args[0][3]
        batch_str = json.dumps(batch)

        assert "Normal message without secrets" in batch_str
        assert "*** MASKED ***" not in batch_str

    @patch("src.pacemaker.langfuse.push.push_batch_events")
    @patch("src.pacemaker.transcript_reader.get_last_n_assistant_messages")
    def test_secrets_parsed_on_subsequent_post_tool_use_calls(
        self,
        mock_get_messages,
        mock_push,
        langfuse_config,
        temp_state_dir,
        temp_transcript,
        temp_db,
    ):
        """
        CRITICAL BUG TEST: Secrets must be parsed on EVERY post_tool_use call, not just when pending_trace exists.

        This test reproduces the bug where secrets parsing only happens when there's a pending trace to push,
        but NOT on subsequent post_tool_use calls after the trace was already pushed.

        Scenario:
        1. First post_tool_use: pending_trace exists, gets pushed, secrets are parsed ✓
        2. Assistant declares a secret in response
        3. Second post_tool_use: NO pending_trace (already pushed), secrets should still be parsed ✗ BUG

        Expected: Secrets are parsed and stored in DB on BOTH calls
        Actual (before fix): Secrets only parsed on first call (when pending_trace exists)
        """
        mock_push.return_value = True

        # First call: Simulate assistant response WITHOUT secret declaration
        mock_get_messages.return_value = ["Normal response without secrets"]

        # Create and push initial trace
        handle_user_prompt_submit(
            config=langfuse_config,
            session_id="test-session",
            transcript_path=temp_transcript,
            state_dir=temp_state_dir,
            user_message="First message",
        )

        # First post_tool_use: pending_trace exists, will be pushed
        handle_post_tool_use(
            config=langfuse_config,
            session_id="test-session",
            transcript_path=temp_transcript,
            state_dir=temp_state_dir,
        )

        # At this point, pending_trace has been pushed and removed from state
        # Verify no secrets in DB yet
        from src.pacemaker.secrets.database import get_all_secrets

        secrets_before = get_all_secrets(temp_db)
        assert len(secrets_before) == 0, "No secrets should be stored yet"

        # Second call: Simulate assistant declaring a secret in response
        mock_get_messages.return_value = [
            "🔐 SECRET_TEXT: my-secret-token-12345",
            "I've declared a secret for you",
        ]

        # Second post_tool_use: NO pending_trace (already pushed in previous call)
        # BUG: Secrets parsing is inside "if pending_trace:" block, so it won't run here
        handle_post_tool_use(
            config=langfuse_config,
            session_id="test-session",
            transcript_path=temp_transcript,
            state_dir=temp_state_dir,
        )

        # CRITICAL ASSERTION: Secret should be in database even though no pending_trace was pushed
        secrets_after = get_all_secrets(temp_db)
        assert (
            len(secrets_after) > 0
        ), "Secret should be parsed and stored even without pending_trace"

        # Verify the specific secret was stored (get_all_secrets returns list of strings)
        assert (
            "my-secret-token-12345" in secrets_after
        ), "The declared secret should be in database"
