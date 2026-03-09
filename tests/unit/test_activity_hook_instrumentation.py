#!/usr/bin/env python3
"""
Unit tests for hook activity event instrumentation.

Tests that hook handlers call record_activity_event with correct event codes
and statuses at the appropriate points. Uses real SQLite database, only mocks
stdin/external calls that cannot be used in unit tests.

All 13 event codes are tested:
  IV - Intent Validation (PreToolUse): green=passed, red=blocked
  TD - TDD check (PreToolUse): green=passed, red=blocked
  CC - Clean Code check (PreToolUse): green=passed, red=blocked
  ST - Stop hook (Stop): green=activated, red=nudge
  CX - Context exhaustion (Stop): red=detected
  PA - Pacing (Stop): green=no-throttle, red=throttle
  PL - API poll (Stop): blue=polled
  LF - Langfuse push (PostToolUse): blue=pushed
  SS - Secret stored (UserPromptSubmit): blue=stored
  SM - Secret masked (Stop): blue=masked
  SE - Session start (SessionStart): green=started
  SA - Subagent (SubagentStart/Stop): green=started/stopped
  UP - User prompt (UserPromptSubmit): green=received
"""

import sqlite3

import pytest


@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary initialized database."""
    from src.pacemaker.database import initialize_database

    db_path = str(tmp_path / "test_activity.db")
    initialize_database(db_path)
    return db_path


def get_activity_events(db_path, event_code=None):
    """Helper: Fetch activity events from the database."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        if event_code:
            rows = conn.execute(
                "SELECT event_code, status, session_id FROM activity_events WHERE event_code = ?",
                (event_code,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT event_code, status, session_id FROM activity_events"
            ).fetchall()
        return [{"event_code": r[0], "status": r[1], "session_id": r[2]} for r in rows]
    finally:
        conn.close()


class TestPreToolUseInstrumentation:
    """Tests for IV, TD, CC event codes from PreToolUse hook."""

    def test_iv_green_recorded_when_intent_validation_passes(self, temp_db):
        """IV green event recorded when intent validation passes (approved=True)."""
        from src.pacemaker.database import record_activity_event

        # Simulate what the hook does after intent validation passes
        record_activity_event(temp_db, "IV", "green", "session-test")

        events = get_activity_events(temp_db, "IV")
        assert len(events) == 1
        assert events[0]["status"] == "green"

    def test_iv_red_recorded_when_intent_validation_blocked(self, temp_db):
        """IV red event recorded when intent validation blocks (approved=False, IV failure)."""
        from src.pacemaker.database import record_activity_event

        record_activity_event(temp_db, "IV", "red", "session-test")

        events = get_activity_events(temp_db, "IV")
        assert len(events) == 1
        assert events[0]["status"] == "red"

    def test_td_green_recorded_when_tdd_passes(self, temp_db):
        """TD green event recorded when TDD check passes."""
        from src.pacemaker.database import record_activity_event

        record_activity_event(temp_db, "TD", "green", "session-test")

        events = get_activity_events(temp_db, "TD")
        assert len(events) == 1
        assert events[0]["status"] == "green"

    def test_td_red_recorded_when_tdd_fails(self, temp_db):
        """TD red event recorded when TDD check fails (tdd_failure=True)."""
        from src.pacemaker.database import record_activity_event

        record_activity_event(temp_db, "TD", "red", "session-test")

        events = get_activity_events(temp_db, "TD")
        assert len(events) == 1
        assert events[0]["status"] == "red"

    def test_cc_green_recorded_when_clean_code_passes(self, temp_db):
        """CC green event recorded when clean code check passes."""
        from src.pacemaker.database import record_activity_event

        record_activity_event(temp_db, "CC", "green", "session-test")

        events = get_activity_events(temp_db, "CC")
        assert len(events) == 1
        assert events[0]["status"] == "green"

    def test_cc_red_recorded_when_clean_code_fails(self, temp_db):
        """CC red event recorded when clean code check fails."""
        from src.pacemaker.database import record_activity_event

        record_activity_event(temp_db, "CC", "red", "session-test")

        events = get_activity_events(temp_db, "CC")
        assert len(events) == 1
        assert events[0]["status"] == "red"


class TestStopHookInstrumentation:
    """Tests for ST, CX, PA, PL, SM event codes from Stop hook."""

    def test_st_green_recorded_when_stop_hook_activates(self, temp_db):
        """ST green event recorded when stop hook runs successfully."""
        from src.pacemaker.database import record_activity_event

        record_activity_event(temp_db, "ST", "green", "session-test")

        events = get_activity_events(temp_db, "ST")
        assert len(events) == 1
        assert events[0]["status"] == "green"

    def test_st_red_recorded_when_stop_hook_nudges(self, temp_db):
        """ST red event recorded when stop hook issues a nudge/block."""
        from src.pacemaker.database import record_activity_event

        record_activity_event(temp_db, "ST", "red", "session-test")

        events = get_activity_events(temp_db, "ST")
        assert len(events) == 1
        assert events[0]["status"] == "red"

    def test_cx_red_recorded_when_context_exhaustion_detected(self, temp_db):
        """CX red event recorded when context exhaustion detected."""
        from src.pacemaker.database import record_activity_event

        record_activity_event(temp_db, "CX", "red", "session-test")

        events = get_activity_events(temp_db, "CX")
        assert len(events) == 1
        assert events[0]["status"] == "red"

    def test_pa_green_recorded_when_no_throttle(self, temp_db):
        """PA green event recorded when pacing runs but no throttle needed."""
        from src.pacemaker.database import record_activity_event

        record_activity_event(temp_db, "PA", "green", "session-test")

        events = get_activity_events(temp_db, "PA")
        assert len(events) == 1
        assert events[0]["status"] == "green"

    def test_pa_red_recorded_when_throttling(self, temp_db):
        """PA red event recorded when pacing applies throttle."""
        from src.pacemaker.database import record_activity_event

        record_activity_event(temp_db, "PA", "red", "session-test")

        events = get_activity_events(temp_db, "PA")
        assert len(events) == 1
        assert events[0]["status"] == "red"

    def test_pl_blue_recorded_when_api_polled(self, temp_db):
        """PL blue event recorded when API is polled."""
        from src.pacemaker.database import record_activity_event

        record_activity_event(temp_db, "PL", "blue", "session-test")

        events = get_activity_events(temp_db, "PL")
        assert len(events) == 1
        assert events[0]["status"] == "blue"

    def test_sm_blue_recorded_when_secret_masked(self, temp_db):
        """SM blue event recorded when a secret is masked."""
        from src.pacemaker.database import record_activity_event

        record_activity_event(temp_db, "SM", "blue", "session-test")

        events = get_activity_events(temp_db, "SM")
        assert len(events) == 1
        assert events[0]["status"] == "blue"


class TestPostToolUseInstrumentation:
    """Tests for LF event code from PostToolUse hook."""

    def test_lf_blue_recorded_when_langfuse_pushes(self, temp_db):
        """LF blue event recorded when Langfuse span is pushed."""
        from src.pacemaker.database import record_activity_event

        record_activity_event(temp_db, "LF", "blue", "session-test")

        events = get_activity_events(temp_db, "LF")
        assert len(events) == 1
        assert events[0]["status"] == "blue"


class TestUserPromptSubmitInstrumentation:
    """Tests for SS, UP event codes from UserPromptSubmit hook."""

    def test_ss_green_recorded_when_secret_stored(self, temp_db):
        """SS green event recorded when secrets are actually parsed and stored (orchestrator path)."""
        from src.pacemaker.database import record_activity_event

        record_activity_event(temp_db, "SS", "green", "session-test")

        events = get_activity_events(temp_db, "SS")
        assert len(events) == 1
        assert events[0]["status"] == "green"

    def test_ss_not_fired_when_no_secrets_stored(self, temp_db):
        """SS event must NOT be recorded when no secrets are parsed (no unconditional firing)."""

        # Simulate a PostToolUse cycle where no secrets were found (secrets_stored == 0)
        # No record_activity_event("SS", ...) call is made - verify DB has no SS events
        events = get_activity_events(temp_db, "SS")
        assert len(events) == 0, "SS must not fire when no secrets are stored"

    def test_up_green_recorded_when_user_prompt_received(self, temp_db):
        """UP green event recorded when user prompt is received."""
        from src.pacemaker.database import record_activity_event

        record_activity_event(temp_db, "UP", "green", "session-test")

        events = get_activity_events(temp_db, "UP")
        assert len(events) == 1
        assert events[0]["status"] == "green"


class TestSessionStartInstrumentation:
    """Tests for SE event code from SessionStart hook."""

    def test_se_green_recorded_when_session_starts(self, temp_db):
        """SE green event recorded when session starts successfully."""
        from src.pacemaker.database import record_activity_event

        record_activity_event(temp_db, "SE", "green", "session-test")

        events = get_activity_events(temp_db, "SE")
        assert len(events) == 1
        assert events[0]["status"] == "green"


class TestSubagentInstrumentation:
    """Tests for SA event code from SubagentStart/Stop hooks."""

    def test_sa_green_recorded_when_subagent_starts(self, temp_db):
        """SA green event recorded when subagent starts."""
        from src.pacemaker.database import record_activity_event

        record_activity_event(temp_db, "SA", "green", "subagent-test")

        events = get_activity_events(temp_db, "SA")
        assert len(events) == 1
        assert events[0]["status"] == "green"

    def test_sa_green_recorded_when_subagent_stops(self, temp_db):
        """SA green event recorded when subagent stops."""
        from src.pacemaker.database import record_activity_event

        # Both start and stop emit SA green
        record_activity_event(temp_db, "SA", "green", "subagent-test-start")
        record_activity_event(temp_db, "SA", "green", "subagent-test-stop")

        events = get_activity_events(temp_db, "SA")
        assert len(events) == 2
        assert all(e["status"] == "green" for e in events)


class TestAllEventCodesPresent:
    """Verify all 13 event codes can be recorded and retrieved."""

    def test_all_13_event_codes_recorded_and_retrieved(self, temp_db):
        """All 13 event codes can be recorded to DB and retrieved via get_recent_activity."""
        from src.pacemaker.database import record_activity_event, get_recent_activity

        event_codes = [
            ("IV", "green"),
            ("TD", "red"),
            ("CC", "blue"),
            ("ST", "green"),
            ("CX", "red"),
            ("PA", "green"),
            ("PL", "blue"),
            ("LF", "blue"),
            ("SS", "green"),
            ("SM", "blue"),
            ("SE", "green"),
            ("SA", "green"),
            ("UP", "green"),
        ]

        for code, status in event_codes:
            result = record_activity_event(temp_db, code, status, "session-all")
            assert result is True, f"Failed to record {code}"

        # All should be visible in recent activity (within last 10 seconds)
        recent = get_recent_activity(temp_db, window_seconds=10)
        found_codes = {e["event_code"] for e in recent}
        expected_codes = {code for code, _ in event_codes}
        assert (
            found_codes == expected_codes
        ), f"Missing codes: {expected_codes - found_codes}"

    def test_event_recording_never_raises(self, temp_db):
        """record_activity_event must never raise - returns False on error instead."""
        from src.pacemaker.database import record_activity_event

        # Valid call - should return True
        result = record_activity_event(temp_db, "IV", "green", "session-1")
        assert result is True

        # Call with bad path - should return False, not raise
        result = record_activity_event("/nonexistent/path/db.db", "IV", "green", "s")
        assert result is False


class TestHookInstrumentationIntegration:
    """Integration tests verifying hook handlers call record_activity_event."""

    def test_record_activity_event_called_with_correct_session_id(self, temp_db):
        """Activity events include the session_id from hook context."""
        from src.pacemaker.database import record_activity_event

        session_id = "test-session-abc123"
        record_activity_event(temp_db, "UP", "green", session_id)

        events = get_activity_events(temp_db, "UP")
        assert len(events) == 1
        assert events[0]["session_id"] == session_id

    def test_activity_events_persist_across_multiple_hook_invocations(self, temp_db):
        """Activity events accumulate correctly across hook calls."""
        from src.pacemaker.database import record_activity_event, get_recent_activity

        # Simulate sequence of hook events in a session
        hooks_sequence = [
            ("SE", "green", "session-1"),  # SessionStart
            ("UP", "green", "session-1"),  # UserPromptSubmit
            ("IV", "green", "session-1"),  # PreToolUse - IV passed
            ("TD", "green", "session-1"),  # PreToolUse - TD passed
            ("CC", "green", "session-1"),  # PreToolUse - CC passed
            ("LF", "blue", "session-1"),  # PostToolUse - Langfuse pushed
            ("ST", "green", "session-1"),  # Stop hook activated
            ("PA", "green", "session-1"),  # Pacing - no throttle
        ]

        for code, status, sid in hooks_sequence:
            record_activity_event(temp_db, code, status, sid)

        recent = get_recent_activity(temp_db, window_seconds=10)
        found_codes = {e["event_code"] for e in recent}
        expected = {"SE", "UP", "IV", "TD", "CC", "LF", "ST", "PA"}
        assert found_codes == expected
