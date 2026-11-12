"""Bug #189: a sticky shared dir (README used 3770) breaks every shared write on
kernels with fs.protected_regular=2 (Ubuntu). pace-maker must name the problem
loudly (status + log on DB open) instead of failing every write silently.

Real directories and files. The /proc value is injected through the module
path constant ``data_dir.PROTECTED_REGULAR_PATH`` pointing at a temp file; the
O_CREAT refusal is injected through the ``opener`` parameter (only the OS
boundary is faked).
"""

import errno
import json
import os
import stat
from pathlib import Path

import pytest

from pacemaker import data_dir
from pacemaker import database as db
from pacemaker import user_commands
from pacemaker.session_registry import db as reg_db
from pacemaker.session_registry import registry

STICKY_GROUP_WRITABLE = 0o3770
WORLD_WRITABLE_STICKY = 0o1777
PLAIN_SETGID = 0o2770
SHARED_FILE_MODE = 0o660
READ_ONLY_FILE_MODE = 0o400


def _config(content):
    cfg = Path(os.path.expanduser("~")) / ".claude-pace-maker" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(content if isinstance(content, str) else json.dumps(content))
    return str(cfg)


@pytest.fixture
def proc_value(tmp_path, monkeypatch):
    """Point the /proc sysctl path at a temp file; returns a setter."""
    f = tmp_path / "protected_regular"
    monkeypatch.setattr(data_dir, "PROTECTED_REGULAR_PATH", str(f))

    def set_value(text):
        if text is None:
            f.unlink(missing_ok=True)
        else:
            f.write_text(text)

    return set_value


def _shared(tmp_path, mode):
    """Configure shared mode on a fresh dir with ``mode``; returns (dir, config)."""
    d = tmp_path / "shared"
    d.mkdir()
    os.chmod(d, mode)
    cfg = _config({"shared_data_dir": str(d)})
    return d, cfg


def _shared_db_file(tmp_path, file_mode=SHARED_FILE_MODE):
    """A shared dir holding an existing usage.db; returns (dir, file, config)."""
    d, cfg = _shared(tmp_path, PLAIN_SETGID)
    f = d / "usage.db"
    f.write_text("")
    f.chmod(file_mode)
    return d, f, cfg


def _refuse_creat(path, flags, *args):
    """Opener standing in for the kernel's protected_regular refusal."""
    if flags & os.O_CREAT:
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), path)
    return os.open(path, flags, *args)


def _refuse_everything(path, flags, *args):
    raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), path)


class TestDetection:
    @pytest.mark.parametrize("value", ["2\n", "3\n", "2"])
    def test_sticky_dir_with_protected_regular_2_or_more_is_reported(
        self, tmp_path, proc_value, value
    ):
        proc_value(value)
        d, _ = _shared(tmp_path, STICKY_GROUP_WRITABLE)
        with pytest.raises(data_dir.SharedDataDirError) as exc:
            data_dir.validate_shared_data_dir()
        msg = str(exc.value)
        assert (
            f"shared_data_dir '{d}' has the sticky bit and fs.protected_regular=" in msg
        )
        assert "other users' files cannot be opened for writing" in msg
        assert "chmod o-t" in msg and f"{PLAIN_SETGID:o}" in msg

    def test_world_writable_sticky_dir_is_reported_already_at_level_1(
        self, tmp_path, proc_value
    ):
        """Kernel rule: level 1 covers world-writable sticky dirs, level 2 adds
        group-writable ones."""
        proc_value("1\n")
        _shared(tmp_path, WORLD_WRITABLE_STICKY)
        with pytest.raises(data_dir.SharedDataDirError, match="protected_regular=1"):
            data_dir.validate_shared_data_dir()

    def test_world_writable_sticky_dir_fine_at_level_0(self, tmp_path, proc_value):
        proc_value("0\n")
        _shared(tmp_path, WORLD_WRITABLE_STICKY)
        data_dir.validate_shared_data_dir()

    def test_negative_sysctl_value_is_unknown(self, proc_value):
        proc_value("-1\n")
        assert data_dir.read_protected_regular() is None

    @pytest.mark.parametrize("value", ["0\n", "1\n"])
    def test_lower_protected_regular_is_fine(self, tmp_path, proc_value, value):
        proc_value(value)
        _shared(tmp_path, STICKY_GROUP_WRITABLE)
        data_dir.validate_shared_data_dir()

    def test_no_sticky_bit_is_fine_even_with_protected_regular_2(
        self, tmp_path, proc_value
    ):
        proc_value("2\n")
        _shared(tmp_path, PLAIN_SETGID)
        data_dir.validate_shared_data_dir()
        assert data_dir.shared_dir_problem() is None

    @pytest.mark.parametrize("content", [None, "", "garbage\n", "-1\n", "\x00"])
    def test_unreadable_or_odd_proc_value_reports_nothing_extra(
        self, tmp_path, proc_value, content
    ):
        proc_value(content)
        _shared(tmp_path, STICKY_GROUP_WRITABLE)
        data_dir.validate_shared_data_dir()  # does not guess
        assert data_dir.shared_dir_problem() is None

    def test_read_protected_regular_parses_and_is_none_when_unknown(
        self, tmp_path, proc_value
    ):
        proc_value("2\n")
        assert data_dir.read_protected_regular() == 2
        proc_value(None)
        assert data_dir.read_protected_regular() is None


class TestSurfacing:
    def test_shared_dir_problem_returns_the_message(self, tmp_path, proc_value):
        proc_value("2\n")
        _shared(tmp_path, STICKY_GROUP_WRITABLE)
        assert "fs.protected_regular=2" in data_dir.shared_dir_problem()

    def test_status_names_the_problem_not_just_an_error_count(
        self, tmp_path, proc_value
    ):
        proc_value("2\n")
        d, cfg = _shared(tmp_path, STICKY_GROUP_WRITABLE)
        out = user_commands.execute_command("status", cfg, db_path=str(d / "usage.db"))
        assert out["success"]
        assert "Shared Data Dir:" in out["message"]
        assert "sticky bit and fs.protected_regular=2" in out["message"]

    def test_db_open_fails_loudly_with_the_named_cause(self, tmp_path, proc_value):
        proc_value("2\n")
        d, _ = _shared(tmp_path, STICKY_GROUP_WRITABLE)
        path = str(d / "usage.db")
        with pytest.raises(data_dir.SharedDataDirError, match="protected_regular"):
            with db.get_db_connection(path):
                pass
        assert db.initialize_database(path) is False  # logged by the caller
        assert db.record_activity_event(path, "IV", "green", "s") is False
        assert not (d / "usage.db").exists()  # nothing half-created

    def test_registry_open_fails_with_the_named_cause(self, tmp_path, proc_value):
        proc_value("2\n")
        d, _ = _shared(tmp_path, STICKY_GROUP_WRITABLE)
        reg_path = str(d / "session_registry.db")
        with pytest.raises(data_dir.SharedDataDirError, match="protected_regular"):
            reg_db.get_connection(reg_path)
        # The best-effort API swallows (and logs) the same error: no exception,
        # and no registry file was created behind the problem's back.
        registry.register_session("s", "/ws", 1, reg_path)
        assert not Path(reg_path).exists()

    def test_fixing_the_mode_makes_it_work_again(self, tmp_path, proc_value):
        proc_value("2\n")
        d, _ = _shared(tmp_path, STICKY_GROUP_WRITABLE)
        os.chmod(d, PLAIN_SETGID)  # chmod o-t
        path = str(d / "usage.db")
        assert db.initialize_database(path) is True
        assert db.record_activity_event(path, "IV", "green", "s") is True
        assert stat.S_IMODE((d / "usage.db").stat().st_mode) == SHARED_FILE_MODE


class TestLegacyNeverTouchesProc:
    def test_legacy_mode_does_not_read_proc(self, tmp_path, monkeypatch):
        calls = []
        real = data_dir.read_protected_regular
        monkeypatch.setattr(
            data_dir,
            "read_protected_regular",
            lambda *a, **k: calls.append(1) or real(*a, **k),
        )
        _config({"enabled": True})
        path = str(tmp_path / "usage.db")
        assert db.initialize_database(path)
        assert db.record_activity_event(path, "IV", "green", "s")
        data_dir.validate_shared_data_dir()
        assert data_dir.shared_dir_problem(opener=_refuse_everything) is None
        assert calls == []

    def test_sticky_legacy_dir_is_irrelevant(self, tmp_path, proc_value):
        proc_value("2\n")
        _config({"enabled": True})
        d = tmp_path / "sticky-but-not-shared"
        d.mkdir()
        os.chmod(d, STICKY_GROUP_WRITABLE)
        assert db.initialize_database(str(d / "usage.db")) is True


class TestWriteProbe:
    """Item 3: the generic symptom (a file we may write but cannot open with
    O_CREAT, whatever the cause) is named by shared_dir_problem, which is what
    ``pace-maker status`` prints. The probe only runs for existing regular
    files, so it never creates anything."""

    def test_probe_names_a_creat_refusal(self, tmp_path):
        _, f, _ = _shared_db_file(tmp_path)
        msg = data_dir.write_probe_problem(str(f), opener=_refuse_creat)
        assert msg is not None
        assert "cannot be opened for writing" in msg and "fs.protected_regular" in msg

    def test_probe_is_quiet_when_open_works_and_never_creates(self, tmp_path):
        d, f, _ = _shared_db_file(tmp_path)
        assert data_dir.write_probe_problem(str(f)) is None
        assert data_dir.write_probe_problem(str(d / "absent.db")) is None
        assert not (d / "absent.db").exists()

    def test_probe_ignores_files_we_cannot_write_anyway(self, tmp_path):
        _, f, _ = _shared_db_file(tmp_path, READ_ONLY_FILE_MODE)
        assert data_dir.write_probe_problem(str(f), opener=_refuse_everything) is None

    def test_probe_does_not_guess_on_other_os_errors(self, tmp_path):
        _, f, _ = _shared_db_file(tmp_path)

        def io_error(path, flags, *args):
            raise OSError(errno.EIO, os.strerror(errno.EIO), path)

        assert data_dir.write_probe_problem(str(f), opener=io_error) is None

    @pytest.mark.parametrize("umask", [0o022, 0o077])
    def test_db_deleted_after_the_check_is_not_recreated_with_a_narrow_mode(
        self, tmp_path, umask
    ):
        """A DB removed between the probe's lstat and its O_CREAT open is
        re-created by that open; it must come out 0660 (group rw), never 0640,
        or the other user would be locked out."""
        _, f, _ = _shared_db_file(tmp_path)

        def delete_then_open(path, flags, *args):
            os.unlink(path)  # the race: file vanishes after lstat/access
            return os.open(path, flags, *args)

        old = os.umask(umask)
        try:
            assert data_dir.write_probe_problem(str(f), opener=delete_then_open) is None
        finally:
            os.umask(old)
        assert stat.S_IMODE(f.stat().st_mode) == SHARED_FILE_MODE

    def test_probe_skips_non_regular_files(self, tmp_path):
        d, _, _ = _shared_db_file(tmp_path)
        target = d / "elsewhere"
        target.write_text("")
        link = d / "session_registry.db"
        link.symlink_to(target)
        assert data_dir.write_probe_problem(str(link), opener=_refuse_creat) is None

    def test_shared_dir_problem_reports_a_real_probe_failure(self, tmp_path):
        _, f, _ = _shared_db_file(tmp_path)
        msg = data_dir.shared_dir_problem(opener=_refuse_creat)
        assert msg is not None
        assert str(f) in msg and "cannot be opened for writing" in msg

    def test_shared_dir_problem_quiet_when_probe_passes(self, tmp_path):
        _shared_db_file(tmp_path)
        assert data_dir.shared_dir_problem() is None

    def test_status_succeeds_and_shows_no_problem_when_probe_passes(self, tmp_path):
        _, f, cfg = _shared_db_file(tmp_path)
        out = user_commands.execute_command("status", cfg, db_path=str(f))
        assert out["success"]
        assert "cannot be opened for writing" not in out["message"]
