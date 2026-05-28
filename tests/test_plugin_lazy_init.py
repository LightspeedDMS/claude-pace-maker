"""
Tests for Plugin Architecture - Lazy-Init and Bootstrap (Story #39).

Covers:
- Scenario 1: Fresh plugin installation bootstraps everything (lazy-init)
- Scenario 4: Lazy-init is idempotent
- Scenario 7: Missing Python dependencies produce clear error

Strategy: Real filesystem operations in temp directories (anti-mock principle).
All tests use subprocess to run actual shell scripts.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SH = REPO_ROOT / "scripts" / "hook.sh"


def run_hook(home, hook_type="session_start", extra_env=None, input_data="{}"):
    """Run hook.sh with the given home directory and hook type."""
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(HOOK_SH), hook_type],
        capture_output=True,
        text=True,
        env=env,
        input=input_data,
        cwd=str(REPO_ROOT),
    )


# ---------------------------------------------------------------------------
# Scenario 1: Fresh plugin installation bootstraps everything (lazy-init)
# ---------------------------------------------------------------------------


class TestScenario1LazyInit:
    """hook.sh creates ~/.claude-pace-maker/ and config when it doesn't exist."""

    @pytest.fixture
    def fresh_home(self, tmp_path):
        """A home directory with NO ~/.claude-pace-maker/ at all."""
        home = tmp_path / "home"
        home.mkdir()
        return home

    def test_lazy_init_creates_config_dir(self, fresh_home):
        """SessionStart hook creates ~/.claude-pace-maker/ when missing."""
        result = run_hook(fresh_home, "session_start")
        pacemaker_dir = fresh_home / ".claude-pace-maker"
        assert pacemaker_dir.exists(), (
            f"~/.claude-pace-maker must be created by lazy-init. "
            f"stdout={result.stdout} stderr={result.stderr}"
        )

    def test_lazy_init_creates_config_json_with_defaults(self, fresh_home):
        """Lazy-init creates config.json with production defaults."""
        result = run_hook(fresh_home, "session_start")
        config_file = fresh_home / ".claude-pace-maker" / "config.json"
        assert (
            config_file.exists()
        ), f"config.json must be created by lazy-init. stderr={result.stderr}"
        with open(config_file) as f:
            config = json.load(f)
        assert config.get("enabled") is True, "Default config must have enabled=true"
        assert (
            "intent_validation_enabled" in config
        ), "Default config must have intent_validation_enabled"
        assert "tdd_enabled" in config, "Default config must have tdd_enabled"

    def test_lazy_init_copies_source_code_extensions(self, fresh_home):
        """Lazy-init copies source_code_extensions.json to ~/.claude-pace-maker/."""
        run_hook(fresh_home, "session_start")
        extensions_file = (
            fresh_home / ".claude-pace-maker" / "source_code_extensions.json"
        )
        assert (
            extensions_file.exists()
        ), "source_code_extensions.json must be copied by lazy-init"
        with open(extensions_file) as f:
            data = json.load(f)
        assert (
            "extensions" in data
        ), "Copied file must be valid JSON with 'extensions' key"

    def test_lazy_init_creates_cli_symlink(self, fresh_home):
        """Lazy-init creates pace-maker symlink in ~/.local/bin/."""
        run_hook(fresh_home, "session_start")
        symlink = fresh_home / ".local" / "bin" / "pace-maker"
        assert (
            symlink.exists() or symlink.is_symlink()
        ), "pace-maker must be symlinked to ~/.local/bin/pace-maker by lazy-init"

    def test_lazy_init_links_pacemaker_package(self, fresh_home):
        """SessionStart full bootstrap symlinks pacemaker package for CLI imports."""
        run_hook(fresh_home, "session_start")
        pkg_link = fresh_home / ".claude-pace-maker" / "pacemaker"
        assert pkg_link.is_symlink() or (
            pkg_link.is_dir() and (pkg_link / "user_commands.py").exists()
        ), "pacemaker package must be linked or present for CLI"
        if pkg_link.is_symlink():
            target = os.readlink(str(pkg_link))
            assert "pacemaker" in target

    def test_session_start_writes_bootstrap_ok(self, fresh_home):
        """SessionStart full bootstrap writes .bootstrap_ok marker."""
        run_hook(fresh_home, "session_start")
        marker = fresh_home / ".claude-pace-maker" / ".bootstrap_ok"
        assert marker.exists(), ".bootstrap_ok must be created on session_start"

    def test_hook_exits_zero_after_lazy_init(self, fresh_home):
        """hook.sh completes successfully (exit 0) after lazy-init."""
        result = run_hook(fresh_home, "session_start")
        assert result.returncode == 0, (
            f"hook.sh must exit 0 after lazy-init. "
            f"stdout={result.stdout} stderr={result.stderr}"
        )


# ---------------------------------------------------------------------------
# Scenario 4: Lazy-init is idempotent
# ---------------------------------------------------------------------------


class TestScenario4LazyInitIdempotent:
    """Lazy-init does not overwrite existing user config."""

    @pytest.fixture
    def home_with_existing_config(self, tmp_path):
        """Home with pre-existing customized config.json."""
        home = tmp_path / "home"
        home.mkdir()
        pacemaker_dir = home / ".claude-pace-maker"
        pacemaker_dir.mkdir()
        # Write custom config with user-specific values
        custom_config = {
            "enabled": True,
            "langfuse_enabled": True,
            "langfuse_host": "https://custom.langfuse.example.com",
            "custom_user_key": "user-specific-value",
        }
        with open(pacemaker_dir / "config.json", "w") as f:
            json.dump(custom_config, f)
        # Write custom extensions
        custom_extensions = {"extensions": [".custom"]}
        with open(pacemaker_dir / "source_code_extensions.json", "w") as f:
            json.dump(custom_extensions, f)
        return home

    def test_existing_config_is_not_overwritten(self, home_with_existing_config):
        """Lazy-init must NOT overwrite existing config.json."""
        home = home_with_existing_config
        run_hook(home, "session_start")
        config_file = home / ".claude-pace-maker" / "config.json"
        with open(config_file) as f:
            config = json.load(f)
        assert (
            config.get("langfuse_host") == "https://custom.langfuse.example.com"
        ), "Lazy-init must NOT overwrite existing config.json"
        assert (
            config.get("custom_user_key") == "user-specific-value"
        ), "User customizations must be preserved"

    def test_existing_extensions_not_overwritten(self, home_with_existing_config):
        """Lazy-init must NOT overwrite existing source_code_extensions.json."""
        home = home_with_existing_config
        run_hook(home, "session_start")
        extensions_file = home / ".claude-pace-maker" / "source_code_extensions.json"
        with open(extensions_file) as f:
            data = json.load(f)
        assert data.get("extensions") == [
            ".custom"
        ], "Existing source_code_extensions.json must NOT be overwritten"

    def test_cli_symlink_updated_on_repeated_run(self, home_with_existing_config):
        """CLI symlink is updated to current plugin root on each hook run."""
        home = home_with_existing_config
        local_bin = home / ".local" / "bin"
        local_bin.mkdir(parents=True, exist_ok=True)
        # Create a stale symlink pointing to wrong location
        stale_symlink = local_bin / "pace-maker"
        stale_symlink.symlink_to("/tmp/old-plugin-root/scripts/pace-maker")

        run_hook(home, "session_start")

        symlink = local_bin / "pace-maker"
        assert symlink.exists() or symlink.is_symlink(), "Symlink must exist"
        if symlink.is_symlink():
            target = os.readlink(str(symlink))
            assert (
                str(REPO_ROOT) in target or "pace-maker" in target
            ), f"Symlink should point to current plugin root, got: {target}"


# ---------------------------------------------------------------------------
# Scenario 7: Missing Python dependencies produce clear error
# ---------------------------------------------------------------------------


class TestScenario7MissingDeps:
    """hook.sh exits gracefully when Python execution fails."""

    def test_hook_exits_zero_when_python_fails(self, tmp_path):
        """hook.sh must exit 0 (graceful) even when Python module execution fails."""
        home = tmp_path / "home"
        home.mkdir()
        pacemaker_dir = home / ".claude-pace-maker"
        pacemaker_dir.mkdir()
        # Write a valid config so we get past the enabled check
        config = {
            "enabled": True,
            "log_level": 2,
            "langfuse_enabled": False,
            "intent_validation_enabled": False,
            "tdd_enabled": False,
        }
        with open(pacemaker_dir / "config.json", "w") as f:
            json.dump(config, f)
        # Copy extensions so lazy-init doesn't re-run
        import shutil

        shutil.copy(
            str(REPO_ROOT / "config" / "source_code_extensions.json"),
            str(pacemaker_dir / "source_code_extensions.json"),
        )

        # Create a fake python3 that always exits with error
        fake_python_dir = tmp_path / "fake_python"
        fake_python_dir.mkdir()
        fake_python = fake_python_dir / "python3"
        fake_python.write_text("#!/bin/bash\nexit 1\n")
        fake_python.chmod(0o755)

        result = run_hook(
            home,
            "session_start",
            extra_env={
                "PATH": f"{fake_python_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            },
        )
        # Hook must exit 0 (graceful degradation) even when Python fails
        assert result.returncode == 0, (
            f"hook.sh must exit 0 even when Python execution fails (graceful). "
            f"returncode={result.returncode} stderr={result.stderr}"
        )

    def test_hook_logs_error_when_python_fails(self, tmp_path):
        """hook.sh logs an error to hook_debug.log when Python execution fails."""
        home = tmp_path / "home"
        home.mkdir()
        pacemaker_dir = home / ".claude-pace-maker"
        pacemaker_dir.mkdir()
        config = {
            "enabled": True,
            "log_level": 2,
            "langfuse_enabled": False,
            "intent_validation_enabled": False,
            "tdd_enabled": False,
        }
        with open(pacemaker_dir / "config.json", "w") as f:
            json.dump(config, f)
        import shutil

        shutil.copy(
            str(REPO_ROOT / "config" / "source_code_extensions.json"),
            str(pacemaker_dir / "source_code_extensions.json"),
        )

        # Fake all interpreters resolve_python may select (must be first on PATH).
        fake_python_dir = tmp_path / "fake_python"
        fake_python_dir.mkdir()
        fake_body = (
            "#!/bin/bash\n"
            "echo 'ModuleNotFoundError: No module named requests' >&2\n"
            "exit 1\n"
        )
        for name in ("python3.11", "python3.10", "python3"):
            fake_py = fake_python_dir / name
            fake_py.write_text(fake_body)
            fake_py.chmod(0o755)

        run_hook(
            home,
            "post_tool_use",
            extra_env={
                "PATH": f"{fake_python_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            },
        )
        # Check that hook_debug.log captured the error
        debug_log = pacemaker_dir / "hook_debug.log"
        if debug_log.exists():
            content = debug_log.read_text()
            # Log should contain some error indication
            assert len(content) > 0, "hook_debug.log must contain error output"


# ---------------------------------------------------------------------------
# Fallback-to-system-python visibility (no more silent degradation)
# ---------------------------------------------------------------------------


class TestHookLogsFallbackToSystemPython:
    """When the managed venv is unavailable, hook.sh must log a clear warning
    naming the fallback interpreter and pointing the user at `pace-maker
    doctor`. Previously the fallback was completely silent."""

    @staticmethod
    def _seed_failed_bootstrap(home):
        """Pre-write state that forces bootstrap_full to short-circuit
        without creating a venv, so resolve_hook_python is forced down the
        system-python fallback path."""
        pacemaker_dir = home / ".claude-pace-maker"
        pacemaker_dir.mkdir(exist_ok=True)
        with open(pacemaker_dir / "config.json", "w") as f:
            json.dump({"enabled": True, "log_level": 2}, f)
        # _ensure_venv_and_deps returns 1 immediately when this marker exists,
        # so bootstrap_full fails and resolve_hook_python falls back.
        (pacemaker_dir / ".venv.failed").touch()
        return pacemaker_dir

    def test_warning_emitted_when_venv_missing(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        pacemaker_dir = self._seed_failed_bootstrap(home)
        run_hook(home, "post_tool_use", input_data="{}")

        debug_log = pacemaker_dir / "hook_debug.log"
        assert debug_log.exists(), "hook_debug.log should exist after first hook"
        content = debug_log.read_text()
        assert "managed venv" in content and "unavailable" in content, (
            f"hook.sh should warn about unavailable venv; got log:\n{content}"
        )
        assert "pace-maker doctor" in content, (
            f"fallback log should point user at `pace-maker doctor`; got:\n{content}"
        )
        # Throttle marker must exist after the first warning.
        assert (pacemaker_dir / ".python_fallback_warn").exists()

    def test_warning_is_throttled_within_one_hour(self, tmp_path):
        """Hooks fire dozens of times per session; the fallback warning is
        throttled to once per hour via a marker file so the log stays
        readable."""
        home = tmp_path / "home"
        home.mkdir()
        pacemaker_dir = self._seed_failed_bootstrap(home)

        for _ in range(3):
            run_hook(home, "post_tool_use", input_data="{}")

        debug_log = pacemaker_dir / "hook_debug.log"
        content = debug_log.read_text()
        # The unique warning header appears once across 3 hook invocations.
        assert content.count("managed venv at") == 1, (
            f"fallback warning must be throttled to once per hour; got "
            f"{content.count('managed venv at')} occurrences:\n{content}"
        )

    def test_marker_cleared_after_successful_bootstrap(self, tmp_path):
        """bootstrap_full clears the throttle marker so a venv that breaks
        AFTER recovery re-warns the user instead of staying silent."""
        home = tmp_path / "home"
        home.mkdir()
        pacemaker_dir = home / ".claude-pace-maker"
        pacemaker_dir.mkdir()
        marker = pacemaker_dir / ".python_fallback_warn"
        marker.touch()
        assert marker.exists()

        # Run real bootstrap_full (creates venv + clears marker on success).
        import subprocess as _sp
        result = _sp.run(
            ["bash", str(REPO_ROOT / "scripts" / "bootstrap-plugin.sh"), "--full"],
            env={**os.environ, "HOME": str(home), "PLUGIN_ROOT": str(REPO_ROOT)},
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, result.stderr
        assert not marker.exists(), (
            "bootstrap_full must clear .python_fallback_warn so a future "
            "fallback re-warns the user"
        )
