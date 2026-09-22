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


def _run_bootstrap_check(home, check_script: str) -> subprocess.CompletedProcess:
    """Run a `source bootstrap-plugin.sh; ...` snippet against `home`.
    Consolidates the HOME/PLUGIN_ROOT env-building repeated across every
    test that sources the script to call one of its internal functions
    directly, instead of going through run_bootstrap()."""
    return subprocess.run(
        ["bash", "-c", check_script],
        env={**os.environ, "HOME": str(home), "PLUGIN_ROOT": str(REPO_ROOT)},
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def prebaked_full_home(tmp_path_factory):
    """One real `--full` bootstrap (real venv creation + real `pip install`
    of the pinned deps -- no mocking) shared, read-only, across every test
    in this module that just needs a HOME with a completed bootstrap
    already in place.

    Issue #144 root cause: this file previously called
    `run_bootstrap(home, "--full")` on a *fresh* HOME in ~15 separate
    tests. Each real `--full` run takes ~20-25s (venv creation + pip
    install of requests/pyyaml/claude-agent-sdk), so the file blew both
    the per-test `--timeout=15` used by `scripts/run_tests.sh` and the
    per-file time cap even though every test passed once given enough
    time (verified: 17 passed, 0 failed, ~361s with `--timeout=170`).

    Tests that only need to OBSERVE a completed bootstrap, or that mutate
    a COPY of one, use this fixture (directly, or via `_clone_home`)
    instead of paying for another fresh pip install. Tests that
    specifically exercise the from-empty-HOME code path (stale lock
    recovery, concurrent venv creation, the pip-failure shim) still
    bootstrap fresh on their own `tmp_path` -- see the
    `@pytest.mark.timeout` on those tests for why.
    """
    home = tmp_path_factory.mktemp("prebaked_home")
    result = run_bootstrap(home, "--full")
    assert result.returncode == 0, result.stderr
    return home


def _clone_home(prebaked_home: Path, tmp_path: Path) -> Path:
    """Copy a prebaked, fully-bootstrapped HOME tree into this test's own
    tmp_path so it can be mutated (downgrade a dep, corrupt a stamp, add
    a failure marker) without paying for a fresh venv creation + pip
    install. Safe to mutate: every bootstrap-plugin.sh code path invokes
    the venv interpreter via `python -m pip`/`python -c`, never the venv's
    own `bin/pip` script directly, so the stale absolute shebang left
    behind by the copy is never exercised."""
    dest = tmp_path / "home"
    shutil.copytree(prebaked_home, dest, symlinks=True)
    return dest


class TestBootstrapLight:
    def test_light_creates_symlinks_without_bootstrap_ok(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        result = run_bootstrap(home, "--light")
        assert result.returncode == 0
        assert (home / ".local" / "bin" / "pace-maker").exists()
        assert (home / ".claude-pace-maker" / "pacemaker").exists()
        assert not (home / ".claude-pace-maker" / ".bootstrap_ok").exists()

    @pytest.mark.timeout(90)
    def test_full_writes_bootstrap_ok(self, prebaked_full_home):
        assert (prebaked_full_home / ".claude-pace-maker" / ".bootstrap_ok").exists()


class TestBootstrapVenv:
    @pytest.mark.timeout(60)
    def test_second_full_run_is_idempotent(self, tmp_path, prebaked_full_home):
        home = _clone_home(prebaked_full_home, tmp_path)
        second = run_bootstrap(home, "--full")
        assert second.returncode == 0, second.stderr

    @pytest.mark.timeout(90)
    def test_full_creates_managed_venv(self, prebaked_full_home):
        venv_python = (
            prebaked_full_home / ".claude-pace-maker" / "venv" / "bin" / "python3"
        )
        assert venv_python.exists(), "managed venv python3 must exist after --full"
        assert venv_python.is_file()

    @pytest.mark.timeout(90)
    def test_venv_stamp_records_base_python_and_requirements_sha256(
        self, prebaked_full_home
    ):
        """Stamp format is `<base_py>:<sha256(requirements.txt)>`. The hash
        suffix means ANY edit to requirements.txt — version bump, comment
        change, added dep — auto-invalidates the stamp and re-bootstraps."""
        stamp = prebaked_full_home / ".claude-pace-maker" / ".venv_stamp"
        assert stamp.exists(), ".venv_stamp must be written after --full"
        content = stamp.read_text().strip()
        expected_sha = _requirements_sha256()
        assert content.endswith(f":{expected_sha}"), (
            f"stamp suffix should match sha256 of requirements.txt ({expected_sha}); "
            f"got: {content!r}"
        )

    @pytest.mark.timeout(60)
    def test_drifted_version_is_repaired_on_next_bootstrap(
        self, tmp_path, prebaked_full_home
    ):
        """If a dep is manually downgraded inside the venv, the next
        bootstrap_full must detect the drift via _deps_imports_ok's
        exact-version assertion (driven by requirements.txt) and
        re-install the pinned version via `pip install -r`."""
        home = _clone_home(prebaked_full_home, tmp_path)
        venv_python = home / ".claude-pace-maker" / "venv" / "bin" / "python3"

        downgrade_version = "2.32.0"
        assert downgrade_version != PINS["requests"], (
            "downgrade target must genuinely differ from the pinned version "
            f"({PINS['requests']!r}) or this test creates no drift to repair"
        )
        downgrade = subprocess.run(
            [
                str(venv_python),
                "-m",
                "pip",
                "install",
                "--quiet",
                f"requests=={downgrade_version}",
            ],
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

    @pytest.mark.timeout(60)
    def test_requirements_file_edit_invalidates_stamp(
        self, tmp_path, prebaked_full_home
    ):
        """The cheap bootstrap_needs_full check must report 'needs full'
        when requirements.txt changes — even if no code in
        bootstrap-plugin.sh did. This is the key benefit of hashing the
        file rather than hardcoding versions in shell."""
        home = _clone_home(prebaked_full_home, tmp_path)
        stamp = home / ".claude-pace-maker" / ".venv_stamp"
        original = stamp.read_text().strip()
        # Simulate an old install where requirements.txt was a different
        # version of itself by rewriting the stamp suffix to a wrong sha.
        base_py = original.split(":", 1)[0]
        stamp.write_text(
            f"{base_py}:0000000000000000000000000000000000000000000000000000000000000000\n"
        )

        check = _run_bootstrap_check(
            home,
            f"source {BOOTSTRAP_SH}; "
            "if bootstrap_needs_full; then echo NEEDS_FULL; else echo OK; fi",
        )
        assert (
            "NEEDS_FULL" in check.stdout
        ), f"stamp with wrong sha must report needs_full; got: {check.stdout!r}"


class TestConcurrentBootstrap:
    @pytest.mark.timeout(150)
    def test_parallel_full_bootstrap_does_not_corrupt_venv(self, tmp_path):
        """Concurrent --full invocations against the same HOME must serialize
        venv creation under the install lock. With the previous design
        (rm -rf / python -m venv ran OUTSIDE the lock), two processes could
        both delete and recreate the venv, clobbering each other and leaving
        a corrupt environment.

        Deliberately NOT using prebaked_full_home/_clone_home: this test's
        entire point is racing --full processes against a HOME with NO
        pre-existing venv, to prove the install lock (not a warm stamp fast
        path) is what keeps them from corrupting each other. n=2 (reduced
        from 4, issue #144) is the minimum that proves a genuine race --
        two processes both attempting venv creation with nothing there yet
        -- while cutting process-spawn/CPU contention that was pushing this
        test to 49-66s on a loaded box and threatening the file's overall
        time budget."""
        home = tmp_path / "home"
        home.mkdir()

        env = os.environ.copy()
        env["HOME"] = str(home)
        env["PLUGIN_ROOT"] = str(REPO_ROOT)

        n = 2
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


def _replace_venv_python_with_sentinel_shim(venv_python: Path, sentinel: Path) -> None:
    """Swap the venv's python3 binary for a script that just records its
    invocation and exits 0. Shared by the two "must not fork the venv
    python on the cheap/fast path" tests below."""
    venv_python.unlink()
    venv_python.write_text(f'#!/usr/bin/env bash\necho "$*" >> {sentinel}\nexit 0\n')
    venv_python.chmod(0o755)


class TestBootstrapNeedsFullIsCheap:
    @pytest.mark.timeout(60)
    def test_needs_full_does_not_fork_venv_python(self, tmp_path, prebaked_full_home):
        """Per-hook bootstrap_needs_full must use file stats + stamp check
        only — no python fork. Replace VENV_PYTHON with a sentinel-recording
        script; if bootstrap_needs_full invokes it, the sentinel fires and
        the test fails."""
        home = _clone_home(prebaked_full_home, tmp_path)

        venv_python = home / ".claude-pace-maker" / "venv" / "bin" / "python3"
        assert venv_python.exists()
        sentinel = tmp_path / "venv_python_invoked.log"
        _replace_venv_python_with_sentinel_shim(venv_python, sentinel)

        check = _run_bootstrap_check(
            home,
            f"source {BOOTSTRAP_SH}; "
            "if bootstrap_needs_full; then echo NEEDS_FULL; else echo OK; fi",
        )
        assert check.returncode == 0, check.stderr
        assert "OK" in check.stdout, (
            f"bootstrap_needs_full should report no full needed when stamp matches; "
            f"got stdout={check.stdout!r} stderr={check.stderr!r}"
        )
        assert not sentinel.exists(), (
            f"bootstrap_needs_full forked the venv python — cheap check regressed. "
            f"Sentinel contents: {sentinel.read_text()}"
        )

    @pytest.mark.timeout(60)
    def test_resolve_runtime_python_does_not_fork_venv_python(
        self, tmp_path, prebaked_full_home
    ):
        """resolve_runtime_python must use the stamp-based fast path (no
        Python fork) when the stamp matches. Same sentinel approach as
        test_needs_full_does_not_fork_venv_python."""
        home = _clone_home(prebaked_full_home, tmp_path)

        venv_python = home / ".claude-pace-maker" / "venv" / "bin" / "python3"
        assert venv_python.exists()
        sentinel = tmp_path / "resolve_runtime_invoked.log"
        _replace_venv_python_with_sentinel_shim(venv_python, sentinel)

        check = _run_bootstrap_check(
            home,
            f"source {BOOTSTRAP_SH}; "
            'result=$(resolve_runtime_python 2>/dev/null) && echo "GOT=$result" || echo FAILED',
        )
        assert check.returncode == 0, check.stderr
        assert "GOT=" in check.stdout, (
            f"resolve_runtime_python should succeed when stamp matches; "
            f"got stdout={check.stdout!r} stderr={check.stderr!r}"
        )
        assert not sentinel.exists(), (
            f"resolve_runtime_python forked the venv python — stamp fast path regressed. "
            f"Sentinel contents: {sentinel.read_text()}"
        )

    @pytest.mark.timeout(60)
    def test_needs_full_returns_true_when_stamp_signature_mismatch(
        self, tmp_path, prebaked_full_home
    ):
        """If DEPS_SIGNATURE rolls forward, the stamp won't match and the
        cheap check should report 'needs full' so SessionStart re-bootstraps."""
        home = _clone_home(prebaked_full_home, tmp_path)

        stamp = home / ".claude-pace-maker" / ".venv_stamp"
        assert stamp.exists()
        stamp.write_text("/some/python:obsolete:signature\n")

        check = _run_bootstrap_check(
            home,
            f"source {BOOTSTRAP_SH}; "
            "if bootstrap_needs_full; then echo NEEDS_FULL; else echo OK; fi",
        )
        assert check.returncode == 0, check.stderr
        assert "NEEDS_FULL" in check.stdout, (
            f"bootstrap_needs_full should report needs full on signature mismatch; "
            f"got stdout={check.stdout!r} stderr={check.stderr!r}"
        )


class TestVenvFailedMarkerAutoRetry:
    @pytest.mark.timeout(60)
    def test_bootstrap_full_clears_failed_marker_and_retries(
        self, tmp_path, prebaked_full_home
    ):
        """bootstrap_full must clear .venv.failed before _ensure_venv_and_deps
        so that transient failures (network timeout during pip install) are
        retried automatically on each session_start rather than requiring
        manual `pace-maker doctor` intervention.

        Uses a clone of a working venv (rather than a from-empty HOME) --
        `bootstrap_full` does `rm -f "$VENV_FAILED_MARKER"` unconditionally,
        before it even looks at whether the venv already satisfies the pin,
        so this still genuinely exercises the ordering being tested."""
        home = _clone_home(prebaked_full_home, tmp_path)
        pacemaker_dir = home / ".claude-pace-maker"
        (pacemaker_dir / ".bootstrap_ok").unlink()
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
    @pytest.mark.timeout(30)
    def test_symlink_with_dead_pid_is_cleared_by_install_lock(self, tmp_path):
        """A symlink lock left by a crashed bootstrap (target string is a
        dead pid) must be auto-cleared so the next lock acquisition
        proceeds without waiting on the lock timeout.

        Calls _with_venv_install_lock directly (wrapping the trivial `true`
        builtin) rather than running a full `bootstrap_full --full`.
        _clear_stale_venv_lock_symlink runs unconditionally at the very top
        of _with_venv_install_lock, before flock/symlink acquisition and
        regardless of what command it wraps -- so this exercises the exact
        real stale-lock-clearing code path bootstrap_full's slow path
        (_create_or_repair_venv_locked) would reach, without paying for the
        unrelated real venv creation + pip install a full bootstrap would
        also perform to get there (issue #144: that indirection cost
        ~25-50s here for zero additional coverage of the lock logic
        itself). A HOME with an already-matching stamp takes the FAST path
        in _ensure_venv_and_deps and never reaches _with_venv_install_lock
        at all, which is why this can't reuse prebaked_full_home/_clone_home
        either -- the function under test must be invoked directly. The
        timeout marker is kept (lowered from 60s to 30s) purely as a
        hang-protection safety net now that the real cost is near-zero."""
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

        check = _run_bootstrap_check(
            home,
            f'source {BOOTSTRAP_SH}; _with_venv_install_lock true; echo "RC=$?"',
        )
        assert check.returncode == 0, check.stderr
        assert "RC=0" in check.stdout, (
            f"_with_venv_install_lock should succeed once the stale lock is "
            f"cleared; got stdout={check.stdout!r} stderr={check.stderr!r}"
        )
        assert (
            not stale_lock.is_symlink()
        ), "stale lock symlink should be removed by _with_venv_install_lock"


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

        check_script = f"""
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
        proc = _run_bootstrap_check(home, check_script)
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

        check = _run_bootstrap_check(
            home,
            f"source {BOOTSTRAP_SH}; "
            "if _try_acquire_venv_install_lock; then echo ACQUIRED; else echo FAILED; fi",
        )
        assert check.returncode == 0, check.stderr
        assert (
            "FAILED" in check.stdout
        ), f"acquire must fail when symlink is held; stdout={check.stdout!r}"
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

        check = _run_bootstrap_check(
            home,
            f"source {BOOTSTRAP_SH}; _clear_stale_venv_lock_symlink; "
            '[ -L "$VENV_LOCK_LINK" ] && echo PRESERVED || echo REMOVED',
        )
        assert check.returncode == 0, check.stderr
        assert (
            "PRESERVED" in check.stdout
        ), f"live holder must not be cleared; stdout={check.stdout!r}"


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
    @pytest.mark.timeout(90)
    def test_venv_pip_failure_writes_failed_marker_no_system_pip(self, tmp_path):
        """When venv pip install fails, .venv.failed is written; system pip is never used.

        Mutation-test contract: the shim is wired into the venv's python
        symlinks, so any pip invocation from the venv interpreter is logged
        as ``invoker=<venv-bin-path>``. A pip call whose invoker is NOT under
        ``.claude-pace-maker/venv`` would indicate the bootstrap fell back to
        a system interpreter, which is the regression we want to catch.

        Deliberately NOT using prebaked_full_home/_clone_home: the shim must
        be wired in BEFORE the venv is ever created so it intercepts the
        real `python -m venv` call itself.
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
