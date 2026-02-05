#!/usr/bin/env python3
"""
Tests for Langfuse trace creation per user turn.

Each UserPromptSubmit creates a NEW trace (not one trace per session).
Traces are linked to the session via sessionId field.
"""


from pacemaker.langfuse.trace import create_trace_for_turn


class TestTraceCreationPerTurn:
    """Test trace creation for each user turn."""

    def test_create_trace_for_user_turn(self):
        """
        Test creating new trace for user prompt.

        Each UserPromptSubmit creates new trace with user message as name.
        """
        session_id = "test-session-mno"
        trace_id = f"{session_id}-turn-1"
        user_message = "implement feature X"
        user_id = "user@example.com"

        trace = create_trace_for_turn(
            session_id=session_id,
            trace_id=trace_id,
            user_message=user_message,
            user_id=user_id,
        )

        # Trace has unique ID (not session ID)
        assert trace["id"] == trace_id
        assert trace["id"] != session_id

        # Trace links to session
        assert trace["sessionId"] == session_id

        # Trace name includes user message
        assert user_message in trace["name"]

        # Trace has user ID
        assert trace["userId"] == user_id

        # Trace has timestamp
        assert "timestamp" in trace

    def test_create_multiple_traces_for_session(self):
        """
        Test creating multiple traces for different user turns in same session.

        Each user prompt creates separate trace, all linked to same session.
        """
        session_id = "test-session-pqr"
        user_id = "user@example.com"

        traces = []
        for turn, message in enumerate(["task 1", "task 2", "task 3"], start=1):
            trace_id = f"{session_id}-turn-{turn}"
            trace = create_trace_for_turn(
                session_id=session_id,
                trace_id=trace_id,
                user_message=message,
                user_id=user_id,
            )
            traces.append(trace)

        # All traces link to same session
        assert all(t["sessionId"] == session_id for t in traces)

        # Each trace has unique ID
        trace_ids = [t["id"] for t in traces]
        assert len(trace_ids) == len(set(trace_ids))

        # Each trace has different name (based on message)
        trace_names = [t["name"] for t in traces]
        assert len(trace_names) == len(set(trace_names))

    def test_trace_truncates_long_user_message(self):
        """
        Test that trace name truncates very long user messages.

        Prevents excessively long trace names in Langfuse UI.
        """
        session_id = "test-session-stu"
        trace_id = f"{session_id}-turn-1"
        # Very long user message (500 chars)
        user_message = "A" * 500
        user_id = "user@example.com"

        trace = create_trace_for_turn(
            session_id=session_id,
            trace_id=trace_id,
            user_message=user_message,
            user_id=user_id,
        )

        # Trace name should be truncated (max 100 chars)
        assert len(trace["name"]) <= 100

    def test_trace_includes_input_field(self):
        """
        Test that trace includes input field with full user message.

        The input field should contain the complete user prompt (not truncated).
        This provides full context in Langfuse for understanding the request.
        """
        session_id = "test-session-input"
        trace_id = f"{session_id}-turn-1"
        user_message = "Please implement feature X with detailed requirements"
        user_id = "user@example.com"

        trace = create_trace_for_turn(
            session_id=session_id,
            trace_id=trace_id,
            user_message=user_message,
            user_id=user_id,
        )

        # Trace should have input field with full user message
        assert "input" in trace
        assert trace["input"] == user_message

    def test_trace_input_not_truncated_for_long_messages(self):
        """
        Test that input field is NOT truncated for very long user messages.

        Unlike the name field (truncated at 100 chars), the input field
        should preserve the full user message regardless of length.
        """
        session_id = "test-session-long-input"
        trace_id = f"{session_id}-turn-1"
        # Very long user message (500 chars)
        user_message = "A" * 500
        user_id = "user@example.com"

        trace = create_trace_for_turn(
            session_id=session_id,
            trace_id=trace_id,
            user_message=user_message,
            user_id=user_id,
        )

        # Input should contain full message (not truncated)
        assert trace["input"] == user_message
        assert len(trace["input"]) == 500

        # But name should still be truncated
        assert len(trace["name"]) <= 100

    def test_trace_includes_project_context_in_metadata(self):
        """
        Test that trace includes project context in metadata when provided.

        Project context should be added to trace metadata to identify which
        project/repo each trace came from.
        """
        session_id = "test-session-project"
        trace_id = f"{session_id}-turn-1"
        user_message = "implement feature"
        user_id = "user@example.com"
        project_context = {
            "project_path": "/home/user/dev/my-project",
            "project_name": "my-project",
            "git_remote": "https://github.com/user/my-project.git",
            "git_branch": "main",
        }

        trace = create_trace_for_turn(
            session_id=session_id,
            trace_id=trace_id,
            user_message=user_message,
            user_id=user_id,
            project_context=project_context,
        )

        # Metadata should include project context
        assert "metadata" in trace
        assert "project_path" in trace["metadata"]
        assert "project_name" in trace["metadata"]
        assert "git_remote" in trace["metadata"]
        assert "git_branch" in trace["metadata"]

        # Values should match input
        assert trace["metadata"]["project_path"] == project_context["project_path"]
        assert trace["metadata"]["project_name"] == project_context["project_name"]
        assert trace["metadata"]["git_remote"] == project_context["git_remote"]
        assert trace["metadata"]["git_branch"] == project_context["git_branch"]

    def test_trace_metadata_empty_when_no_project_context(self):
        """
        Test that trace has empty metadata when project_context not provided.

        Backwards compatibility: if project_context is None or omitted,
        metadata should be empty dict (existing behavior).
        """
        session_id = "test-session-no-project"
        trace_id = f"{session_id}-turn-1"
        user_message = "implement feature"
        user_id = "user@example.com"

        # Call without project_context parameter
        trace = create_trace_for_turn(
            session_id=session_id,
            trace_id=trace_id,
            user_message=user_message,
            user_id=user_id,
        )

        # Metadata should be empty dict
        assert trace["metadata"] == {}

    def test_trace_handles_partial_project_context(self):
        """
        Test that trace handles project context with None values gracefully.

        When git is unavailable, git_remote and git_branch will be None.
        These should still be included in metadata.
        """
        session_id = "test-session-partial"
        trace_id = f"{session_id}-turn-1"
        user_message = "implement feature"
        user_id = "user@example.com"
        project_context = {
            "project_path": "/home/user/dev/my-project",
            "project_name": "my-project",
            "git_remote": None,  # Not a git repo
            "git_branch": None,  # Not a git repo
        }

        trace = create_trace_for_turn(
            session_id=session_id,
            trace_id=trace_id,
            user_message=user_message,
            user_id=user_id,
            project_context=project_context,
        )

        # Metadata should include all fields even with None values
        assert trace["metadata"]["project_path"] == "/home/user/dev/my-project"
        assert trace["metadata"]["project_name"] == "my-project"
        assert trace["metadata"]["git_remote"] is None
        assert trace["metadata"]["git_branch"] is None

    def test_trace_includes_model_in_metadata(self):
        """
        Test that trace includes model in metadata when provided.

        Model name should be added to trace metadata for cost tracking
        and analytics in Langfuse dashboards.
        """
        session_id = "test-session-model"
        trace_id = f"{session_id}-turn-1"
        user_message = "implement feature"
        user_id = "user@example.com"
        model = "claude-opus-4-5-20250929"
        project_context = {
            "project_path": "/home/user/dev/my-project",
            "project_name": "my-project",
            "git_remote": "https://github.com/user/my-project.git",
            "git_branch": "main",
        }

        trace = create_trace_for_turn(
            session_id=session_id,
            trace_id=trace_id,
            user_message=user_message,
            user_id=user_id,
            model=model,
            project_context=project_context,
        )

        # Metadata should include model
        assert "metadata" in trace
        assert "model" in trace["metadata"]
        assert trace["metadata"]["model"] == model

        # Should also include project context
        assert trace["metadata"]["project_path"] == "/home/user/dev/my-project"
        assert trace["metadata"]["project_name"] == "my-project"

    def test_trace_metadata_without_model_parameter(self):
        """
        Test that trace works without model parameter (backward compatibility).

        When model is not provided, metadata should not include model field.
        """
        session_id = "test-session-no-model"
        trace_id = f"{session_id}-turn-1"
        user_message = "implement feature"
        user_id = "user@example.com"
        project_context = {
            "project_path": "/home/user/dev/my-project",
            "project_name": "my-project",
        }

        # Call without model parameter
        trace = create_trace_for_turn(
            session_id=session_id,
            trace_id=trace_id,
            user_message=user_message,
            user_id=user_id,
            project_context=project_context,
        )

        # Metadata should not include model field
        assert "model" not in trace["metadata"]
