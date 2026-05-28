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
REQUIREMENTS_TXT = REPO_ROOT / "requirements.txt"


def _parse_requirements():
    """Read the pinned specs from requirements.txt (the single source of
    truth) so tests track it without duplicating versions."""
    assert REQUIREMENTS_TXT.exists(), f"requirements.txt missing at {REQUIREMENTS_TXT}"
    specs = []
    for raw in REQUIREMENTS_TXT.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            specs.append(line)
    assert specs, f"no pinned specs parsed from {REQUIREMENTS_TXT}"
    return specs


def _requirements_sha256():
    import hashlib

    return hashlib.sha256(REQUIREMENTS_TXT.read_bytes()).hexdigest()


PINNED_SPECS = _parse_requirements()
PINS = {}
for _spec in PINNED_SPECS:
    _name, _, _ver = _spec.partition("==")
    PINS[_name] = _ver


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

    def test_venv_stamp_records_base_python_and_requirements_sha256(self, tmp_path):
        """Stamp format is `<base_py>:<sha256(requirements.txt)>`. The hash
        suffix means ANY edit to requirements.txt — version bump, comment
        change, added dep — auto-invalidates the stamp and re-bootstraps."""
        home = tmp_path / "home"
        home.mkdir()
        run_bootstrap(home, "--full")
        stamp = home / ".claude-pace-maker" / ".venv_stamp"
        assert stamp.exists(), ".venv_stamp must be written after --full"
        content = stamp.read_text().strip()
        expected_sha = _requirements_sha256()
        assert content.endswith(f":{expected_sha}"), (
            f"stamp suffix should match sha256 of requirements.txt ({expected_sha}); "
            f"got: {content!r}"
        )

    def test_drifted_version_is_repaired_on_next_bootstrap(self, tmp_path):
        """If a dep is manually downgraded inside the venv, the next
        bootstrap_full must detect the drift via _deps_imports_ok's
        exact-version assertion (driven by requirements.txt) and
        re-install the pinned version via `pip install -r`."""
        home = tmp_path / "home"
        home.mkdir()
        run_bootstrap(home, "--full")
        venv_python = home / ".claude-pace-maker" / "venv" / "bin" / "python3"

        downgrade = subprocess.run(
            [str(venv_python), "-m", "pip", "install", "--quiet", "requests==2.32.0"],
            capture_output=True,
            text=True,
        )
        assert downgrade.returncode == 0, downgrade.stderr

        # bootstrap_full must repair back to the pinned version.
        # Remove .bootstrap_ok so bootstrap_full's _ensure_venv_and_deps path runs.
        (home / ".claude-pace-maker" / ".bootstrap_ok").unlink()
        repair = run_bootstrap(home, "--full")
        assert repair.returncode == 0, repair.stderr
        version_check = subprocess.run(
            [str(venv_python), "-c", "import requests; print(requests.__version__)"],
            capture_output=True,
            text=True,
        )
        assert version_check.stdout.strip() == PINS["requests"], (
            f"requests should be repaired to pinned {PINS['requests']}, "
            f"got {version_check.stdout.strip()!r}"
        )

    def test_requirements_file_edit_invalidates_stamp(self, tmp_path):
        """The cheap bootstrap_needs_full check must report 'needs full'
        when requirements.txt changes — even if no code in
        bootstrap-plugin.sh did. This is the key benefit of hashing the
        file rather than hardcoding versions in shell."""
        home = tmp_path / "home"
        home.mkdir()
        run_bootstrap(home, "--full")
        stamp = home / ".claude-pace-maker" / ".venv_stamp"
        original = stamp.read_text().strip()
        # Simulate an old install where requirements.txt was a different
        # version of itself by rewriting the stamp suffix to a wrong sha.
        base_py = original.split(":", 1)[0]
        stamp.write_text(
            f"{base_py}:0000000000000000000000000000000000000000000000000000000000000000\n"
        )

        check = subprocess.run(
            [
                "bash",
                "-c",
                f"source {BOOTSTRAP_SH}; "
                "if bootstrap_needs_full; then echo NEEDS_FULL; else echo OK; fi",
            ],
            env={**os.environ, "HOME": str(home), "PLUGIN_ROOT": str(REPO_ROOT)},
            capture_output=True,
            text=True,
        )
        assert (
            "NEEDS_FULL" in check.stdout
        ), f"stamp with wrong sha must report needs_full; got: {check.stdout!r}"


class TestConcurrentBootstrap:
    def test_parallel_full_bootstrap_does_not_corrupt_venv(self, tmp_path):
        """Concurrent --full invocations against the same HOME must serialize
        venv creation under the install lock. With the previous design
        (rm -rf / python -m venv ran OUTSIDE the lock), two processes could
        both delete and recreate the venv, clobbering each other and leaving
        a corrupt environment."""
        home = tmp_path / "home"
        home.mkdir()

        env = os.environ.copy()
        env["HOME"] = str(home)
        env["PLUGIN_ROOT"] = str(REPO_ROOT)

        n = 4
        procs = [
            subprocess.Popen(
                ["bash", str(BOOTSTRAP_SH), "--full"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(REPO_ROOT),
            )
            for _ in range(n)
        ]
        results = []
        for p in procs:
            out, err = p.communicate(timeout=180)
            results.append((p.returncode, out.decode(), err.decode()))

        for i, (rc, out, err) in enumerate(results):
            assert (
                rc == 0
            ), f"parallel bootstrap #{i} failed (rc={rc})\nstdout={out}\nstderr={err}"

        venv_python = home / ".claude-pace-maker" / "venv" / "bin" / "python3"
        assert (
            venv_python.exists()
        ), "managed venv python missing after concurrent bootstrap"

        check = subprocess.run(
            [str(venv_python), "-c", "import requests, yaml, claude_agent_sdk"],
            capture_output=True,
            text=True,
        )
        assert (
            check.returncode == 0
        ), f"venv is broken after concurrent bootstrap: {check.stderr}"
        assert (home / ".claude-pace-maker" / ".bootstrap_ok").exists()
        assert not (home / ".claude-pace-maker" / ".venv.failed").exists()


class TestBootstrapNeedsFullIsCheap:
    def test_needs_full_does_not_fork_venv_python(self, tmp_path):
        """Per-hook bootstrap_needs_full must use file stats + stamp check
        only — no python fork. Replace VENV_PYTHON with a sentinel-recording
        script; if bootstrap_needs_full invokes it, the sentinel fires and
        the test fails."""
        home = tmp_path / "home"
        home.mkdir()
        first = run_bootstrap(home, "--full")
        assert first.returncode == 0, first.stderr

        venv_python = home / ".claude-pace-maker" / "venv" / "bin" / "python3"
        assert venv_python.exists()
        sentinel = tmp_path / "venv_python_invoked.log"
        if venv_python.is_symlink():
            venv_python.unlink()
        else:
            venv_python.unlink()
        venv_python.write_text(
            f'#!/usr/bin/env bash\necho "$*" >> {sentinel}\nexit 0\n'
        )
        venv_python.chmod(0o755)

        check_script = (
            f"source {BOOTSTRAP_SH}; "
            "if bootstrap_needs_full; then echo NEEDS_FULL; else echo OK; fi"
        )
        proc = subprocess.run(
            ["bash", "-c", check_script],
            env={
                **os.environ,
                "HOME": str(home),
                "PLUGIN_ROOT": str(REPO_ROOT),
            },
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "OK" in proc.stdout, (
            f"bootstrap_needs_full should report no full needed when stamp matches; "
            f"got stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
        assert not sentinel.exists(), (
            f"bootstrap_needs_full forked the venv python — cheap check regressed. "
            f"Sentinel contents: {sentinel.read_text()}"
        )

    def test_resolve_runtime_python_does_not_fork_venv_python(self, tmp_path):
        """resolve_runtime_python must use the stamp-based fast path (no
        Python fork) when the stamp matches. Same sentinel approach as
        test_needs_full_does_not_fork_venv_python."""
        home = tmp_path / "home"
        home.mkdir()
        first = run_bootstrap(home, "--full")
        assert first.returncode == 0, first.stderr

        venv_python = home / ".claude-pace-maker" / "venv" / "bin" / "python3"
        assert venv_python.exists()
        sentinel = tmp_path / "resolve_runtime_invoked.log"
        if venv_python.is_symlink():
            venv_python.unlink()
        else:
            venv_python.unlink()
        venv_python.write_text(
            f'#!/usr/bin/env bash\necho "$*" >> {sentinel}\nexit 0\n'
        )
        venv_python.chmod(0o755)

        check_script = (
            f"source {BOOTSTRAP_SH}; "
            'result=$(resolve_runtime_python 2>/dev/null) && echo "GOT=$result" || echo FAILED'
        )
        proc = subprocess.run(
            ["bash", "-c", check_script],
            env={
                **os.environ,
                "HOME": str(home),
                "PLUGIN_ROOT": str(REPO_ROOT),
            },
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "GOT=" in proc.stdout, (
            f"resolve_runtime_python should succeed when stamp matches; "
            f"got stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
        assert not sentinel.exists(), (
            f"resolve_runtime_python forked the venv python — stamp fast path regressed. "
            f"Sentinel contents: {sentinel.read_text()}"
        )

    def test_needs_full_returns_true_when_stamp_signature_mismatch(self, tmp_path):
        """If DEPS_SIGNATURE rolls forward, the stamp won't match and the
        cheap check should report 'needs full' so SessionStart re-bootstraps."""
        home = tmp_path / "home"
        home.mkdir()
        result = run_bootstrap(home, "--full")
        assert result.returncode == 0, result.stderr

        stamp = home / ".claude-pace-maker" / ".venv_stamp"
        assert stamp.exists()
        stamp.write_text("/some/python:obsolete:signature\n")

        check_script = (
            f"source {BOOTSTRAP_SH}; "
            "if bootstrap_needs_full; then echo NEEDS_FULL; else echo OK; fi"
        )
        proc = subprocess.run(
            ["bash", "-c", check_script],
            env={
                **os.environ,
                "HOME": str(home),
                "PLUGIN_ROOT": str(REPO_ROOT),
            },
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "NEEDS_FULL" in proc.stdout, (
            f"bootstrap_needs_full should report needs full on signature mismatch; "
            f"got stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )


class TestVenvFailedMarkerAutoRetry:
    def test_bootstrap_full_clears_failed_marker_and_retries(self, tmp_path):
        """bootstrap_full must clear .venv.failed before _ensure_venv_and_deps
        so that transient failures (network timeout during pip install) are
        retried automatically on each session_start rather than requiring
        manual `pace-maker doctor` intervention."""
        home = tmp_path / "home"
        home.mkdir()
        pacemaker_dir = home / ".claude-pace-maker"
        pacemaker_dir.mkdir()
        failed_marker = pacemaker_dir / ".venv.failed"
        failed_marker.touch()
        assert failed_marker.exists()

        result = run_bootstrap(home, "--full")
        assert result.returncode == 0, result.stderr
        assert not failed_marker.exists(), (
            ".venv.failed must be cleared by bootstrap_full so transient "
            "failures auto-retry on next session_start"
        )
        assert (pacemaker_dir / ".bootstrap_ok").exists()


class TestStaleVenvLockRecovery:
    def test_symlink_with_dead_pid_is_cleared_and_bootstrap_succeeds(self, tmp_path):
        """A symlink lock left by a crashed bootstrap (target string is a
        dead pid) must be auto-cleared so the next invocation proceeds
        without waiting on the lock timeout."""
        home = tmp_path / "home"
        home.mkdir()
        pacemaker_dir = home / ".claude-pace-maker"
        pacemaker_dir.mkdir()
        stale_lock = pacemaker_dir / ".venv.lock.link"

        import sys

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


class TestVenvLockSymlinkAcquire:
    """The symlink lock binds the pid into the link target at symlink(2)
    time. There is no torn-write window: either the symlink doesn't
    exist, or it exists with a populated target. These tests cover the
    invariants that flow from that property."""

    def test_acquired_symlink_target_is_acquiring_pid(self, tmp_path):
        """Acquire returns the lock with the acquiring shell's pid as the
        symlink target — readable via readlink in one syscall."""
        home = tmp_path / "home"
        home.mkdir()
        pacemaker_dir = home / ".claude-pace-maker"
        pacemaker_dir.mkdir()

        check = f"""
source {BOOTSTRAP_SH}
_try_acquire_venv_install_lock || {{ echo ACQUIRE_FAILED; exit 1; }}
if [ -L "$VENV_LOCK_LINK" ]; then
    echo SYMLINK_EXISTS=1
else
    echo SYMLINK_EXISTS=0
fi
echo "LINK_TARGET=$(readlink "$VENV_LOCK_LINK")"
echo "MY_PID=$$"
rm -f "$VENV_LOCK_LINK"
"""
        proc = subprocess.run(
            ["bash", "-c", check],
            env={**os.environ, "HOME": str(home), "PLUGIN_ROOT": str(REPO_ROOT)},
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert (
            "SYMLINK_EXISTS=1" in proc.stdout
        ), f"Symlink must exist after acquire; stdout={proc.stdout!r}"
        my_pid = next(
            (
                line.split("=", 1)[1]
                for line in proc.stdout.splitlines()
                if line.startswith("MY_PID=")
            ),
            None,
        )
        target = next(
            (
                line.split("=", 1)[1]
                for line in proc.stdout.splitlines()
                if line.startswith("LINK_TARGET=")
            ),
            None,
        )
        assert my_pid is not None and target is not None
        assert (
            target == my_pid
        ), f"symlink target {target!r} should equal acquiring shell pid {my_pid!r}"

    def test_acquire_fails_when_lock_is_held(self, tmp_path):
        """A second acquire attempt while the lock is held must fail
        without touching the existing symlink."""
        home = tmp_path / "home"
        home.mkdir()
        pacemaker_dir = home / ".claude-pace-maker"
        pacemaker_dir.mkdir()
        lock_link = pacemaker_dir / ".venv.lock.link"
        held_pid = str(os.getpid())
        os.symlink(held_pid, str(lock_link))

        check = (
            f"source {BOOTSTRAP_SH}; "
            "if _try_acquire_venv_install_lock; then echo ACQUIRED; else echo FAILED; fi"
        )
        proc = subprocess.run(
            ["bash", "-c", check],
            env={**os.environ, "HOME": str(home), "PLUGIN_ROOT": str(REPO_ROOT)},
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert (
            "FAILED" in proc.stdout
        ), f"acquire must fail when symlink is held; stdout={proc.stdout!r}"
        assert lock_link.is_symlink()
        assert os.readlink(str(lock_link)) == held_pid

    def test_live_pid_holder_is_not_cleared(self, tmp_path):
        """_clear_stale_venv_lock_symlink must leave a live holder alone."""
        home = tmp_path / "home"
        home.mkdir()
        pacemaker_dir = home / ".claude-pace-maker"
        pacemaker_dir.mkdir()
        lock_link = pacemaker_dir / ".venv.lock.link"
        os.symlink(str(os.getpid()), str(lock_link))

        check = (
            f"source {BOOTSTRAP_SH}; _clear_stale_venv_lock_symlink; "
            '[ -L "$VENV_LOCK_LINK" ] && echo PRESERVED || echo REMOVED'
        )
        proc = subprocess.run(
            ["bash", "-c", check],
            env={**os.environ, "HOME": str(home), "PLUGIN_ROOT": str(REPO_ROOT)},
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert (
            "PRESERVED" in proc.stdout
        ), f"live holder must not be cleared; stdout={proc.stdout!r}"


def _install_python_shim(fake_bin: Path, real_python: str, pip_call_log: Path) -> Path:
    """Install a python3 shim that intercepts pip calls and forces import
    failures. After creating a venv via the real interpreter, the shim
    relinks the venv's python symlinks back to itself so subsequent
    venv-pip invocations are also captured (the real python's symlinks
    would otherwise bypass the shim entirely)."""
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        f"""#!/usr/bin/env bash
is_pip=0; in_venv=0; has_import_check=0
case "$0" in
    */.claude-pace-maker/venv/*) in_venv=1 ;;
esac
for arg in "$@"; do
    [ "$arg" = "pip" ] && is_pip=1
    case "$arg" in
        *"import requests"*|*"import yaml"*|*"import claude_agent_sdk"*) has_import_check=1 ;;
    esac
done
if [ "$is_pip" = "1" ]; then
    [ -n "${{PIP_CALL_LOG:-}}" ] && echo "invoker=$0 args=$*" >> "$PIP_CALL_LOG"
    if [ "$in_venv" = "1" ]; then
        echo "fake: venv pip install failed" >&2
        exit 1
    fi
    echo "fake: system pip must not be called" >&2
    exit 1
fi
if [ "$has_import_check" = "1" ]; then
    echo "fake: import check forced failure" >&2
    exit 1
fi
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
    venv_dir="$3"
    {real_python} "$@"
    rc=$?
    if [ $rc -eq 0 ] && [ -d "$venv_dir/bin" ]; then
        for f in "$venv_dir"/bin/python "$venv_dir"/bin/python3 "$venv_dir"/bin/python3.*; do
            if [ -e "$f" ] || [ -L "$f" ]; then
                rm -f "$f"
                ln -sf "$0" "$f"
            fi
        done
    fi
    exit $rc
fi
exec {real_python} "$@"
"""
    )
    fake_python.chmod(0o755)
    # resolve_python tries python3.13 .. python3.10 before python3. Cover them all
    # so the shim is selected as the base interpreter, not a real versioned python
    # that happens to be on PATH.
    for name in ("python3.10", "python3.11", "python3.12", "python3.13", "python3.14"):
        link = fake_bin / name
        link.symlink_to("python3")
    return fake_python


class TestVenvPipNeverTouchesSystemPython:
    def test_venv_pip_failure_writes_failed_marker_no_system_pip(self, tmp_path):
        """When venv pip install fails, .venv.failed is written; system pip is never used.

        Mutation-test contract: the shim is wired into the venv's python
        symlinks, so any pip invocation from the venv interpreter is logged
        as ``invoker=<venv-bin-path>``. A pip call whose invoker is NOT under
        ``.claude-pace-maker/venv`` would indicate the bootstrap fell back to
        a system interpreter, which is the regression we want to catch.
        """
        home = tmp_path / "home"
        home.mkdir()

        # resolve_python() requires Python 3.10+; use the first available
        # 3.10+ interpreter so the shim passes the version check and bootstrap
        # reaches the pip-failure path.  Fall back to plain python3 only if no
        # versioned binary is found (test will then skip if it's too old).
        real_python = (
            shutil.which("python3.13")
            or shutil.which("python3.12")
            or shutil.which("python3.11")
            or shutil.which("python3.10")
            or shutil.which("python3")
        )
        assert real_python is not None, "python3 not found on PATH"
        import subprocess as _sp

        ver_check = _sp.run(
            [
                real_python,
                "-c",
                "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)",
            ],
            capture_output=True,
        )
        if ver_check.returncode != 0:
            pytest.skip(f"No Python 3.10+ available (found {real_python})")

        fake_bin = tmp_path / "fake_bin"
        fake_bin.mkdir()
        pip_call_log = fake_bin / "pip_calls.log"
        _install_python_shim(fake_bin, real_python, pip_call_log)

        result = run_bootstrap(
            home,
            "--full",
            extra_env={
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "PIP_CALL_LOG": str(pip_call_log),
            },
        )

        assert result.returncode != 0, (
            f"Bootstrap should fail when venv pip is rejected.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        failed = home / ".claude-pace-maker" / ".venv.failed"
        assert failed.exists(), "Expected .venv.failed when venv pip fails"

        assert pip_call_log.exists(), (
            "pip shim was never invoked — bootstrap may have skipped pip entirely.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        calls = pip_call_log.read_text().splitlines()
        venv_calls = [c for c in calls if ".claude-pace-maker/venv" in c]
        system_calls = [c for c in calls if ".claude-pace-maker/venv" not in c]
        assert len(venv_calls) >= 1, (
            "Expected at least one pip call invoked from the managed venv. "
            f"All calls: {calls}"
        )
        assert (
            len(system_calls) == 0
        ), f"System pip must not be invoked; got: {system_calls}\nAll calls: {calls}"
