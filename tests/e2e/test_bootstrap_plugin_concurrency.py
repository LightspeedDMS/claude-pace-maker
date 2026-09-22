"""
Concurrency test for scripts/bootstrap-plugin.sh, split out of
tests/test_bootstrap_plugin.py into tests/e2e/ (issue #144 code-review
follow-up #2).

Why this lives in tests/e2e/, not the fast suite: racing N real
`bootstrap-plugin.sh --full` processes against a HOME with no pre-existing
venv is the whole point of this test (proving the install lock, not a warm
stamp fast path, is what prevents corruption) -- there is no
prebaked-fixture shortcut available for it the way there is for the other
bootstrap-plugin.sh tests. Under `--quick`'s original file budget (120s)
this test's own real cost (49-92s measured, machine-load dependent) left
test_bootstrap_plugin.py with no safety margin; tests/e2e/ has its own,
more generous per-file/per-test budget (see scripts/run_tests.sh).
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BOOTSTRAP_SH = REPO_ROOT / "scripts" / "bootstrap-plugin.sh"

# Number of concurrent --full bootstraps raced against the same, empty HOME.
CONCURRENT_PROCESSES = 4
# pytest-timeout override for the whole test (real cost measured 49-92s
# depending on machine load).
PER_TEST_TIMEOUT_SECONDS = 150
# Per-process wait ceiling passed to subprocess.communicate().
PROCESS_WAIT_TIMEOUT_SECONDS = 180


def _wait_all_with_cleanup(procs, timeout: float):
    """Wait on every process in `procs`. If ANY of them fails to exit
    within `timeout`, kill and drain every process in the batch (not just
    the one that timed out) before raising -- a hung child must never be
    left running, and this is a batch of concurrently-raced processes, so
    one hanging leaves the others in an unknown state too."""
    results = [None] * len(procs)
    try:
        for i, p in enumerate(procs):
            out, err = p.communicate(timeout=timeout)
            results[i] = (p.returncode, out.decode(), err.decode())
    except subprocess.TimeoutExpired:
        for p in procs:
            if p.poll() is None:
                p.kill()
                p.communicate()
        raise AssertionError(
            "at least one bootstrap process did not exit within "
            f"{timeout}s; killed the whole batch"
        ) from None
    return results


class TestConcurrentBootstrap:
    @pytest.mark.timeout(PER_TEST_TIMEOUT_SECONDS)
    def test_parallel_full_bootstrap_does_not_corrupt_venv(self, tmp_path):
        """Concurrent --full invocations against the same HOME must serialize
        venv creation under the install lock. With the previous design
        (rm -rf / python -m venv ran OUTSIDE the lock), two processes could
        both delete and recreate the venv, clobbering each other and leaving
        a corrupt environment.

        Deliberately NOT using a prebaked/cloned home: this test's entire
        point is racing --full processes against a HOME with NO pre-existing
        venv, to prove the install lock (not a warm stamp fast path) is what
        keeps them from corrupting each other."""
        home = tmp_path / "home"
        home.mkdir()

        env = os.environ.copy()
        env["HOME"] = str(home)
        env["PLUGIN_ROOT"] = str(REPO_ROOT)

        procs = [
            subprocess.Popen(
                ["bash", str(BOOTSTRAP_SH), "--full"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(REPO_ROOT),
            )
            for _ in range(CONCURRENT_PROCESSES)
        ]
        results = _wait_all_with_cleanup(procs, PROCESS_WAIT_TIMEOUT_SECONDS)

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
