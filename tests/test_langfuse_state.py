#!/usr/bin/env python3
"""
Tests for Langfuse state management.

Tests AC3: State Tracking Per Session
- Each session has its own state file
- State contains: session_id, last_pushed_line, trace_id
- Stale state files (>7 days old) are cleaned up
- Atomic writes prevent corruption
"""

import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from pacemaker.langfuse.state import StateManager


class TestStateManager:
    """Test StateManager class for state file operations."""

    @pytest.fixture
    def state_dir(self):
        """Create temporary state directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def manager(self, state_dir):
        """Create StateManager instance."""
        return StateManager(state_dir)

    def test_create_state_for_new_session(self, manager):
        """
        Test creating state for a new session.

        AC3: Each session has its own state file
        """
        session_id = "test-session-123"
        trace_id = "trace-abc"

        # Create state
        manager.create_or_update(session_id, trace_id=trace_id, last_pushed_line=0)

        # Verify state file exists
        state_file = Path(manager.state_dir) / f"{session_id}.json"
        assert state_file.exists()

        # Verify contents
        with open(state_file) as f:
            data = json.load(f)

        assert data["session_id"] == session_id
        assert data["trace_id"] == trace_id
        assert data["last_pushed_line"] == 0

    def test_read_existing_state(self, manager, state_dir):
        """
        Test reading state from existing file.

        AC3: State contains session_id, last_pushed_line, trace_id
        """
        session_id = "test-session-456"
        trace_id = "trace-def"
        last_pushed_line = 42

        # Create state file manually
        state_file = Path(state_dir) / f"{session_id}.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "session_id": session_id,
                    "trace_id": trace_id,
                    "last_pushed_line": last_pushed_line,
                },
                f,
            )

        # Read state
        state = manager.read(session_id)

        assert state is not None
        assert state["session_id"] == session_id
        assert state["trace_id"] == trace_id
        assert state["last_pushed_line"] == last_pushed_line

    def test_read_nonexistent_state_returns_none(self, manager):
        """Test reading state for session that doesn't exist."""
        state = manager.read("nonexistent-session")
        assert state is None

    def test_update_existing_state(self, manager):
        """Test updating existing state (incremental push)."""
        session_id = "test-session-789"
        trace_id = "trace-ghi"

        # Create initial state
        manager.create_or_update(session_id, trace_id=trace_id, last_pushed_line=10)

        # Update state (new lines pushed)
        manager.create_or_update(session_id, trace_id=trace_id, last_pushed_line=25)

        # Verify updated
        state = manager.read(session_id)
        assert state["last_pushed_line"] == 25
        assert state["trace_id"] == trace_id  # Unchanged

    def test_atomic_write_prevents_corruption(self, manager, state_dir):
        """
        Test atomic writes using temp file + rename.

        AC3: Atomic state file writes
        """
        session_id = "test-atomic"
        trace_id = "trace-atomic"

        # Create state
        manager.create_or_update(session_id, trace_id=trace_id, last_pushed_line=5)

        # Verify no temp files left behind
        temp_files = list(Path(state_dir).glob("*.tmp"))
        assert len(temp_files) == 0

        # Verify final file is valid JSON
        state_file = Path(state_dir) / f"{session_id}.json"
        assert state_file.exists()

        with open(state_file) as f:
            data = json.load(f)  # Should not raise JSONDecodeError

        assert data["session_id"] == session_id

    def test_cleanup_stale_files(self, manager, state_dir):
        """
        Test cleanup of state files >7 days old.

        AC3: Stale state files (>7 days old) are cleaned up
        """
        # Create old state file (8 days ago)
        old_session = "old-session"
        old_file = Path(state_dir) / f"{old_session}.json"
        with open(old_file, "w") as f:
            json.dump(
                {
                    "session_id": old_session,
                    "trace_id": "old-trace",
                    "last_pushed_line": 10,
                },
                f,
            )

        # Set mtime to 8 days ago
        eight_days_ago = time.time() - (8 * 24 * 60 * 60)
        os.utime(old_file, (eight_days_ago, eight_days_ago))

        # Create recent state file (1 day ago)
        recent_session = "recent-session"
        recent_file = Path(state_dir) / f"{recent_session}.json"
        with open(recent_file, "w") as f:
            json.dump(
                {
                    "session_id": recent_session,
                    "trace_id": "recent-trace",
                    "last_pushed_line": 5,
                },
                f,
            )

        # Run cleanup
        manager.cleanup_stale_files(max_age_days=7)

        # Verify old file deleted
        assert not old_file.exists()

        # Verify recent file preserved
        assert recent_file.exists()

    def test_cleanup_ignores_non_json_files(self, manager, state_dir):
        """Test cleanup only affects .json files."""
        # Create non-JSON file
        other_file = Path(state_dir) / "README.txt"
        other_file.write_text("This is not a state file")

        # Create old state file
        old_session = "old-session-2"
        old_file = Path(state_dir) / f"{old_session}.json"
        with open(old_file, "w") as f:
            json.dump({"session_id": old_session}, f)

        eight_days_ago = time.time() - (8 * 24 * 60 * 60)
        os.utime(old_file, (eight_days_ago, eight_days_ago))

        # Run cleanup
        manager.cleanup_stale_files(max_age_days=7)

        # Verify non-JSON file preserved
        assert other_file.exists()

        # Verify old JSON file deleted
        assert not old_file.exists()

    def test_multiple_sessions_have_separate_files(self, manager):
        """
        Test multiple sessions maintain separate state files.

        AC3: Each session has its own state file
        """
        session1 = "session-alpha"
        session2 = "session-beta"
        session3 = "session-gamma"

        # Create states for 3 sessions
        manager.create_or_update(session1, trace_id="trace-1", last_pushed_line=10)
        manager.create_or_update(session2, trace_id="trace-2", last_pushed_line=20)
        manager.create_or_update(session3, trace_id="trace-3", last_pushed_line=30)

        # Verify each has its own state
        state1 = manager.read(session1)
        state2 = manager.read(session2)
        state3 = manager.read(session3)

        assert state1["last_pushed_line"] == 10
        assert state2["last_pushed_line"] == 20
        assert state3["last_pushed_line"] == 30

        # Verify separate files exist
        assert (Path(manager.state_dir) / f"{session1}.json").exists()
        assert (Path(manager.state_dir) / f"{session2}.json").exists()
        assert (Path(manager.state_dir) / f"{session3}.json").exists()

    def test_state_dir_created_if_not_exists(self):
        """Test state directory is created if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nonexistent_dir = os.path.join(tmpdir, "langfuse_state")
            assert not os.path.exists(nonexistent_dir)

            # Create manager (should create directory)
            StateManager(nonexistent_dir)

            # Verify directory created
            assert os.path.exists(nonexistent_dir)
            assert os.path.isdir(nonexistent_dir)

    def test_corrupted_state_file_returns_none(self, manager, state_dir):
        """Test reading corrupted state file returns None gracefully."""
        session_id = "corrupted-session"
        state_file = Path(state_dir) / f"{session_id}.json"

        # Write corrupted JSON
        with open(state_file, "w") as f:
            f.write("{invalid json content")

        # Should return None, not raise exception
        state = manager.read(session_id)
        assert state is None
