"""
Tests for the shell-level enabled guard in scripts/hook.sh and src/hooks/*.sh.

Regression coverage for the jq '//' operator bug introduced in v2.32.2:
  jq '.enabled // true' returns "true" when config has "enabled": false
  because jq's alternative operator treats false the same as null.

The correct form is:
  jq 'if has("enabled") then .enabled else true end'

Strategy: real subprocess calls against actual shell scripts with a
controlled $HOME, a sentinel fake-python that records whether it was
invoked, and a config.json whose 'enabled' field is varied per test.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

# snap-confined jq cannot access /tmp — create test homes under the real
# home directory so jq can read config files in its confined filesystem view.
_REAL_HOME = Path.home()


@pytest.fixture
def tmp_home(tmp_path_factory):
    """Temp directory under the real home so snap jq can read files in it."""
    base = _REAL_HOME / ".pytest-hook-tests"
    base.mkdir(parents=True, exist_ok=True)
    d = tempfile.mkdtemp(dir=base)
    yield Path(d)
    import shutil

    shutil.rmtree(d, ignore_errors=True)


REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_HOOK_SH = REPO_ROOT / "scripts" / "hook.sh"
SRC_HOOKS_DIR = REPO_ROOT / "src" / "hooks"
SRC_HOOK_SCRIPTS = list(SRC_HOOKS_DIR.glob("*.sh"))

# Hook types accepted by scripts/hook.sh
HOOK_TYPES = [
    "pre_tool_use",
    "post_tool_use",
    "session_start",
    "stop",
    "subagent_start",
    "subagent_stop",
    "user_prompt_submit",
]


def _make_env(home: Path, extra: dict | None = None) -> dict:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT)
    # Suppress bootstrap_light symlink noise in test output
    env["BOOTSTRAP_VERBOSE"] = "0"
    if extra:
        env.update(extra)
    return env


def _write_config(home: Path, config: dict) -> Path:
    pacemaker_dir = home / ".claude-pace-maker"
    pacemaker_dir.mkdir(parents=True, exist_ok=True)
    config_file = pacemaker_dir / "config.json"
    config_file.write_text(json.dumps(config))
    return config_file


def _write_sentinel_python(bin_dir: Path) -> Path:
    """Write fake python interpreters that record being called, then exit 1.

    find_python() in the hooks iterates `python3.11 python3.10 python3` and
    picks the first on PATH, so the sentinel must shadow ALL of them — otherwise
    a real python3.11/3.10 on the host is selected ahead of a bare python3
    sentinel, producing host-dependent false failures.
    """
    sentinel = bin_dir.parent / ".python_was_called"
    for name in ("python3.11", "python3.10", "python3"):
        fake_py = bin_dir / name
        fake_py.write_text(f"#!/bin/bash\ntouch {sentinel}\nexit 1\n")
        fake_py.chmod(0o755)
    return sentinel


def _run_plugin_hook(hook_type: str, home: Path, extra_env: dict | None = None):
    env = _make_env(home, extra_env)
    return subprocess.run(
        ["bash", str(PLUGIN_HOOK_SH), hook_type],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


def _run_src_hook(script: Path, home: Path, extra_env: dict | None = None):
    env = _make_env(home, extra_env)
    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


# ---------------------------------------------------------------------------
# scripts/hook.sh — plugin entry point
# ---------------------------------------------------------------------------


class TestPluginHookShEnabledGuard:
    """scripts/hook.sh exits immediately when config has enabled: false."""

    @pytest.fixture
    def home_disabled(self, tmp_home):
        _write_config(tmp_home, {"enabled": False})
        return tmp_home

    @pytest.fixture
    def home_enabled(self, tmp_home):
        _write_config(tmp_home, {"enabled": True})
        return tmp_home

    @pytest.fixture
    def home_no_key(self, tmp_home):
        _write_config(tmp_home, {"log_level": 2})  # no 'enabled' key
        return tmp_home

    @pytest.mark.parametrize("hook_type", HOOK_TYPES)
    def test_exits_zero_when_disabled(self, home_disabled, hook_type):
        """Hook must exit 0 immediately when enabled: false."""
        result = _run_plugin_hook(hook_type, home_disabled)
        assert result.returncode == 0, (
            f"hook.sh {hook_type} must exit 0 when disabled. "
            f"stderr={result.stderr[:300]}"
        )

    @pytest.mark.parametrize("hook_type", HOOK_TYPES)
    def test_does_not_invoke_python_when_disabled(
        self, home_disabled, tmp_home, hook_type
    ):
        """Python must not be invoked when enabled: false."""
        fake_bin = tmp_home / "fake_bin"
        fake_bin.mkdir()
        sentinel = _write_sentinel_python(fake_bin)

        env = {"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"}
        _run_plugin_hook(hook_type, home_disabled, extra_env=env)

        assert not sentinel.exists(), (
            f"hook.sh {hook_type} must not invoke Python when enabled: false. "
            f"Sentinel file was created at {sentinel}"
        )

    # Note: testing "missing key defaults to enabled" for scripts/hook.sh is not
    # practical — any hook type triggers bootstrap_full (pip install) when no
    # .bootstrap_ok exists in the test HOME, causing multi-second timeouts.
    # The equivalent behavior is already covered by
    # TestSrcHooksEnabledGuard::test_missing_enabled_key_defaults_to_enabled.


# ---------------------------------------------------------------------------
# src/hooks/*.sh — individual hook scripts
# ---------------------------------------------------------------------------


class TestSrcHooksEnabledGuard:
    """src/hooks/*.sh scripts exit immediately when config has enabled: false."""

    @pytest.fixture
    def home_disabled(self, tmp_home):
        _write_config(tmp_home, {"enabled": False})
        return tmp_home

    @pytest.fixture
    def home_no_key(self, tmp_home):
        _write_config(tmp_home, {"log_level": 2})
        return tmp_home

    @pytest.mark.parametrize("script", SRC_HOOK_SCRIPTS, ids=lambda s: s.name)
    def test_exits_zero_when_disabled(self, home_disabled, script):
        """Each src/hooks/*.sh must exit 0 immediately when enabled: false."""
        result = _run_src_hook(script, home_disabled)
        assert (
            result.returncode == 0
        ), f"{script.name} must exit 0 when disabled. stderr={result.stderr[:300]}"

    @pytest.mark.parametrize("script", SRC_HOOK_SCRIPTS, ids=lambda s: s.name)
    def test_does_not_invoke_python_when_disabled(
        self, home_disabled, tmp_home, script
    ):
        """Python must not be invoked by src/hooks/*.sh when enabled: false."""
        fake_bin = tmp_home / f"fake_bin_{script.stem}"
        fake_bin.mkdir(exist_ok=True)
        sentinel = _write_sentinel_python(fake_bin)

        env = {"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"}
        _run_src_hook(script, home_disabled, extra_env=env)

        assert not sentinel.exists(), (
            f"{script.name} must not invoke Python when enabled: false. "
            f"Sentinel file was created at {sentinel}"
        )

    @pytest.mark.parametrize("script", SRC_HOOK_SCRIPTS, ids=lambda s: s.name)
    def test_missing_enabled_key_defaults_to_enabled(
        self, home_no_key, tmp_home, script
    ):
        """Missing 'enabled' key should default to true in src/hooks/*.sh."""
        fake_bin = tmp_home / f"fake_bin2_{script.stem}"
        fake_bin.mkdir(exist_ok=True)
        sentinel = _write_sentinel_python(fake_bin)

        _run_src_hook(
            script,
            home_no_key,
            extra_env={"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"},
        )

        assert sentinel.exists(), (
            f"{script.name}: when 'enabled' key is absent, hook must proceed and invoke Python. "
            f"Sentinel was NOT created — hook short-circuited unexpectedly."
        )
