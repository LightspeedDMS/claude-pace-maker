#!/usr/bin/env python3
"""
Tests for Langfuse subagent trace hierarchy functionality.

Tests Story #31 AC1: Subagent Transcript Detection
"""

import json
import tempfile
from pathlib import Path

import pytest

from pacemaker.langfuse import subagent


class TestSubagentTranscriptDetection:
    """
    Test AC1: Subagent Transcript Detection

    Given a Claude Code session spawns a subagent via Task tool
    And the subagent transcript is created as agent-{uuid}.jsonl
    When the Langfuse module scans for transcripts
    Then agent-*.jsonl files are identified as subagent transcripts
    And the isSidechain: true marker confirms subagent status
    """

    @pytest.fixture
    def main_transcript_file(self):
        """Create a main session transcript."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, prefix="transcript-"
        ) as f:
            # Main session transcript
            f.write(
                json.dumps(
                    {
                        "type": "session_start",
                        "session_id": "main-session-123",
                        "model": "claude-sonnet-4-5",
                        "isSidechain": False,
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps({"message": {"role": "user", "content": "Run task"}}) + "\n"
            )
            transcript_path = f.name

        yield transcript_path
        Path(transcript_path).unlink()

    @pytest.fixture
    def subagent_transcript_file(self):
        """Create a subagent transcript with agent-{uuid}.jsonl naming."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".jsonl",
            delete=False,
            prefix="agent-abc-123-",
        ) as f:
            # Subagent transcript with isSidechain: true
            f.write(
                json.dumps(
                    {
                        "type": "session_start",
                        "session_id": "subagent-456",
                        "model": "claude-sonnet-4-5",
                        "isSidechain": True,
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps({"message": {"role": "assistant", "content": "Working"}})
                + "\n"
            )
            transcript_path = f.name

        yield transcript_path
        Path(transcript_path).unlink()

    def test_is_subagent_transcript_by_filename(self, subagent_transcript_file):
        """Test subagent detection by agent-*.jsonl filename pattern."""
        # Should detect by filename pattern
        assert subagent.is_subagent_transcript(subagent_transcript_file)

    def test_is_not_subagent_transcript_main_file(self, main_transcript_file):
        """Test main session transcript is not detected as subagent."""
        # Main transcript should NOT be detected as subagent
        assert not subagent.is_subagent_transcript(main_transcript_file)

    def test_is_subagent_transcript_by_sidechain_marker(self, subagent_transcript_file):
        """Test isSidechain: true marker confirms subagent status."""
        # Should verify isSidechain: true in transcript
        assert subagent.verify_sidechain_marker(subagent_transcript_file)

    def test_verify_sidechain_marker_returns_false_for_main(self, main_transcript_file):
        """Test isSidechain: false in main session."""
        # Main session has isSidechain: false
        assert not subagent.verify_sidechain_marker(main_transcript_file)

    def test_detect_subagent_by_both_criteria(self, subagent_transcript_file):
        """Test combined detection: filename AND isSidechain marker."""
        # Should detect by both filename pattern and marker
        is_subagent = subagent.is_subagent_transcript(subagent_transcript_file)
        has_marker = subagent.verify_sidechain_marker(subagent_transcript_file)

        assert is_subagent
        assert has_marker


class TestIndependentStateTracking:
    """
    Test AC2: Independent State Tracking Per Subagent

    Given a main session with 3 subagent sessions
    When incremental pushes occur
    Then 4 state files exist: 1 main + 3 subagents
    And each state file tracks: last_pushed_line, trace_id, parent_observation_id
    And state files are independent
    """

    @pytest.fixture
    def state_dir(self):
        """Create temporary state directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_state_tracks_parent_observation_id(self, state_dir):
        """Test state schema includes parent_observation_id field."""
        session_id = "subagent-123"
        trace_id = "trace-abc"
        parent_observation_id = "parent-obs-456"

        # Create subagent state with parent link
        subagent.create_subagent_state(
            state_dir=state_dir,
            session_id=session_id,
            trace_id=trace_id,
            parent_observation_id=parent_observation_id,
            last_pushed_line=0,
        )

        # Read state and verify fields
        state = subagent.read_subagent_state(state_dir, session_id)

        assert state["session_id"] == session_id
        assert state["trace_id"] == trace_id
        assert state["parent_observation_id"] == parent_observation_id
        assert state["last_pushed_line"] == 0

    def test_main_and_subagent_states_independent(self, state_dir):
        """Test main session and subagents have separate state files."""
        main_id = "main-session"
        sub1_id = "subagent-1"
        sub2_id = "subagent-2"
        sub3_id = "subagent-3"

        # Create main session state
        subagent.create_subagent_state(
            state_dir=state_dir,
            session_id=main_id,
            trace_id="main-trace",
            parent_observation_id=None,  # Main has no parent
            last_pushed_line=10,
        )

        # Create 3 subagent states
        for idx, session_id in enumerate([sub1_id, sub2_id, sub3_id], start=1):
            subagent.create_subagent_state(
                state_dir=state_dir,
                session_id=session_id,
                trace_id=f"sub-trace-{idx}",
                parent_observation_id=f"parent-obs-{idx}",
                last_pushed_line=idx * 5,
            )

        # Verify 4 separate state files
        state_files = list(Path(state_dir).glob("*.json"))
        assert len(state_files) == 4

        # Verify each state is independent
        main_state = subagent.read_subagent_state(state_dir, main_id)
        sub1_state = subagent.read_subagent_state(state_dir, sub1_id)
        sub2_state = subagent.read_subagent_state(state_dir, sub2_id)
        sub3_state = subagent.read_subagent_state(state_dir, sub3_id)

        assert main_state["last_pushed_line"] == 10
        assert sub1_state["last_pushed_line"] == 5
        assert sub2_state["last_pushed_line"] == 10
        assert sub3_state["last_pushed_line"] == 15

    def test_update_subagent_state_incremental_push(self, state_dir):
        """Test updating subagent state after incremental push."""
        session_id = "subagent-456"

        # Create initial state
        subagent.create_subagent_state(
            state_dir=state_dir,
            session_id=session_id,
            trace_id="trace-xyz",
            parent_observation_id="parent-123",
            last_pushed_line=10,
        )

        # Update state (simulate incremental push)
        subagent.update_subagent_state(
            state_dir=state_dir, session_id=session_id, last_pushed_line=25
        )

        # Verify updated
        state = subagent.read_subagent_state(state_dir, session_id)
        assert state["last_pushed_line"] == 25
        assert state["parent_observation_id"] == "parent-123"  # Unchanged


class TestChildSpanCreation:
    """
    Test AC3: Child Span Creation with Parent Linking

    Given a main session trace exists
    And the main session spawns a subagent via Task tool
    When the subagent's SubagentStart hook fires
    Then a child span is created in Langfuse
    And the child span's parent_observation_id links to the Task tool span
    And the Langfuse UI shows the subagent nested under the Task tool call
    """

    @pytest.fixture
    def mock_langfuse_client(self):
        """Mock Langfuse client for testing span creation."""

        class MockClient:
            def __init__(self):
                self.created_spans = []

            def create_span(self, trace_id, parent_observation_id, name, metadata):
                span_id = f"span-{len(self.created_spans)}"
                span = {
                    "id": span_id,
                    "trace_id": trace_id,
                    "parent_observation_id": parent_observation_id,
                    "name": name,
                    "metadata": metadata,
                }
                self.created_spans.append(span)
                return span_id

        return MockClient()

    def test_create_child_span_with_parent_link(self, mock_langfuse_client):
        """Test child span is created with parent_observation_id linking to Task tool."""
        parent_trace_id = "main-trace-123"
        parent_observation_id = "task-tool-obs-456"
        subagent_session_id = "subagent-789"

        # Create child span for subagent
        child_span_id = subagent.create_child_span(
            client=mock_langfuse_client,
            parent_trace_id=parent_trace_id,
            parent_observation_id=parent_observation_id,
            subagent_session_id=subagent_session_id,
            subagent_name="code-reviewer",
        )

        # Verify span was created
        assert child_span_id is not None
        assert len(mock_langfuse_client.created_spans) == 1

        # Verify parent linking
        span = mock_langfuse_client.created_spans[0]
        assert span["trace_id"] == parent_trace_id
        assert span["parent_observation_id"] == parent_observation_id
        assert span["name"] == "subagent:code-reviewer"
        assert span["metadata"]["session_id"] == subagent_session_id

    def test_child_span_nests_under_parent_trace(self, mock_langfuse_client):
        """Test child span belongs to same trace as parent (hierarchical nesting)."""
        parent_trace_id = "main-trace-abc"
        parent_obs_1 = "tool-obs-1"
        parent_obs_2 = "tool-obs-2"

        # Create two child spans under same parent trace
        subagent.create_child_span(
            client=mock_langfuse_client,
            parent_trace_id=parent_trace_id,
            parent_observation_id=parent_obs_1,
            subagent_session_id="subagent-1",
            subagent_name="tdd-engineer",
        )

        subagent.create_child_span(
            client=mock_langfuse_client,
            parent_trace_id=parent_trace_id,
            parent_observation_id=parent_obs_2,
            subagent_session_id="subagent-2",
            subagent_name="code-reviewer",
        )

        # Both children should belong to same trace
        assert mock_langfuse_client.created_spans[0]["trace_id"] == parent_trace_id
        assert mock_langfuse_client.created_spans[1]["trace_id"] == parent_trace_id

        # But have different parent observations
        assert (
            mock_langfuse_client.created_spans[0]["parent_observation_id"]
            == parent_obs_1
        )
        assert (
            mock_langfuse_client.created_spans[1]["parent_observation_id"]
            == parent_obs_2
        )

    def test_child_span_creation_returns_observation_id(self, mock_langfuse_client):
        """Test create_child_span returns observation_id for subagent state tracking."""
        parent_trace_id = "trace-xyz"
        parent_observation_id = "parent-obs-123"

        # Create child span
        child_observation_id = subagent.create_child_span(
            client=mock_langfuse_client,
            parent_trace_id=parent_trace_id,
            parent_observation_id=parent_observation_id,
            subagent_session_id="sub-456",
            subagent_name="manual-test-executor",
        )

        # Should return observation ID for state tracking
        assert child_observation_id is not None
        assert child_observation_id.startswith("span-")


class TestSubagentStartHookIntegration:
    """
    Test AC3: SubagentStart Hook Integration with Child Span Creation

    Given SubagentStart hook fires with subagent transcript path
    And parent session has Langfuse state with trace_id
    When the hook processes the subagent start event
    Then child span is created with parent linking
    And subagent state file is created with child span info
    """

    @pytest.fixture
    def state_dir(self):
        """Create temporary state directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def parent_state(self, state_dir):
        """Create parent session Langfuse state."""
        parent_session_id = "main-session-123"
        parent_trace_id = "main-trace-456"

        # Create parent state file
        subagent.create_subagent_state(
            state_dir=state_dir,
            session_id=parent_session_id,
            trace_id=parent_trace_id,
            parent_observation_id=None,
            last_pushed_line=10,
        )

        return {
            "session_id": parent_session_id,
            "trace_id": parent_trace_id,
        }

    @pytest.fixture
    def mock_langfuse_client(self):
        """Mock Langfuse client for testing."""

        class MockClient:
            def __init__(self):
                self.created_spans = []

            def create_span(self, trace_id, parent_observation_id, name, metadata):
                span_id = f"span-{len(self.created_spans)}"
                self.created_spans.append(
                    {
                        "id": span_id,
                        "trace_id": trace_id,
                        "parent_observation_id": parent_observation_id,
                        "name": name,
                        "metadata": metadata,
                    }
                )
                return span_id

        return MockClient()

    def test_subagent_start_creates_child_span_and_state(
        self, state_dir, parent_state, mock_langfuse_client
    ):
        """Test SubagentStart hook creates child span and subagent state."""
        subagent_session_id = "subagent-789"
        subagent_transcript = f"agent-{subagent_session_id}.jsonl"
        parent_observation_id = "task-tool-obs-123"

        # Simulate SubagentStart hook processing
        child_span_id = subagent.handle_subagent_start(
            client=mock_langfuse_client,
            state_dir=state_dir,
            subagent_session_id=subagent_session_id,
            subagent_transcript_path=subagent_transcript,
            parent_session_id=parent_state["session_id"],
            parent_observation_id=parent_observation_id,
            subagent_name="code-reviewer",
        )

        # Verify child span was created
        assert child_span_id is not None
        assert len(mock_langfuse_client.created_spans) == 1

        span = mock_langfuse_client.created_spans[0]
        assert span["trace_id"] == parent_state["trace_id"]
        assert span["parent_observation_id"] == parent_observation_id

        # Verify subagent state was created
        subagent_state = subagent.read_subagent_state(state_dir, subagent_session_id)
        assert subagent_state is not None
        assert subagent_state["session_id"] == subagent_session_id
        assert subagent_state["trace_id"] == parent_state["trace_id"]
        assert subagent_state["parent_observation_id"] == child_span_id
        assert subagent_state["last_pushed_line"] == 0
