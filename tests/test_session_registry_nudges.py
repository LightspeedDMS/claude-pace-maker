"""
Unit tests for session_registry.nudges module.

Tests:
- build_start_banner: returns empty string for empty sibling list
- build_start_banner: returns non-empty str naming sibling session_id
- build_start_banner: includes workspace_root from sibling
- build_start_banner: includes pid from sibling
- build_start_banner: includes start_time from sibling
- build_start_banner: includes all session_ids when multiple siblings
- build_start_banner: return type is str
- build_start_banner: output is UTF-8 encodable
- build_periodic_reminder: returns empty string for empty sibling list
- build_periodic_reminder: returns non-empty str naming sibling session_id
- build_periodic_reminder: includes workspace_root from sibling
- build_periodic_reminder: includes all session_ids when multiple siblings
- build_periodic_reminder: return type is str
- build_periodic_reminder: output is UTF-8 encodable
- build_danger_bash_warning: returns empty string for empty sibling list
- build_danger_bash_warning: returns non-empty str naming sibling session_id
- build_danger_bash_warning: includes the bash command in output
- build_danger_bash_warning: includes all session_ids when multiple siblings
- build_danger_bash_warning: return type is str
- build_danger_bash_warning: output is UTF-8 encodable
"""

import sys
import time

# ── Module paths for cache-busting ───────────────────────────────────────────
MOD_NUDGES = "pacemaker.session_registry.nudges"
MOD_PACKAGE = "pacemaker.session_registry"

# ── Test data constants ───────────────────────────────────────────────────────
SESSION_A = "session-aaa-111"
SESSION_B = "session-bbb-222"
WORKSPACE_X = "/workspace/projectX"
PID_A = 9001
PID_B = 9002
BASH_CMD_DESTRUCTIVE = "git checkout -- src/foo.py"

# ── Time constants ────────────────────────────────────────────────────────────
_START_TIME_OFFSET = 300  # 5 minutes ago
# Known fixed start_time for deterministic assertion
KNOWN_START_TIME = 1_700_000_000.0


def _fresh_nudges():
    """Return a freshly imported nudges module."""
    sys.modules.pop(MOD_NUDGES, None)
    sys.modules.pop(MOD_PACKAGE, None)
    import pacemaker.session_registry.nudges as nudges

    return nudges


def _make_sibling(
    session_id=SESSION_A,
    workspace_root=WORKSPACE_X,
    pid=PID_A,
    start_time=None,
):
    """Return a sibling dict as returned by list_siblings()."""
    now = time.time()
    return {
        "session_id": session_id,
        "workspace_root": workspace_root,
        "pid": pid,
        "start_time": (
            start_time if start_time is not None else (now - _START_TIME_OFFSET)
        ),
        "last_seen": now,
    }


class TestBuildStartBanner:
    """Tests for build_start_banner(siblings)."""

    def test_empty_siblings_returns_empty_string(self):
        """build_start_banner returns empty string when sibling list is empty."""
        nudges = _fresh_nudges()
        result = nudges.build_start_banner([])
        assert result == ""

    def test_return_type_is_str(self):
        """build_start_banner always returns a str (non-empty case)."""
        nudges = _fresh_nudges()
        result = nudges.build_start_banner([_make_sibling()])
        assert isinstance(result, str)

    def test_single_sibling_includes_session_id(self):
        """build_start_banner includes the sibling's session_id in output."""
        nudges = _fresh_nudges()
        result = nudges.build_start_banner([_make_sibling(session_id=SESSION_A)])
        assert result != ""
        assert SESSION_A in result

    def test_single_sibling_includes_workspace_root(self):
        """build_start_banner includes the sibling's workspace_root in output."""
        nudges = _fresh_nudges()
        result = nudges.build_start_banner([_make_sibling(workspace_root=WORKSPACE_X)])
        assert WORKSPACE_X in result

    def test_single_sibling_includes_pid(self):
        """build_start_banner includes the sibling's pid in output."""
        nudges = _fresh_nudges()
        result = nudges.build_start_banner([_make_sibling(pid=PID_A)])
        assert str(PID_A) in result

    def test_single_sibling_includes_start_time(self):
        """build_start_banner includes the sibling's start_time (or its rendered form) in output."""
        nudges = _fresh_nudges()
        sibling = _make_sibling(start_time=KNOWN_START_TIME)
        result = nudges.build_start_banner([sibling])
        # The banner must reference the start_time value in some rendered form.
        # Accept either the raw integer repr or a human-readable datetime fragment (year 2023).
        assert str(int(KNOWN_START_TIME)) in result or "2023" in result

    def test_multiple_siblings_includes_all_session_ids(self):
        """build_start_banner includes all sibling session_ids when multiple siblings."""
        nudges = _fresh_nudges()
        sibling_a = _make_sibling(session_id=SESSION_A)
        sibling_b = _make_sibling(session_id=SESSION_B, pid=PID_B)
        result = nudges.build_start_banner([sibling_a, sibling_b])
        assert SESSION_A in result
        assert SESSION_B in result

    def test_start_banner_is_utf8_encodable(self):
        """Output must round-trip through UTF-8 (real hook path writes to stdout)."""
        nudges = _fresh_nudges()
        result = nudges.build_start_banner([_make_sibling()])
        # Must not raise UnicodeEncodeError
        encoded = result.encode("utf-8")
        assert len(encoded) > 0


class TestBuildPeriodicReminder:
    """Tests for build_periodic_reminder(siblings)."""

    def test_empty_siblings_returns_empty_string(self):
        """build_periodic_reminder returns empty string when sibling list is empty."""
        nudges = _fresh_nudges()
        result = nudges.build_periodic_reminder([])
        assert result == ""

    def test_return_type_is_str(self):
        """build_periodic_reminder always returns a str (non-empty case)."""
        nudges = _fresh_nudges()
        result = nudges.build_periodic_reminder([_make_sibling()])
        assert isinstance(result, str)

    def test_single_sibling_includes_session_id(self):
        """build_periodic_reminder includes the sibling's session_id in output."""
        nudges = _fresh_nudges()
        result = nudges.build_periodic_reminder([_make_sibling(session_id=SESSION_A)])
        assert result != ""
        assert SESSION_A in result

    def test_single_sibling_includes_workspace_root(self):
        """build_periodic_reminder includes the sibling's workspace_root in output."""
        nudges = _fresh_nudges()
        result = nudges.build_periodic_reminder(
            [_make_sibling(workspace_root=WORKSPACE_X)]
        )
        assert WORKSPACE_X in result

    def test_multiple_siblings_includes_all_session_ids(self):
        """build_periodic_reminder includes all sibling session_ids."""
        nudges = _fresh_nudges()
        sibling_a = _make_sibling(session_id=SESSION_A)
        sibling_b = _make_sibling(session_id=SESSION_B, pid=PID_B)
        result = nudges.build_periodic_reminder([sibling_a, sibling_b])
        assert SESSION_A in result
        assert SESSION_B in result

    def test_periodic_reminder_is_utf8_encodable(self):
        """Output must round-trip through UTF-8 (AC3 hook injects into stdout)."""
        nudges = _fresh_nudges()
        result = nudges.build_periodic_reminder([_make_sibling()])
        # Must not raise UnicodeEncodeError
        encoded = result.encode("utf-8")
        assert len(encoded) > 0


class TestBuildDangerBashWarning:
    """Tests for build_danger_bash_warning(siblings, command)."""

    def test_empty_siblings_returns_empty_string(self):
        """build_danger_bash_warning returns empty string when sibling list is empty."""
        nudges = _fresh_nudges()
        result = nudges.build_danger_bash_warning([], BASH_CMD_DESTRUCTIVE)
        assert result == ""

    def test_return_type_is_str(self):
        """build_danger_bash_warning always returns a str (non-empty case)."""
        nudges = _fresh_nudges()
        result = nudges.build_danger_bash_warning(
            [_make_sibling()], BASH_CMD_DESTRUCTIVE
        )
        assert isinstance(result, str)

    def test_single_sibling_includes_session_id(self):
        """build_danger_bash_warning includes sibling session_id in output."""
        nudges = _fresh_nudges()
        result = nudges.build_danger_bash_warning(
            [_make_sibling(session_id=SESSION_A)], BASH_CMD_DESTRUCTIVE
        )
        assert result != ""
        assert SESSION_A in result

    def test_includes_bash_command(self):
        """build_danger_bash_warning includes the bash command in output."""
        nudges = _fresh_nudges()
        result = nudges.build_danger_bash_warning(
            [_make_sibling()], BASH_CMD_DESTRUCTIVE
        )
        assert BASH_CMD_DESTRUCTIVE in result

    def test_multiple_siblings_includes_all_session_ids(self):
        """build_danger_bash_warning includes all sibling session_ids."""
        nudges = _fresh_nudges()
        sibling_a = _make_sibling(session_id=SESSION_A)
        sibling_b = _make_sibling(session_id=SESSION_B, pid=PID_B)
        result = nudges.build_danger_bash_warning(
            [sibling_a, sibling_b], BASH_CMD_DESTRUCTIVE
        )
        assert SESSION_A in result
        assert SESSION_B in result

    def test_danger_bash_warning_is_utf8_encodable(self):
        """Output must round-trip through UTF-8 (AC5 injects into Stage 2 context)."""
        nudges = _fresh_nudges()
        result = nudges.build_danger_bash_warning(
            [_make_sibling()], BASH_CMD_DESTRUCTIVE
        )
        # Must not raise UnicodeEncodeError
        encoded = result.encode("utf-8")
        assert len(encoded) > 0
