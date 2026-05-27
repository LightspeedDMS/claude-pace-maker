"""
Tests for scripts/bootstrap-plugin.sh (plugin bootstrap and managed venv).
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP_SH = REPO_ROOT / "scripts" / "bootstrap-plugin.sh"


def run_bootstrap(home, mode="--light", extra_env=None):
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PLUGIN_ROOT"] = str(REPO_ROOT)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(BOOTSTRAP_SH), mode],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )


class TestBootstrapLight:
    def test_light_creates_symlinks_without_bootstrap_ok(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        result = run_bootstrap(home, "--light")
        assert result.returncode == 0
        assert (home / ".local" / "bin" / "pace-maker").exists()
        assert (home / ".claude-pace-maker" / "pacemaker").exists()
        assert not (home / ".claude-pace-maker" / ".bootstrap_ok").exists()

    def test_full_writes_bootstrap_ok(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        result = run_bootstrap(home, "--full")
        assert result.returncode == 0, result.stderr
        assert (home / ".claude-pace-maker" / ".bootstrap_ok").exists()


class TestBootstrapVenv:
    def test_second_full_run_is_idempotent(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        first = run_bootstrap(home, "--full")
        assert first.returncode == 0, first.stderr
        second = run_bootstrap(home, "--full")
        assert second.returncode == 0, second.stderr

    def test_full_creates_managed_venv(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        result = run_bootstrap(home, "--full")
        assert result.returncode == 0, result.stderr
        venv_python = home / ".claude-pace-maker" / "venv" / "bin" / "python3"
        assert venv_python.exists(), "managed venv python3 must exist after --full"
        assert venv_python.is_file()

    def test_venv_stamp_records_base_python_and_deps(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        run_bootstrap(home, "--full")
        stamp = home / ".claude-pace-maker" / ".venv_stamp"
        assert stamp.exists(), ".venv_stamp must be written after --full"
        content = stamp.read_text().strip()
        assert ":" in content
        assert "requests:pyyaml:claude-agent-sdk" in content

    def test_deps_importable_in_venv(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        run_bootstrap(home, "--full")
        venv_python = home / ".claude-pace-maker" / "venv" / "bin" / "python3"
        proc = subprocess.run(
            [
                str(venv_python),
                "-c",
                "import requests, yaml, claude_agent_sdk",
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr


class TestStaleVenvLockRecovery:
    def test_stale_lock_dir_is_cleared_and_bootstrap_succeeds(self, tmp_path):
        """Orphaned .venv.lock.d from an interrupted bootstrap must not block forever."""
        home = tmp_path / "home"
        home.mkdir()
        pacemaker_dir = home / ".claude-pace-maker"
        pacemaker_dir.mkdir()
        stale_lock = pacemaker_dir / ".venv.lock.d"
        stale_lock.mkdir()

        result = run_bootstrap(home, "--full")
        assert result.returncode == 0, result.stderr
        assert not stale_lock.exists(), "stale lock dir should be removed after bootstrap"
        assert (pacemaker_dir / ".bootstrap_ok").exists()


class TestVenvPipNeverTouchesSystemPython:
    def test_venv_pip_failure_writes_failed_marker_no_system_pip(self, tmp_path):
        """When venv pip install fails, .venv.failed is written; system pip is never used."""
        home = tmp_path / "home"
        home.mkdir()

        real_python = shutil.which("python3")
        assert real_python is not None, "python3 not found on PATH"

        fake_bin = tmp_path / "fake_bin"
        fake_bin.mkdir()
        pip_call_log = fake_bin / "pip_calls.log"
        fake_python = fake_bin / "python3"
        fake_python.write_text(
            f"""#!/usr/bin/env bash
is_pip=0; in_venv=0
for arg in "$@"; do
    [ "$arg" = "pip" ] && is_pip=1
    case "$arg" in
        */.claude-pace-maker/venv/*) in_venv=1 ;;
    esac
done
if [ "$is_pip" = "1" ]; then
    [ -n "${{PIP_CALL_LOG:-}}" ] && echo "$*" >> "$PIP_CALL_LOG"
    if [ "$in_venv" = "1" ]; then
        echo "fake: venv pip install failed" >&2
        exit 1
    fi
    echo "fake: system pip must not be called" >&2
    exit 1
fi
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
    exec {real_python} "$@"
fi
exec {real_python} "$@"
"""
        )
        fake_python.chmod(0o755)

        result = run_bootstrap(
            home,
            "--full",
            extra_env={
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "PIP_CALL_LOG": str(pip_call_log),
            },
        )

        assert result.returncode != 0

        failed = home / ".claude-pace-maker" / ".venv.failed"
        assert failed.exists(), "Expected .venv.failed when venv pip fails"

        if pip_call_log.exists():
            calls = pip_call_log.read_text().splitlines()
            system_calls = [c for c in calls if ".claude-pace-maker/venv" not in c]
            assert len(system_calls) == 0, (
                f"System pip must not be invoked; got: {system_calls}"
            )
