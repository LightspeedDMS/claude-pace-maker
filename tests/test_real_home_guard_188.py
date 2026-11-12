"""Bug #188 permanent guard: the suite must never create the real user's
``~/.claude-pace-maker``.

``real_home_guard_support`` is the small stdlib-only helper behind the
session-finish check in ``conftest.py``. It only judges a run that STARTED with
no such directory: a machine with a live pace-maker install legitimately has
its own hooks writing there, which cannot be told apart from a test leak.
"""

import os
import pwd

import pytest

import conftest
import real_home_guard_support as guard

EXTRA_FILES_BEYOND_LIMIT = 25
SESSION_FAILED_EXIT_STATUS = 1
# Lines in a leak message besides the listed files: the header and the
# "... and N more" line (newlines separate them, so count("\n") is lines - 1).
MESSAGE_FRAME_LINES = 2
# Under a uid with no passwd entry there is no real home: tests that look it up skip.
needs_passwd_entry = pytest.mark.skipif(
    guard.real_pace_maker_dir() is None, reason="current uid has no passwd entry"
)


class _Session:
    """Minimal stand-in for pytest's Session: only the attribute we set."""

    def __init__(self, exitstatus=0):
        self.exitstatus = exitstatus


class TestSnapshot:
    def test_absent_dir_is_recorded_as_absent(self, tmp_path):
        assert guard.snapshot(str(tmp_path / ".claude-pace-maker")).absent_at_start

    def test_existing_dir_is_recorded_as_present(self, tmp_path):
        d = tmp_path / ".claude-pace-maker"
        d.mkdir()
        assert not guard.snapshot(str(d)).absent_at_start

    def test_dangling_symlink_counts_as_present(self, tmp_path):
        d = tmp_path / ".claude-pace-maker"
        d.symlink_to(tmp_path / "nowhere")
        assert not guard.snapshot(str(d)).absent_at_start

    def test_snapshot_never_creates_anything(self, tmp_path):
        base = tmp_path / "base"  # tmp_path itself already holds fixture dirs
        base.mkdir()
        d = base / ".claude-pace-maker"
        guard.snapshot(str(d))
        assert not d.exists()
        assert list(base.iterdir()) == []


class TestLeakMessage:
    def test_dir_appearing_during_the_run_is_a_leak(self, tmp_path):
        d = tmp_path / ".claude-pace-maker"
        before = guard.snapshot(str(d))
        d.mkdir()
        (d / "state.json").write_text("{}")
        message = guard.leak_message(before)
        assert message is not None
        assert str(d) in message
        assert "state.json" in message

    def test_quiet_when_nothing_appeared(self, tmp_path):
        d = tmp_path / ".claude-pace-maker"
        assert guard.leak_message(guard.snapshot(str(d))) is None

    def test_quiet_when_the_dir_pre_existed(self, tmp_path):
        """A live install's own hooks write here: not judged."""
        d = tmp_path / ".claude-pace-maker"
        d.mkdir()
        before = guard.snapshot(str(d))
        (d / "pace-maker.log").write_text("live hook output")
        assert guard.leak_message(before) is None

    def test_lists_a_bounded_number_of_files(self, tmp_path):
        d = tmp_path / ".claude-pace-maker"
        before = guard.snapshot(str(d))
        d.mkdir()
        for i in range(guard.MAX_LISTED_FILES + EXTRA_FILES_BEYOND_LIMIT):
            (d / f"f{i}").write_text("x")
        message = guard.leak_message(before)
        assert message is not None
        assert message.count("\n") + 1 <= guard.MAX_LISTED_FILES + MESSAGE_FRAME_LINES


class TestRealHomeResolution:
    @needs_passwd_entry
    def test_dir_comes_from_the_passwd_entry_not_the_env_home(self, monkeypatch):
        monkeypatch.setenv("HOME", "/definitely/not/real")
        expected = os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".claude-pace-maker")
        assert guard.real_pace_maker_dir() == expected


NO_ENTRY_UID = 4242


def _lookup_without_entry(uid):
    raise KeyError(f"getpwuid(): uid not found: {uid}")


def _lookup_that_fails_otherwise(uid):
    raise OSError("passwd database unreadable")


class TestNoPasswdEntry:
    """CI containers often run under a uid with no passwd entry: the guard must
    disable itself (visibly) instead of stopping the whole suite from starting."""

    def test_missing_entry_means_no_directory_to_guard(self):
        assert guard.real_pace_maker_dir(lookup=_lookup_without_entry) is None

    def test_other_lookup_errors_still_propagate(self):
        with pytest.raises(OSError):
            guard.real_pace_maker_dir(lookup=_lookup_that_fails_otherwise)

    def test_take_snapshot_returns_none_for_a_missing_entry(self):
        assert (
            guard.take_snapshot(lookup=_lookup_without_entry, uid=NO_ENTRY_UID) is None
        )

    def test_the_notice_names_the_uid_in_one_line(self):
        notice = guard.disabled_notice(NO_ENTRY_UID)
        assert notice == (
            f"real-home guard disabled: uid {NO_ENTRY_UID} has no passwd entry"
        )
        assert "\n" not in notice

    @needs_passwd_entry
    def test_take_snapshot_with_an_entry_returns_a_snapshot(self):
        assert isinstance(guard.take_snapshot(), guard.Snapshot)

    def test_a_disabled_guard_never_fails_the_session(self):
        session = _Session(exitstatus=0)
        conftest._fail_on_real_home_leak(session, None)
        assert session.exitstatus == 0

    def test_leak_message_for_a_disabled_guard_is_none(self):
        assert guard.leak_message(None) is None


class _Reporter:
    """Records lines written through pytest's terminal reporter API."""

    def __init__(self):
        self.lines = []

    def write_line(self, line):
        self.lines.append(line)


class TestConftestWiring:
    """pytest's fd-level capture is already active while conftest loads, so a
    print there is swallowed on a passing run: the notice is written from the
    terminal summary hook, which shows even with -q."""

    def test_disabled_guard_writes_the_notice_once_in_the_summary(self):
        reporter = _Reporter()
        conftest._write_guard_notice(reporter, None, NO_ENTRY_UID)
        assert reporter.lines == [guard.disabled_notice(NO_ENTRY_UID)]

    def test_enabled_guard_writes_nothing(self, tmp_path):
        reporter = _Reporter()
        before = guard.snapshot(str(tmp_path / ".claude-pace-maker"))
        conftest._write_guard_notice(reporter, before, NO_ENTRY_UID)
        assert reporter.lines == []

    def test_the_summary_hook_is_wired_to_the_module_snapshot(self):
        reporter = _Reporter()
        conftest.pytest_terminal_summary(reporter)
        if conftest._REAL_HOME_SNAPSHOT is None:
            assert reporter.lines == [guard.disabled_notice(os.getuid())]
        else:
            assert reporter.lines == []

    @needs_passwd_entry
    def test_the_conftest_captured_a_snapshot_before_the_home_override(self):
        assert isinstance(conftest._REAL_HOME_SNAPSHOT, guard.Snapshot)
        assert conftest._REAL_HOME_SNAPSHOT.path == guard.real_pace_maker_dir()

    def test_a_leak_turns_the_session_red_and_names_the_files(self, tmp_path, capsys):
        d = tmp_path / ".claude-pace-maker"
        before = guard.snapshot(str(d))
        d.mkdir()
        (d / "state.json.lock").write_text("")
        session = _Session()
        conftest._fail_on_real_home_leak(session, before)
        assert session.exitstatus == SESSION_FAILED_EXIT_STATUS
        assert "state.json.lock" in capsys.readouterr().err

    def test_no_leak_leaves_the_exit_status_alone(self, tmp_path):
        before = guard.snapshot(str(tmp_path / ".claude-pace-maker"))
        session = _Session(exitstatus=0)
        conftest._fail_on_real_home_leak(session, before)
        assert session.exitstatus == 0
