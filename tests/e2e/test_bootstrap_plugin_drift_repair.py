"""
Dependency-drift-repair test for scripts/bootstrap-plugin.sh, split out of
tests/test_bootstrap_plugin.py into tests/e2e/ (issue #144 code-review
follow-up #2).

Why this lives in tests/e2e/, not the fast suite: this test does two real
`pip install` calls against a real managed venv (downgrade requests, then
let bootstrap_full repair it back to the pinned version) on top of a real
`bootstrap-plugin.sh --full` bootstrap -- real pip/network cost on every
run, the same class of "genuinely e2e-scale" work as the concurrency test.
Moving it out of tests/test_bootstrap_plugin.py buys margin for that file's
own <60s target without weakening coverage: everything else left there
only needs filesystem/stamp checks against the module's shared prebaked
fixture.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BOOTSTRAP_SH = REPO_ROOT / "scripts" / "bootstrap-plugin.sh"
REQUIREMENTS_TXT = REPO_ROOT / "requirements.txt"


def _parse_requirements():
    assert REQUIREMENTS_TXT.exists(), f"requirements.txt missing at {REQUIREMENTS_TXT}"
    specs = []
    for raw in REQUIREMENTS_TXT.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            specs.append(line)
    assert specs, f"no pinned specs parsed from {REQUIREMENTS_TXT}"
    return specs


PINNED_SPECS = _parse_requirements()
PINS = {}
for _spec in PINNED_SPECS:
    _name, _, _ver = _spec.partition("==")
    PINS[_name] = _ver


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


class TestBootstrapDriftRepair:
    @pytest.mark.timeout(90)
    def test_drifted_version_is_repaired_on_next_bootstrap(self, tmp_path):
        """If a dep is manually downgraded inside the venv, the next
        bootstrap_full must detect the drift via _deps_imports_ok's
        exact-version assertion (driven by requirements.txt) and
        re-install the pinned version via `pip install -r`."""
        home = tmp_path / "home"
        home.mkdir()
        first = run_bootstrap(home, "--full")
        assert first.returncode == 0, first.stderr

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
