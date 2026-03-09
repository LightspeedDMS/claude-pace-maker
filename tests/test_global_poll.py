"""Tests for global API poll coordination (Story #43).

Verifies that should_poll_globally() provides atomic cross-session poll
coordination via SQLite BEGIN IMMEDIATE, and that get_last_pacing_decision()
returns the most recent decision regardless of session_id.
"""

import sqlite3
import time
import threading
import tempfile
import os
from datetime import datetime


from pacemaker import database


class TestShouldPollGlobally:
    """Tests for should_poll_globally() atomic check-and-update."""

    def setup_method(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        database.initialize_database(self.db_path)

    def teardown_method(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_first_poll_returns_true(self):
        """Empty global_poll_state table -> should return True and insert row."""
        result = database.should_poll_globally(
            self.db_path, poll_interval=300, session_id="session-1"
        )
        assert result is True
        # Verify row was inserted
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT last_poll_time, last_poll_session FROM global_poll_state WHERE id = 1"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[1] == "session-1"
        assert row[0] > 0  # timestamp should be set

    def test_interval_not_elapsed_returns_false(self):
        """When less than poll_interval has elapsed, should return False."""
        # First call claims the slot
        database.should_poll_globally(
            self.db_path, poll_interval=300, session_id="session-1"
        )
        # Second call immediately after should return False
        result = database.should_poll_globally(
            self.db_path, poll_interval=300, session_id="session-2"
        )
        assert result is False

    def test_interval_elapsed_returns_true(self):
        """When poll_interval has elapsed, should return True and update timestamp."""
        # Insert a row with old timestamp (400 seconds ago)
        old_time = time.time() - 400
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT OR REPLACE INTO global_poll_state "
            "(id, last_poll_time, last_poll_session) VALUES (1, ?, ?)",
            (old_time, "session-old"),
        )
        conn.commit()
        conn.close()

        result = database.should_poll_globally(
            self.db_path, poll_interval=300, session_id="session-new"
        )
        assert result is True

        # Verify timestamp was updated
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT last_poll_time, last_poll_session FROM global_poll_state WHERE id = 1"
        ).fetchone()
        conn.close()
        assert row[0] > old_time
        assert row[1] == "session-new"

    def test_atomicity_two_rapid_calls(self):
        """Two rapid concurrent calls should result in exactly one True."""
        # Set timestamp to old (so both would want to poll)
        old_time = time.time() - 400
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT OR REPLACE INTO global_poll_state "
            "(id, last_poll_time, last_poll_session) VALUES (1, ?, ?)",
            (old_time, "session-old"),
        )
        conn.commit()
        conn.close()

        results = []
        barrier = threading.Barrier(2)

        def call_poll(session_id):
            barrier.wait()  # Synchronize start
            r = database.should_poll_globally(
                self.db_path, poll_interval=300, session_id=session_id
            )
            results.append(r)

        t1 = threading.Thread(target=call_poll, args=("session-A",))
        t2 = threading.Thread(target=call_poll, args=("session-B",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert results.count(True) == 1
        assert results.count(False) == 1

    def test_global_poll_state_table_created(self):
        """Verify initialize_database creates global_poll_state table."""
        conn = sqlite3.connect(self.db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='global_poll_state'"
        ).fetchall()
        conn.close()
        assert len(tables) == 1

    def test_fail_open_on_error(self):
        """On database error, should return True (fail-open for availability)."""
        result = database.should_poll_globally(
            "/nonexistent/path/db.sqlite", poll_interval=300, session_id="s1"
        )
        assert result is True


class TestGetLastPacingDecisionGlobal:
    """Tests for get_last_pacing_decision without session_id filter."""

    def setup_method(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        database.initialize_database(self.db_path)

    def teardown_method(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_returns_none_when_empty(self):
        """Empty pacing_decisions table should return None."""
        result = database.get_last_pacing_decision(self.db_path)
        assert result is None

    def test_returns_most_recent_any_session(self):
        """Should return most recent decision regardless of which session stored it."""
        # Insert decisions from different sessions
        database.insert_pacing_decision(
            self.db_path, datetime(2024, 1, 1, 12, 0, 0), True, 30, "session-A"
        )
        database.insert_pacing_decision(
            self.db_path, datetime(2024, 1, 1, 12, 5, 0), True, 45, "session-B"
        )

        result = database.get_last_pacing_decision(self.db_path)
        assert result is not None
        assert result["delay_seconds"] == 45  # Most recent (session-B)
        assert result["should_throttle"] is True

    def test_decision_sharing_across_sessions(self):
        """Session A stores decision, session B can retrieve it globally."""
        database.insert_pacing_decision(
            self.db_path,
            datetime(2024, 6, 15, 10, 0, 0),
            True,
            60,
            "session-A",
        )

        # Session B retrieves without specifying session_id
        result = database.get_last_pacing_decision(self.db_path)
        assert result is not None
        assert result["delay_seconds"] == 60
        assert result["session_id"] == "session-A"
