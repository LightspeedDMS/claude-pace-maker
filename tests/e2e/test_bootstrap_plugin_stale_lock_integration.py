"""
End-to-end integration test for stale venv-install-lock recovery via a
REAL `bootstrap-plugin.sh --full` run (issue #144 code-review follow-up
#6).

tests/test_bootstrap_plugin.py::TestStaleVenvLockRecovery covers the same
scenario cheaply by calling `_with_venv_install_lock` directly (the real
lock-clearing code path bootstrap_full's slow branch reaches), but that
means it never actually exercises `.bootstrap_ok` getting created via the
public `bootstrap_full` entrypoint. Restoring that end-to-end assertion
cheaply is not possible: `_venv_needs_recreate` forces a full venv
rebuild (real `pip install`) the instant the on-disk stamp doesn't match
the current run's expected value, and reaching the SLOW/locked branch of
`_ensure_venv_and_deps` at all requires exactly that stamp mismatch --
there is no state where the lock gets touched but a full reinstall is
avoided. So this real, ~24-40s integration test lives here instead,
under tests/e2e/'s generous budget, preserving the original pre-#144
test body verbatim (see git history for
test_symlink_with_dead_pid_is_cleared_and_bootstrap_succeeds).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BOOTSTRAP_SH = REPO_ROOT / "scripts" / "bootstrap-plugin.sh"


def run_bootstrap(home, mode="--light"):
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PLUGIN_ROOT"] = str(REPO_ROOT)
    return subprocess.run(
        ["bash", str(BOOTSTRAP_SH), mode],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )


class TestStaleVenvLockRecoveryIntegration:
    @pytest.mark.timeout(90)
    def test_symlink_with_dead_pid_is_cleared_and_bootstrap_succeeds(self, tmp_path):
        """A symlink lock left by a crashed bootstrap (target string is a
        dead pid) must be auto-cleared so the next invocation proceeds
        without waiting on the lock timeout, all the way through to a
        completed, real `bootstrap_full` run."""
        home = tmp_path / "home"
        home.mkdir()
        pacemaker_dir = home / ".claude-pace-maker"
        pacemaker_dir.mkdir()
        stale_lock = pacemaker_dir / ".venv.lock.link"

        proc = subprocess.run(
            [sys.executable, "-c", "import os; print(os.getpid())"],
            capture_output=True,
            text=True,
        )
        dead_pid = proc.stdout.strip()
        assert dead_pid.isdigit()
        os.symlink(dead_pid, str(stale_lock))

        result = run_bootstrap(home, "--full")
        assert result.returncode == 0, result.stderr
        assert (
            not stale_lock.is_symlink()
        ), "stale lock symlink should be removed after bootstrap"
        assert (pacemaker_dir / ".bootstrap_ok").exists()
