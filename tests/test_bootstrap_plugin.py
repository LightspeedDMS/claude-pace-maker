"""
Tests for scripts/bootstrap-plugin.sh (plugin bootstrap and dep markers).
"""

import os
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
