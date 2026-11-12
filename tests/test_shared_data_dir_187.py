"""Story #187: shared monitoring data directory resolver (opt-in).

Real filesystem, real config.json, no mocks. HOME is redirected by the
autouse conftest fixture; each test writes its own per-user config.json.
"""

import json
import os
import pwd
import stat
from pathlib import Path

import pytest

from pacemaker import data_dir


def _write_config(content):
    cfg = Path(os.path.expanduser("~")) / ".claude-pace-maker" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(content) if not isinstance(content, str) else content)
    return cfg


class TestLegacyModeUnchanged:
    """AC6: with the setting absent, everything resolves exactly as before."""

    def test_no_config_file_uses_per_user_dir(self):
        home = Path(os.path.expanduser("~"))
        (home / ".claude-pace-maker" / "config.json").unlink(missing_ok=True)
        assert data_dir.shared_data_dir() is None
        assert data_dir.monitoring_data_dir() == str(home / ".claude-pace-maker")
        assert data_dir.monitoring_db_path("usage.db") == str(
            home / ".claude-pace-maker" / "usage.db"
        )

    @pytest.mark.parametrize(
        "content", [{}, {"enabled": True}, {"shared_data_dir": None}]
    )
    def test_absent_or_null_setting_is_legacy(self, content):
        _write_config(content)
        assert data_dir.shared_data_dir() is None
        assert not data_dir.shared_mode_enabled()

    def test_unreadable_config_is_legacy_not_a_crash(self):
        _write_config("{not json")
        assert data_dir.shared_data_dir() is None

    def test_pathologically_nested_config_is_legacy_not_a_crash(self):
        """Older configs must never break import (RecursionError from json)."""
        _write_config("[" * 100000 + "]" * 100000)
        assert data_dir.shared_data_dir() is None

    def test_unknown_extra_keys_do_not_matter(self):
        _write_config({"future_key": 1, "enabled": False})
        assert data_dir.shared_data_dir() is None

    def test_prepare_db_file_is_noop_for_legacy_paths(self, tmp_path):
        db = tmp_path / "usage.db"
        data_dir.prepare_db_file(str(db))
        assert not db.exists()  # legacy: SQLite creates it, we do nothing


class TestSharedModeResolution:
    def test_shared_setting_redirects_monitoring_paths(self, tmp_path):
        shared = tmp_path / "shared"
        shared.mkdir()
        _write_config({"shared_data_dir": str(shared)})
        assert data_dir.shared_mode_enabled()
        assert data_dir.monitoring_data_dir() == str(shared)
        assert data_dir.monitoring_db_path("usage.db") == str(shared / "usage.db")
        assert data_dir.monitoring_db_path("session_registry.db") == str(
            shared / "session_registry.db"
        )

    def test_per_user_dir_never_moves(self, tmp_path):
        _write_config({"shared_data_dir": str(tmp_path)})
        assert data_dir.per_user_dir() == str(
            Path(os.path.expanduser("~")) / ".claude-pace-maker"
        )

    def test_secrets_db_is_per_user_in_shared_mode(self, tmp_path):
        _write_config({"shared_data_dir": str(tmp_path)})
        assert data_dir.per_user_secrets_db_path() == str(
            Path(os.path.expanduser("~")) / ".claude-pace-maker" / "secrets.db"
        )

    def test_tilde_is_expanded(self, tmp_path):
        _write_config({"shared_data_dir": "~/somewhere"})
        assert data_dir.shared_data_dir() == str(
            Path(os.path.expanduser("~")) / "somewhere"
        )

    @pytest.mark.parametrize("bad", ["", "   ", 5, ["x"], True])
    def test_invalid_setting_type_fails_loudly(self, bad):
        _write_config({"shared_data_dir": bad})
        with pytest.raises(data_dir.SharedDataDirError, match="shared_data_dir"):
            data_dir.shared_data_dir()

    def test_relative_path_rejected(self):
        _write_config({"shared_data_dir": "relative/dir"})
        with pytest.raises(data_dir.SharedDataDirError, match="absolute"):
            data_dir.shared_data_dir()

    def test_per_user_dir_cannot_be_the_shared_dir(self):
        """Would put code/config/secrets next to 0660 data and chmod them."""
        _write_config({"shared_data_dir": data_dir.per_user_dir()})
        with pytest.raises(data_dir.SharedDataDirError, match="per-user"):
            data_dir.shared_data_dir()

    def test_is_shared_path_only_for_files_directly_in_shared_dir(self, tmp_path):
        shared = tmp_path / "shared"
        shared.mkdir()
        _write_config({"shared_data_dir": str(shared)})
        assert data_dir.is_shared_path(str(shared / "usage.db"))
        assert not data_dir.is_shared_path(str(tmp_path / "other" / "usage.db"))
        assert not data_dir.is_shared_path(str(shared / "sub" / "usage.db"))


class TestValidation:
    def test_missing_dir_fails_with_story_message(self, tmp_path):
        missing = tmp_path / "nope"
        _write_config({"shared_data_dir": str(missing)})
        with pytest.raises(data_dir.SharedDataDirError) as exc:
            data_dir.validate_shared_data_dir()
        assert f"shared_data_dir '{missing}' does not exist" in str(exc.value)

    def test_file_instead_of_dir_fails(self, tmp_path):
        f = tmp_path / "afile"
        f.write_text("x")
        _write_config({"shared_data_dir": str(f)})
        with pytest.raises(data_dir.SharedDataDirError, match="not a directory"):
            data_dir.validate_shared_data_dir()

    def test_unwritable_dir_fails_with_story_message(self, tmp_path):
        if os.geteuid() == 0:
            pytest.skip("root bypasses permission checks")
        d = tmp_path / "ro"
        d.mkdir()
        d.chmod(0o555)
        try:
            _write_config({"shared_data_dir": str(d)})
            user = pwd.getpwuid(os.getuid()).pw_name
            with pytest.raises(data_dir.SharedDataDirError) as exc:
                data_dir.validate_shared_data_dir()
            assert (
                f"shared_data_dir '{d}' is not writable by {user} "
                "(need group rw, setgid dir)"
            ) in str(exc.value)
        finally:
            d.chmod(0o755)

    def test_valid_dir_passes_and_legacy_validation_is_noop(self, tmp_path):
        data_dir.validate_shared_data_dir()  # absent setting: nothing to validate
        _write_config({"shared_data_dir": str(tmp_path)})
        data_dir.validate_shared_data_dir()

    def test_status_problem_reports_error_text_or_none(self, tmp_path):
        assert data_dir.shared_dir_problem() is None
        _write_config({"shared_data_dir": str(tmp_path / "nope")})
        assert "does not exist" in data_dir.shared_dir_problem()


class TestSharedFilePermissions:
    def test_prepare_creates_db_0660_regardless_of_umask(self, tmp_path):
        shared = tmp_path / "shared"
        shared.mkdir()
        _write_config({"shared_data_dir": str(shared)})
        db = shared / "usage.db"
        old = os.umask(0o077)
        try:
            data_dir.prepare_db_file(str(db))
        finally:
            os.umask(old)
        assert stat.S_IMODE(db.stat().st_mode) == 0o660

    def test_prepare_repairs_narrow_mode_on_owned_file(self, tmp_path):
        shared = tmp_path / "shared"
        shared.mkdir()
        _write_config({"shared_data_dir": str(shared)})
        db = shared / "usage.db"
        db.write_text("")
        db.chmod(0o600)
        data_dir.prepare_db_file(str(db))
        assert stat.S_IMODE(db.stat().st_mode) == 0o660

    def test_prepare_raises_for_missing_shared_dir(self, tmp_path):
        _write_config({"shared_data_dir": str(tmp_path / "nope")})
        with pytest.raises(data_dir.SharedDataDirError):
            data_dir.prepare_db_file(str(tmp_path / "nope" / "usage.db"))


class TestOsUser:
    def test_os_user_is_pwd_name(self):
        assert data_dir.os_user() == pwd.getpwuid(os.getuid()).pw_name


def _constants_in_subprocess(home, expr):
    """Evaluate ``expr`` against pacemaker.constants in a fresh process (the
    constants are frozen at import, so only a fresh process proves them)."""
    import subprocess
    import sys

    src = str(Path(__file__).resolve().parent.parent / "src")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PACEMAKER_")}
    env["HOME"] = str(home)
    env["PYTHONPATH"] = src
    out = subprocess.run(
        [sys.executable, "-c", f"from pacemaker import constants as c; print({expr})"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return out


class TestConstantsWiring:
    def test_legacy_constants_equal_historical_values(self, tmp_path):
        home = tmp_path / "h"
        (home / ".claude-pace-maker").mkdir(parents=True)
        out = _constants_in_subprocess(home, "c.DEFAULT_DB_PATH")
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == str(home / ".claude-pace-maker" / "usage.db")

    def test_legacy_per_user_paths_unchanged(self, tmp_path):
        home = tmp_path / "h"
        (home / ".claude-pace-maker").mkdir(parents=True)
        for const, name in [
            ("DEFAULT_CONFIG_PATH", "config.json"),
            ("DEFAULT_STATE_PATH", "state.json"),
            ("DEFAULT_LOG_DIR", ""),
        ]:
            out = _constants_in_subprocess(home, f"c.{const}")
            expected = home / ".claude-pace-maker" / name
            assert out.stdout.strip() == str(expected).rstrip("/"), const

    def test_shared_setting_moves_only_usage_db_constant(self, tmp_path):
        home = tmp_path / "h"
        shared = tmp_path / "shared"
        shared.mkdir()
        cfgdir = home / ".claude-pace-maker"
        cfgdir.mkdir(parents=True)
        (cfgdir / "config.json").write_text(
            json.dumps({"shared_data_dir": str(shared)})
        )
        out = _constants_in_subprocess(home, "c.DEFAULT_DB_PATH")
        assert out.stdout.strip() == str(shared / "usage.db"), out.stderr
        out = _constants_in_subprocess(home, "c.DEFAULT_CONFIG_PATH")
        assert out.stdout.strip() == str(cfgdir / "config.json")
        out = _constants_in_subprocess(home, "c.DEFAULT_STATE_PATH")
        assert out.stdout.strip() == str(cfgdir / "state.json")


class TestSharedUmaskScope:
    """SQLite creates -wal/-shm lazily under the process umask; with 022 a second
    OS user can briefly see them unwritable ("readonly database"). In shared mode
    the umask is 007 only while a connection is OPENED (narrow scope; see
    test_shared_hardening_187.TestNarrowUmask), and only then."""

    def _shared(self, tmp_path):
        shared = tmp_path / "shared"
        shared.mkdir()
        _write_config({"shared_data_dir": str(shared)})
        return str(shared / "usage.db")

    def test_umask_is_group_friendly_inside_and_restored_after(self, tmp_path):
        db = self._shared(tmp_path)
        old = os.umask(0o022)
        try:
            with data_dir.shared_umask(db):
                inside = os.umask(0o007)  # reads current, keeps 007
            after = os.umask(0o022)
        finally:
            os.umask(old)
        assert inside == 0o007
        assert after == 0o022

    def test_nested_scopes_restore_the_original_umask(self, tmp_path):
        db = self._shared(tmp_path)
        old = os.umask(0o027)
        try:
            with data_dir.shared_umask(db):
                with data_dir.shared_umask(db):
                    pass
                assert os.umask(0o007) == 0o007
            assert os.umask(0o027) == 0o027
        finally:
            os.umask(old)

    def test_restored_even_when_body_raises(self, tmp_path):
        db = self._shared(tmp_path)
        old = os.umask(0o022)
        try:
            with pytest.raises(ValueError):
                with data_dir.shared_umask(db):
                    raise ValueError("boom")
            assert os.umask(0o022) == 0o022
        finally:
            os.umask(old)

    def test_legacy_paths_never_touch_the_umask(self, tmp_path):
        old = os.umask(0o022)
        try:
            with data_dir.shared_umask(str(tmp_path / "usage.db")):
                assert os.umask(0o022) == 0o022
        finally:
            os.umask(old)
