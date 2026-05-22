"""
Tests for scripts/bootstrap-plugin.sh (plugin bootstrap and dep markers).
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


class TestBootstrapDepsMarkers:
    def test_second_full_run_is_idempotent(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        first = run_bootstrap(home, "--full")
        assert first.returncode == 0, first.stderr
        second = run_bootstrap(home, "--full")
        assert second.returncode == 0, second.stderr

    def test_resolved_python_dedup_single_marker_per_interpreter(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        run_bootstrap(home, "--full")
        deps_dir = home / ".claude-pace-maker" / ".python_deps"
        if not deps_dir.exists():
            pytest.skip("no deps markers (packages may be preinstalled globally)")
        markers = [p for p in deps_dir.iterdir() if p.is_file() and not p.name.endswith(".failed")]
        assert len(markers) >= 1
        for marker in markers:
            content = marker.read_text().strip()
            assert ":" in content
            assert "requests:pyyaml:claude-agent-sdk" in content


def _make_pip_shim(fake_bin: Path, real_python: str) -> Path:
    """Write a python3 shim and return the path of the pip call log it will create.

    Shim behaviour:
    - Every ``-m pip`` invocation appends the full argv to ``$PIP_CALL_LOG``.
    - ``pip install --user …`` exits 1  (PEP-668 simulation).
    - Bare ``pip install …`` (no --user) exits 0  (mutation discriminator:
      buggy fallback code would reach this branch and succeed, leaving a bare
      call in the log that the assertion helper will catch).
    - ``-c`` calls that import the three target packages exit 1, forcing the
      script past the fast-path import check so pip is actually attempted.
    - Everything else is delegated to the real python3.
    """
    pip_call_log = fake_bin / "pip_calls.log"
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        f"""#!/usr/bin/env bash
is_pip=0; has_user_flag=0; has_import_check=0
for arg in "$@"; do
    [ "$arg" = "pip" ] && is_pip=1
    [ "$arg" = "--user" ] && has_user_flag=1
    case "$arg" in
        *"import requests"*|*"import yaml"*|*"import claude_agent_sdk"*) has_import_check=1 ;;
    esac
done
if [ "$is_pip" = "1" ]; then
    [ -n "${{PIP_CALL_LOG:-}}" ] && echo "$*" >> "$PIP_CALL_LOG"
    if [ "$has_user_flag" = "1" ]; then
        echo "fake python3: pip --user rejected (PEP-668 simulation)" >&2; exit 1
    else
        exit 0
    fi
fi
if [ "$has_import_check" = "1" ]; then
    echo "fake python3: import check simulated as missing package" >&2; exit 1
fi
exec {real_python} "$@"
"""
    )
    fake_python.chmod(0o755)
    return pip_call_log


def _assert_pip_log_no_bare_retry(pip_call_log: Path, result) -> None:
    """Assert the pip call log exists, is non-empty, and has no bare pip calls.

    Requiring the log to exist (and be non-empty) proves that pip was actually
    invoked — guarding against false passes from unexpected early exits.
    Requiring every call to contain ``--user`` proves no bare-pip fallback ran.
    """
    assert pip_call_log.exists(), (
        "pip_calls.log not found — the shim was never invoked for a pip call. "
        "The script may have taken an unexpected exit path before reaching pip.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    calls = pip_call_log.read_text().splitlines()
    assert len(calls) >= 1, (
        "pip_calls.log exists but is empty — no pip invocation was logged.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    bare_calls = [c for c in calls if "--user" not in c]
    assert len(bare_calls) == 0, (
        "Bare pip retry detected — script fell back to bare pip install "
        f"(PEP 668 violation).\nBare calls: {bare_calls}\nAll calls: {calls}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


class TestPipUserFallbackRemoved:
    def test_pip_user_failure_writes_failed_marker_no_bare_pip_retry(self, tmp_path):
        """When pip install --user fails, .failed marker is written immediately.
        No bare pip install (without --user) should be attempted — that would
        violate PEP 668 on system-managed Python environments.

        Mutation-test contract:
        - Fixed code (--user only): one --user call logged → fails →
          .failed written → no bare calls in log → test PASSES.
        - Buggy code (bare fallback): --user call logged (fails) + bare call
          logged (exits 0 via shim) → bare call in log → assertion FAILS.
        """
        home = tmp_path / "home"
        home.mkdir()

        real_python = shutil.which("python3")
        assert real_python is not None, "python3 not found on PATH"

        fake_bin = tmp_path / "fake_bin"
        fake_bin.mkdir()
        pip_call_log = _make_pip_shim(fake_bin, real_python)

        result = run_bootstrap(
            home,
            "--full",
            extra_env={
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "PIP_CALL_LOG": str(pip_call_log),
            },
        )

        assert result.returncode != 0, (
            f"Expected non-zero returncode when pip --user fails, got 0.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        deps_dir = home / ".claude-pace-maker" / ".python_deps"
        failed_markers = list(deps_dir.glob("*.failed")) if deps_dir.exists() else []
        assert len(failed_markers) >= 1, (
            f"Expected at least one .failed marker in {deps_dir}, found none.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        _assert_pip_log_no_bare_retry(pip_call_log, result)
