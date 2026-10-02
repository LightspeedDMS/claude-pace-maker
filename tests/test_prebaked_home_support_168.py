"""Issue #168 -- disk-I/O stalls in tests/test_bootstrap_plugin.py and
tests/test_plugin_lazy_init.py.

Both files bootstrap one real venv ("prebaked home") and used to
``shutil.copytree`` the WHOLE home, venv included, once per test that needs a
mutable copy. Under concurrent I/O that copy plus the pip run stalled past the
per-test caps. ``tests/prebaked_home_support.py`` replaces the copy with a
hard-link tree for the venv (everything else is still a real copy), deletes
trees when the test is done, and bounds every subprocess so a stall fails with
a clear message instead of a pytest kill.

These tests use real files, real hard links and real processes -- nothing is
mocked.
"""

import os
import re
import stat
import subprocess
import time
from pathlib import Path

import pytest

from prebaked_home_support import (
    clone_home,
    freeze_venv,
    remove_tree,
    run_bounded,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def fake_prebaked(tmp_path) -> Path:
    """A miniature prebaked home with the same shape as the real one."""
    home = tmp_path / "prebaked"
    pm = home / ".claude-pace-maker"
    (pm / "venv" / "bin").mkdir(parents=True)
    (pm / "venv" / "lib" / "site-packages" / "pkg").mkdir(parents=True)
    (pm / "venv" / "lib" / "site-packages" / "pkg" / "mod.py").write_text("x = 1\n")
    (pm / "venv" / "bin" / "activate").write_text("# activate\n")
    base_python = tmp_path / "base_python"
    base_python.write_text("#!/bin/sh\nexit 0\n")
    base_python.chmod(0o755)
    (pm / "venv" / "bin" / "python3").symlink_to(base_python)
    (pm / "config.json").write_text('{"enabled": true}\n')
    (pm / ".venv_stamp").write_text("py:abc\n")
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "bin" / "pace-maker").symlink_to("/opt/plugin/pace-maker")
    (home / ".cache" / "pip" / "http").mkdir(parents=True)
    (home / ".cache" / "pip" / "http" / "blob").write_bytes(b"wheel-bytes")
    return home


class TestCloneHomePipCache:
    """The prebake's pip cache (HOME/.cache/pip, ~90 MB) is content-addressed
    and read-only to the tests, so it is hard-linked like the venv."""

    def test_pip_cache_files_are_hard_links(self, fake_prebaked, tmp_path):
        dest = clone_home(fake_prebaked, tmp_path / "clone")
        src = fake_prebaked / ".cache/pip/http/blob"
        dup = dest / ".cache/pip/http/blob"
        assert dup.read_bytes() == b"wheel-bytes"
        assert os.stat(src).st_ino == os.stat(dup).st_ino

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
    def test_freeze_also_protects_the_pip_cache(self, fake_prebaked, tmp_path):
        dest = clone_home(fake_prebaked, tmp_path / "clone")
        freeze_venv(fake_prebaked)
        with pytest.raises(PermissionError):
            (dest / ".cache/pip/http/blob").write_bytes(b"corrupted")

    def test_prebaked_home_without_a_pip_cache_still_clones(
        self, fake_prebaked, tmp_path
    ):
        remove_tree(fake_prebaked / ".cache")
        dest = clone_home(fake_prebaked, tmp_path / "clone")
        freeze_venv(fake_prebaked)
        assert (dest / ".claude-pace-maker" / "venv").is_dir()
        assert not (dest / ".cache").exists()


class TestCloneHome:
    def test_venv_regular_files_are_hard_links(self, fake_prebaked, tmp_path):
        dest = clone_home(fake_prebaked, tmp_path / "clone")
        src = fake_prebaked / ".claude-pace-maker/venv/lib/site-packages/pkg/mod.py"
        dup = dest / ".claude-pace-maker/venv/lib/site-packages/pkg/mod.py"
        assert dup.read_text() == "x = 1\n"
        assert os.stat(src).st_ino == os.stat(dup).st_ino
        assert os.stat(src).st_nlink == 2

    def test_venv_directories_are_real_directories_not_links(
        self, fake_prebaked, tmp_path
    ):
        dest = clone_home(fake_prebaked, tmp_path / "clone")
        clone_venv = dest / ".claude-pace-maker" / "venv"
        assert clone_venv.is_dir() and not clone_venv.is_symlink()

    def test_symlinks_are_preserved_as_symlinks(self, fake_prebaked, tmp_path):
        dest = clone_home(fake_prebaked, tmp_path / "clone")
        py = dest / ".claude-pace-maker" / "venv" / "bin" / "python3"
        assert py.is_symlink()
        assert os.readlink(py) == os.readlink(
            fake_prebaked / ".claude-pace-maker/venv/bin/python3"
        )
        cli = dest / ".local" / "bin" / "pace-maker"
        assert cli.is_symlink() and os.readlink(cli) == "/opt/plugin/pace-maker"

    def test_files_outside_the_venv_are_real_copies(self, fake_prebaked, tmp_path):
        dest = clone_home(fake_prebaked, tmp_path / "clone")
        for name in ("config.json", ".venv_stamp"):
            src = fake_prebaked / ".claude-pace-maker" / name
            dup = dest / ".claude-pace-maker" / name
            assert dup.read_text() == src.read_text()
            assert os.stat(src).st_ino != os.stat(dup).st_ino

    def test_rewriting_cloned_config_leaves_prebaked_untouched(
        self, fake_prebaked, tmp_path
    ):
        dest = clone_home(fake_prebaked, tmp_path / "clone")
        (dest / ".claude-pace-maker" / "config.json").write_text('{"custom": 1}\n')
        (dest / ".claude-pace-maker" / ".venv_stamp").write_text("other\n")
        assert (
            fake_prebaked / ".claude-pace-maker" / "config.json"
        ).read_text() == '{"enabled": true}\n'
        assert (
            fake_prebaked / ".claude-pace-maker" / ".venv_stamp"
        ).read_text() == "py:abc\n"

    def test_unlink_and_rewrite_of_cloned_venv_file_leaves_prebaked_untouched(
        self, fake_prebaked, tmp_path
    ):
        """The sentinel-shim tests replace venv/bin/python3 by unlink+write;
        with hard links that must only affect the clone."""
        dest = clone_home(fake_prebaked, tmp_path / "clone")
        freeze_venv(fake_prebaked)
        activate = dest / ".claude-pace-maker" / "venv" / "bin" / "activate"
        activate.unlink()
        activate.write_text("# shim\n")
        original = fake_prebaked / ".claude-pace-maker/venv/bin/activate"
        assert original.read_text() == "# activate\n"


class TestFreezeVenv:
    def test_regular_files_become_read_only(self, fake_prebaked):
        freeze_venv(fake_prebaked)
        mod = fake_prebaked / ".claude-pace-maker/venv/lib/site-packages/pkg/mod.py"
        assert not (mod.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))

    def test_symlink_targets_are_not_chmodded(self, fake_prebaked, tmp_path):
        """venv/bin/python3 points at the real system interpreter in real
        life; freezing must never touch what a symlink points to."""
        base_python = tmp_path / "base_python"
        before = base_python.stat().st_mode
        freeze_venv(fake_prebaked)
        assert base_python.stat().st_mode == before

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
    def test_in_place_write_through_a_clone_fails_loudly(self, fake_prebaked, tmp_path):
        """A hard-linked file shares its inode with the prebaked tree, so an
        in-place write would silently corrupt every later clone. Frozen files
        make that a PermissionError instead."""
        dest = clone_home(fake_prebaked, tmp_path / "clone")
        freeze_venv(fake_prebaked)
        mod = dest / ".claude-pace-maker/venv/lib/site-packages/pkg/mod.py"
        with pytest.raises(PermissionError):
            mod.write_text("corrupted\n")


class TestRemoveTree:
    def test_removes_a_clone_with_read_only_hard_links(self, fake_prebaked, tmp_path):
        dest = clone_home(fake_prebaked, tmp_path / "clone")
        freeze_venv(fake_prebaked)
        remove_tree(dest)
        assert not dest.exists()
        # The prebaked tree still has its files.
        mod = fake_prebaked / ".claude-pace-maker/venv/lib/site-packages/pkg/mod.py"
        assert mod.read_text() == "x = 1\n"
        assert os.stat(mod).st_nlink == 1

    def test_missing_path_is_not_an_error(self, tmp_path):
        remove_tree(tmp_path / "never-created")


class TestRunBounded:
    def test_returns_completed_process_with_output(self, tmp_path):
        result = run_bounded(
            ["bash", "-c", "cat; echo err >&2; exit 3"],
            timeout=10,
            input_data="hello",
        )
        assert isinstance(result, subprocess.CompletedProcess)
        assert result.returncode == 3
        assert result.stdout == "hello"
        assert result.stderr == "err\n"

    def test_passes_env_and_cwd(self, tmp_path):
        result = run_bounded(
            ["bash", "-c", 'echo "$PROBE:$PWD"'],
            timeout=10,
            env={**os.environ, "PROBE": "yes"},
            cwd=str(tmp_path),
        )
        assert result.stdout.strip() == f"yes:{tmp_path}"

    def test_timeout_fails_with_a_clear_message_and_kills_grandchildren(self, tmp_path):
        """A stalled hook (bash -> python) must not leave the pipe held open
        by an orphaned grandchild, which would make the cleanup hang too."""
        marker = tmp_path / "grandchild.pid"
        script = f"sleep 60 & echo $! > {marker}; echo started; wait"
        started = time.monotonic()
        with pytest.raises(pytest.fail.Exception) as excinfo:
            run_bounded(["bash", "-c", script], timeout=1, label="stalled hook")
        elapsed = time.monotonic() - started
        message = str(excinfo.value)
        assert "stalled hook" in message
        assert "did not finish within 1s" in message
        assert "started" in message  # partial stdout is preserved
        assert elapsed < 10, f"timeout handling hung for {elapsed:.1f}s"
        grandchild = int(marker.read_text())
        gone = False
        for _ in range(60):  # bounded: at most ~3s for init to reap it
            try:
                os.kill(grandchild, 0)
            except ProcessLookupError:
                gone = True
                break
            time.sleep(0.05)
        assert gone, f"grandchild {grandchild} survived the group kill"


class TestRunTestsScriptSync:
    """scripts/run_tests.sh flushes dirty pages after I/O-heavy files so the
    next file does not inherit their writeback stall."""

    SCRIPT = REPO_ROOT / "scripts" / "run_tests.sh"

    def test_script_still_parses(self):
        result = subprocess.run(
            ["bash", "-n", str(self.SCRIPT)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr

    def test_bounded_sync_follows_io_heavy_files(self):
        text = self.SCRIPT.read_text()
        assert "SYNC_TIMEOUT=120" in text
        assert 'timeout "$SYNC_TIMEOUT" sync' in text
        match = re.search(r"IO_HEAVY_PATTERNS=\((.*?)\)", text, re.DOTALL)
        assert match, "run_tests.sh must declare an IO_HEAVY_PATTERNS=( ... ) array"
        for name in ("test_bootstrap_plugin", "test_plugin_lazy_init", "tests/e2e/"):
            assert name in match.group(1), f"{name} must be listed as I/O heavy"

    def test_sync_failure_is_reported_not_swallowed(self):
        text = self.SCRIPT.read_text()
        assert "sync did not finish" in text
