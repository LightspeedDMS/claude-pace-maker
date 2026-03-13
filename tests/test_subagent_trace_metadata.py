#!/usr/bin/env python3
"""
Tests for bug #48 - Subagent traces missing userId, project context, model, and token counts.

Bug: handle_subagent_start() creates trace WITHOUT:
- userId field
- project context (project_path, project_name, git_remote, git_branch) in metadata
- model in metadata

Bug: handle_subagent_stop() updates trace WITHOUT:
- Copying token counts to trace.metadata (only goes into generation observation)

These tests define the expected correct behavior.
They are written FIRST (TDD red phase) before the fix is implemented.
"""

import json
import pytest
from unittest.mock import patch

from pacemaker.langfuse import orchestrator


class TestSubagentStartTraceMissingMetadata:
    """Tests that handle_subagent_start includes userId, project context, and model."""

    @pytest.fixture
    def config(self):
        """Config with Langfuse enabled."""
        return {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://langfuse.example.com",
            "langfuse_public_key": "pk-test-123",
            "langfuse_secret_key": "sk-test-456",
            "db_path": "/tmp/test.db",
        }

    @pytest.fixture
    def parent_transcript(self, tmp_path):
        """Parent transcript with Task tool call and auth profile for user_id extraction."""
        transcript = tmp_path / "parent-session.jsonl"

        # auth_profile entry so extract_user_id can get the email
        auth_entry = {
            "type": "auth_profile",
            "profile": {"email": "tester@example.com"},
        }

        # session_start with model info
        session_entry = {
            "type": "session_start",
            "session_id": "parent-session-123",
            "model": "claude-sonnet-4-5-20250929",
            "timestamp": "2025-01-01T10:00:00Z",
        }

        # Task tool call for prompt extraction
        task_tool_entry = {
            "type": "assistant",
            "uuid": "msg-123",
            "timestamp": "2025-01-01T10:00:01Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "task-tool-obs-456",
                        "name": "Task",
                        "input": {
                            "subagent_type": "code-reviewer",
                            "prompt": "Review the authentication code",
                        },
                    }
                ],
            },
        }

        with open(transcript, "w") as f:
            f.write(json.dumps(auth_entry) + "\n")
            f.write(json.dumps(session_entry) + "\n")
            f.write(json.dumps(task_tool_entry) + "\n")

        return str(transcript)

    @pytest.fixture
    def project_context(self):
        """Sample project context returned by get_project_context()."""
        return {
            "project_path": "/home/user/my-project",
            "project_name": "my-project",
            "git_remote": "git@github.com:user/my-project.git",
            "git_branch": "feature/auth",
        }

    def test_subagent_start_includes_user_id_in_trace(
        self, config, parent_transcript, project_context, tmp_path
    ):
        """
        Bug #48: handle_subagent_start must include userId in trace body.

        Given a parent transcript with auth_profile containing user email
        When handle_subagent_start is called
        Then the trace body must include userId = user email
        """
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        with (
            patch(
                "pacemaker.langfuse.push.push_batch_events", return_value=(True, 1)
            ) as mock_push,
            patch("pacemaker.langfuse.metrics.increment_metric"),
            patch(
                "pacemaker.langfuse.orchestrator.get_project_context",
                return_value=project_context,
            ),
        ):
            orchestrator.handle_subagent_start(
                config=config,
                parent_session_id="parent-session-123",
                subagent_session_id="subagent-789",
                subagent_name="code-reviewer",
                parent_transcript_path=parent_transcript,
                state_dir=str(state_dir),
            )

            assert mock_push.called
            batch = mock_push.call_args[0][3]
            assert len(batch) >= 1
            trace_body = batch[0]["body"]

            # Bug #48: userId must be present in trace
            assert (
                "userId" in trace_body
            ), "Trace must have userId field (bug #48: subagent traces missing userId)"
            assert (
                trace_body["userId"] == "tester@example.com"
            ), f"Expected userId='tester@example.com' but got '{trace_body.get('userId')}'"

    def test_subagent_start_includes_project_context_in_metadata(
        self, config, parent_transcript, project_context, tmp_path
    ):
        """
        Bug #48: handle_subagent_start must include project context fields in trace metadata.

        Given get_project_context() returns project info
        When handle_subagent_start is called
        Then trace.metadata must contain project_path, project_name, git_remote, git_branch
        """
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        with (
            patch(
                "pacemaker.langfuse.push.push_batch_events", return_value=(True, 1)
            ) as mock_push,
            patch("pacemaker.langfuse.metrics.increment_metric"),
            patch(
                "pacemaker.langfuse.orchestrator.get_project_context",
                return_value=project_context,
            ),
        ):
            orchestrator.handle_subagent_start(
                config=config,
                parent_session_id="parent-session-123",
                subagent_session_id="subagent-789",
                subagent_name="code-reviewer",
                parent_transcript_path=parent_transcript,
                state_dir=str(state_dir),
            )

            assert mock_push.called
            batch = mock_push.call_args[0][3]
            trace_body = batch[0]["body"]
            metadata = trace_body.get("metadata", {})

            # Bug #48: project context fields must be in trace metadata
            assert (
                "project_path" in metadata
            ), "trace.metadata must have project_path (bug #48)"
            assert metadata["project_path"] == "/home/user/my-project"

            assert (
                "project_name" in metadata
            ), "trace.metadata must have project_name (bug #48)"
            assert metadata["project_name"] == "my-project"

            assert (
                "git_remote" in metadata
            ), "trace.metadata must have git_remote (bug #48)"
            assert metadata["git_remote"] == "git@github.com:user/my-project.git"

            assert (
                "git_branch" in metadata
            ), "trace.metadata must have git_branch (bug #48)"
            assert metadata["git_branch"] == "feature/auth"

    def test_subagent_start_includes_model_in_metadata(
        self, config, parent_transcript, project_context, tmp_path
    ):
        """
        Bug #48: handle_subagent_start must include model in trace metadata.

        Given a parent transcript with session_start containing model info
        When handle_subagent_start is called
        Then trace.metadata must contain the model name
        """
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        with (
            patch(
                "pacemaker.langfuse.push.push_batch_events", return_value=(True, 1)
            ) as mock_push,
            patch("pacemaker.langfuse.metrics.increment_metric"),
            patch(
                "pacemaker.langfuse.orchestrator.get_project_context",
                return_value=project_context,
            ),
        ):
            orchestrator.handle_subagent_start(
                config=config,
                parent_session_id="parent-session-123",
                subagent_session_id="subagent-789",
                subagent_name="code-reviewer",
                parent_transcript_path=parent_transcript,
                state_dir=str(state_dir),
            )

            assert mock_push.called
            batch = mock_push.call_args[0][3]
            trace_body = batch[0]["body"]
            metadata = trace_body.get("metadata", {})

            # Bug #48: model must be in trace metadata
            assert "model" in metadata, "trace.metadata must have model field (bug #48)"
            assert (
                metadata["model"] == "claude-sonnet-4-5-20250929"
            ), f"Expected model='claude-sonnet-4-5-20250929' but got '{metadata.get('model')}'"

    def test_subagent_start_preserves_existing_metadata_fields(
        self, config, parent_transcript, project_context, tmp_path
    ):
        """
        Bug #48 regression: Adding new fields must NOT remove existing metadata fields.

        The existing subagent_session_id and subagent_name fields must still be present.
        """
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        with (
            patch(
                "pacemaker.langfuse.push.push_batch_events", return_value=(True, 1)
            ) as mock_push,
            patch("pacemaker.langfuse.metrics.increment_metric"),
            patch(
                "pacemaker.langfuse.orchestrator.get_project_context",
                return_value=project_context,
            ),
        ):
            orchestrator.handle_subagent_start(
                config=config,
                parent_session_id="parent-session-123",
                subagent_session_id="subagent-789",
                subagent_name="code-reviewer",
                parent_transcript_path=parent_transcript,
                state_dir=str(state_dir),
            )

            assert mock_push.called
            batch = mock_push.call_args[0][3]
            trace_body = batch[0]["body"]
            metadata = trace_body.get("metadata", {})

            # Existing fields must still be there (regression check)
            assert (
                "subagent_session_id" in metadata
            ), "subagent_session_id must still be in metadata after adding new fields"
            assert metadata["subagent_session_id"] == "subagent-789"

            assert (
                "subagent_name" in metadata
            ), "subagent_name must still be in metadata after adding new fields"
            assert metadata["subagent_name"] == "code-reviewer"

    def test_subagent_start_handles_missing_user_id_gracefully(self, config, tmp_path):
        """
        Bug #48 edge case: When user_id cannot be extracted, trace should still be created.

        Transcript with no auth_profile and extract_user_id returns None.
        The trace should be created with userId='unknown' or similar fallback.
        """
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        # Transcript with only a Task tool call, no auth profile
        transcript = tmp_path / "no-auth-transcript.jsonl"
        task_entry = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Task",
                        "input": {
                            "subagent_type": "tdd-engineer",
                            "prompt": "Fix the bug",
                        },
                    }
                ],
            },
        }
        with open(transcript, "w") as f:
            f.write(json.dumps(task_entry) + "\n")

        with (
            patch(
                "pacemaker.langfuse.push.push_batch_events", return_value=(True, 1)
            ) as mock_push,
            patch("pacemaker.langfuse.metrics.increment_metric"),
            patch(
                "pacemaker.langfuse.project_context.get_project_context",
                return_value={
                    "project_path": "/tmp",
                    "project_name": "tmp",
                    "git_remote": None,
                    "git_branch": None,
                },
            ),
            patch(
                "pacemaker.telemetry.jsonl_parser.get_user_email",
                return_value=None,
            ),
        ):
            result = orchestrator.handle_subagent_start(
                config=config,
                parent_session_id="parent-session-123",
                subagent_session_id="subagent-789",
                subagent_name="tdd-engineer",
                parent_transcript_path=str(transcript),
                state_dir=str(state_dir),
            )

            # Must succeed even without user_id
            assert result is not None

            batch = mock_push.call_args[0][3]
            trace_body = batch[0]["body"]

            # userId must exist (even if 'unknown')
            assert (
                "userId" in trace_body
            ), "userId must be present even when extraction fails"


class TestSubagentStopTokenCountsInTraceMetadata:
    """Tests that handle_subagent_stop copies token counts to trace.metadata."""

    @pytest.fixture
    def config(self):
        """Config with Langfuse enabled."""
        return {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://langfuse.example.com",
            "langfuse_public_key": "pk-test-123",
            "langfuse_secret_key": "sk-test-456",
            "db_path": "/tmp/test.db",
        }

    @pytest.fixture
    def agent_transcript_with_tokens(self, tmp_path):
        """Subagent transcript with token usage data."""
        transcript = tmp_path / "subagent-session.jsonl"

        # session_start with model info
        session_entry = {
            "type": "session_start",
            "session_id": "subagent-789",
            "model": "claude-sonnet-4-5-20250929",
            "timestamp": "2025-01-01T10:00:00Z",
        }

        # Assistant message with usage stats
        assistant_entry = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Code review complete."}],
                "usage": {
                    "input_tokens": 1500,
                    "output_tokens": 300,
                    "cache_read_input_tokens": 200,
                    "cache_creation_input_tokens": 50,
                },
            },
        }

        with open(transcript, "w") as f:
            f.write(json.dumps(session_entry) + "\n")
            f.write(json.dumps(assistant_entry) + "\n")

        return str(transcript)

    def test_subagent_stop_copies_token_counts_to_trace_metadata(
        self, config, agent_transcript_with_tokens, tmp_path
    ):
        """
        Bug #48: handle_subagent_stop must copy token counts to trace.metadata.

        Given a subagent transcript with token usage
        When handle_subagent_stop is called
        Then the trace-create update event must include token counts in metadata
        (in addition to the generation observation which already has them)
        """
        token_data = {
            "token_usage": {
                "input_tokens": 1500,
                "output_tokens": 300,
                "cache_read_tokens": 200,
                "cache_creation_tokens": 50,
            }
        }

        with (
            patch(
                "pacemaker.langfuse.push.push_batch_events", return_value=(True, 1)
            ) as mock_push,
            patch(
                "pacemaker.langfuse.incremental.parse_incremental_lines",
                return_value=token_data,
            ),
        ):
            result = orchestrator.handle_subagent_stop(
                config=config,
                subagent_trace_id="parent-session-123-subagent-code-reviewer-abcd1234",
                parent_transcript_path=None,
                agent_id="agent-abc123",
                agent_transcript_path=agent_transcript_with_tokens,
                last_assistant_message=None,
            )

            assert result is True
            assert mock_push.called

            batch = mock_push.call_args[0][3]

            # Find the trace-create (upsert) event - it's the first event
            trace_events = [e for e in batch if e["type"] == "trace-create"]
            assert len(trace_events) >= 1, "Must have at least one trace-create event"

            trace_body = trace_events[0]["body"]
            metadata = trace_body.get("metadata", {})

            # Bug #48: Token counts must be in trace.metadata
            assert (
                "input_tokens" in metadata
            ), "trace.metadata must have input_tokens (bug #48: tokens only go to generation, not trace)"
            assert metadata["input_tokens"] == 1500

            assert (
                "output_tokens" in metadata
            ), "trace.metadata must have output_tokens (bug #48)"
            assert metadata["output_tokens"] == 300

            assert (
                "cache_read_tokens" in metadata
            ), "trace.metadata must have cache_read_tokens (bug #48)"
            assert metadata["cache_read_tokens"] == 200

            assert (
                "cache_creation_tokens" in metadata
            ), "trace.metadata must have cache_creation_tokens (bug #48)"
            assert metadata["cache_creation_tokens"] == 50

    def test_subagent_stop_token_counts_zero_when_no_tokens(self, config, tmp_path):
        """
        Bug #48 edge case: When there are no token counts, metadata should have zeros.

        Ensures trace.metadata always has the token count fields (even if zero).
        """
        # Empty transcript with just text output, no token data
        transcript = tmp_path / "subagent-no-tokens.jsonl"
        session_entry = {
            "type": "session_start",
            "session_id": "subagent-789",
            "model": "claude-sonnet-4-5-20250929",
        }
        assistant_entry = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Done."}],
            },
        }
        with open(transcript, "w") as f:
            f.write(json.dumps(session_entry) + "\n")
            f.write(json.dumps(assistant_entry) + "\n")

        # Return token_data with all zeros
        empty_token_data = {
            "token_usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
            }
        }

        with (
            patch(
                "pacemaker.langfuse.push.push_batch_events", return_value=(True, 1)
            ) as mock_push,
            patch(
                "pacemaker.langfuse.incremental.parse_incremental_lines",
                return_value=empty_token_data,
            ),
        ):
            result = orchestrator.handle_subagent_stop(
                config=config,
                subagent_trace_id="parent-session-123-subagent-tdd-engineer-abcd1234",
                parent_transcript_path=None,
                agent_id="agent-abc123",
                agent_transcript_path=str(transcript),
                last_assistant_message=None,
            )

            assert result is True
            assert mock_push.called

            batch = mock_push.call_args[0][3]
            trace_events = [e for e in batch if e["type"] == "trace-create"]
            assert len(trace_events) >= 1

            trace_body = trace_events[0]["body"]
            metadata = trace_body.get("metadata", {})

            # Token fields must exist even when zero
            assert "input_tokens" in metadata
            assert metadata["input_tokens"] == 0
            assert "output_tokens" in metadata
            assert metadata["output_tokens"] == 0

    def test_subagent_stop_without_agent_transcript_no_token_metadata(self, config):
        """
        Bug #48 edge case: When agent_transcript_path is None, no token metadata added.

        When there's no subagent transcript, we can't know the token counts,
        so trace.metadata should not include token fields (or include zeros).
        The test verifies the function still succeeds.
        """
        with patch("pacemaker.langfuse.push.push_batch_events", return_value=(True, 1)):
            result = orchestrator.handle_subagent_stop(
                config=config,
                subagent_trace_id="parent-session-123-subagent-code-reviewer-abcd1234",
                parent_transcript_path=None,
                agent_id=None,
                agent_transcript_path=None,
                last_assistant_message="Done.",
            )

            # Must succeed even without transcript
            assert result is True
