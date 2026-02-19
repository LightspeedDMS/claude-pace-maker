#!/usr/bin/env python3
"""
Tests for state.py race condition fix.

Tests that the atomic write in StateManager.create_or_update() uses
PID-unique temp file names to prevent concurrent hooks from clobbering
each other's temp files.

TDD: These tests are written FIRST - production code comes after.
"""

import json
import os
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from pacemaker.langfuse.state import StateManager


class TestTempFileIncludesPID:
    """Test that temp file names include PID for uniqueness."""

    @pytest.fixture
    def state_dir(self):
        """Create temporary state directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def manager(self, state_dir):
        """Create StateManager instance."""
        return StateManager(state_dir)

    def test_temp_file_uses_pid_in_name(self, manager, state_dir):
        """Temp file should include os.getpid() to prevent collisions."""
        session_id = "test-session"
        current_pid = os.getpid()

        # Patch Path.rename to capture what temp file was created
        # We need to intercept BEFORE the rename happens
        original_rename = Path.rename
        temp_files_seen = []

        def capture_rename(self_path, target):
            temp_files_seen.append(str(self_path))
            return original_rename(self_path, target)

        with patch.object(Path, "rename", capture_rename):
            manager.create_or_update(
                session_id=session_id,
                trace_id="trace-1",
                last_pushed_line=0,
            )

        # Verify at least one temp file was created with PID in name
        assert len(temp_files_seen) > 0, "Should have created a temp file"
        temp_file_name = temp_files_seen[0]
        assert (
            str(current_pid) in temp_file_name
        ), f"Temp file '{temp_file_name}' should contain PID '{current_pid}'"

    def test_temp_file_name_format(self, manager, state_dir):
        """Temp file should follow format: {session_id}.json.tmp.{pid}"""
        session_id = "test-format"
        current_pid = os.getpid()
        expected_temp_name = f"{session_id}.json.tmp.{current_pid}"

        original_rename = Path.rename
        temp_files_seen = []

        def capture_rename(self_path, target):
            temp_files_seen.append(self_path.name)
            return original_rename(self_path, target)

        with patch.object(Path, "rename", capture_rename):
            manager.create_or_update(
                session_id=session_id,
                trace_id="trace-1",
                last_pushed_line=0,
            )

        assert len(temp_files_seen) > 0
        assert (
            temp_files_seen[0] == expected_temp_name
        ), f"Expected temp file name '{expected_temp_name}', got '{temp_files_seen[0]}'"

    def test_no_temp_files_left_after_successful_write(self, manager, state_dir):
        """After successful write, no temp files should remain."""
        manager.create_or_update(
            session_id="test-cleanup",
            trace_id="trace-1",
            last_pushed_line=0,
        )

        # Check for any temp files (old format or new format)
        temp_files = list(Path(state_dir).glob("*.tmp*"))
        assert (
            len(temp_files) == 0
        ), f"No temp files should remain after successful write, found: {temp_files}"


class TestConcurrentWriteSafety:
    """Test that concurrent writes to same session don't corrupt data."""

    @pytest.fixture
    def state_dir(self):
        """Create temporary state directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_concurrent_writes_preserve_data_integrity(self, state_dir):
        """
        Multiple threads writing to the same session_id should not
        corrupt the final state file. The last writer wins, but the
        file should always be valid JSON.
        """
        manager = StateManager(state_dir)
        session_id = "concurrent-session"
        errors = []
        write_count = 20

        def writer(thread_id):
            try:
                for i in range(write_count):
                    manager.create_or_update(
                        session_id=session_id,
                        trace_id=f"trace-{thread_id}",
                        last_pushed_line=thread_id * 1000 + i,
                    )
            except Exception as e:
                errors.append(e)

        # Launch 5 threads writing concurrently
        threads = []
        for t_id in range(5):
            t = threading.Thread(target=writer, args=(t_id,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # No errors should have occurred
        assert len(errors) == 0, f"Concurrent writes raised errors: {errors}"

        # Final state file should be valid JSON
        state_file = Path(state_dir) / f"{session_id}.json"
        assert state_file.exists(), "State file should exist after concurrent writes"

        with open(state_file) as f:
            data = json.load(f)  # Should not raise JSONDecodeError

        # Data should have all required fields
        assert "session_id" in data
        assert "trace_id" in data
        assert "last_pushed_line" in data
        assert data["session_id"] == session_id

    def test_concurrent_writes_no_leftover_temp_files(self, state_dir):
        """After concurrent writes complete, no temp files should remain."""
        manager = StateManager(state_dir)
        session_id = "concurrent-cleanup"

        def writer(thread_id):
            for i in range(10):
                manager.create_or_update(
                    session_id=session_id,
                    trace_id=f"trace-{thread_id}",
                    last_pushed_line=i,
                )

        threads = []
        for t_id in range(5):
            t = threading.Thread(target=writer, args=(t_id,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # No temp files should remain
        temp_files = list(Path(state_dir).glob("*.tmp*"))
        assert (
            len(temp_files) == 0
        ), f"No temp files should remain after concurrent writes, found: {temp_files}"

    def test_different_sessions_independent(self, state_dir):
        """Concurrent writes to different sessions should not interfere."""
        manager = StateManager(state_dir)
        errors = []

        def writer(session_id, trace_id, line):
            try:
                for i in range(10):
                    manager.create_or_update(
                        session_id=session_id,
                        trace_id=trace_id,
                        last_pushed_line=line + i,
                    )
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=("session-a", "trace-a", 0)),
            threading.Thread(target=writer, args=("session-b", "trace-b", 100)),
            threading.Thread(target=writer, args=("session-c", "trace-c", 200)),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

        # Each session should have its own valid state
        for session_id in ["session-a", "session-b", "session-c"]:
            state = manager.read(session_id)
            assert state is not None, f"State for {session_id} should exist"
            assert state["session_id"] == session_id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
