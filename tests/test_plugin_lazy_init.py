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
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

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


class PrebakedHome(NamedTuple):
    """The home Path and the run_hook() result of the one real
    session_start bootstrap this module performs."""

    home: Path
    result: subprocess.CompletedProcess


@pytest.fixture(scope="module")
def prebaked_session_start_home(tmp_path_factory) -> PrebakedHome:
    """One real `hook.sh session_start` run (which internally calls
    bootstrap_full -- real venv creation + real `pip install`, no mocking)
    shared, read-only, across every test in this module that just needs
    the outcome of a completed lazy-init.

    Issue #144 root cause: hook.sh's session_start path ALWAYS calls
    bootstrap_full (`if [ "$HOOK_TYPE" = "session_start" ] || ...`), so
    this file's ~14 tests each running `run_hook(fresh_home,
    "session_start")` on a brand-new HOME meant ~14 independent real
    venv+pip installs (~20-90s each depending on machine load) -- a
    partial run under heavy load didn't finish 15 tests in ~590s despite
    all of them passing.

    Tests that only need to OBSERVE the outcome of a fresh lazy-init use
    this fixture directly. Tests that need a HOME with SPECIFIC
    pre-existing content (a custom config.json, a stale CLI symlink) clone
    this directory via `_clone_home` first -- session_start still runs
    for real on the clone, but `_ensure_venv_and_deps`'s stamp-match fast
    path (see bootstrap-plugin.sh) makes that second real run near-instant
    instead of paying for another full pip install. NOT every test can use
    this shortcut -- see test_hook_logs_error_when_python_fails below for
    the one case where a pre-seeded working venv would silently defeat the
    behavior under test.
    """
    home = tmp_path_factory.mktemp("prebaked_home")
    result = run_hook(home, "session_start")
    assert result.returncode == 0, f"stdout={result.stdout} stderr={result.stderr}"
    assert (home / ".claude-pace-maker" / ".bootstrap_ok").exists(), (
        f"prebaked session_start must complete a full bootstrap. "
        f"stdout={result.stdout} stderr={result.stderr}"
    )
    return PrebakedHome(home, result)


def _clone_home(prebaked_home: Path, tmp_path: Path) -> Path:
    """Copy a prebaked, fully-bootstrapped HOME tree into this test's own
    tmp_path so it can be mutated (overlay a custom config, replace the
    CLI symlink) without paying for a fresh venv creation + pip install on
    the next `run_hook` call."""
    dest = tmp_path / "home"
    shutil.copytree(prebaked_home, dest, symlinks=True)
    return dest


# ---------------------------------------------------------------------------
# Scenario 1: Fresh plugin installation bootstraps everything (lazy-init)
# ---------------------------------------------------------------------------


@pytest.mark.timeout(90)
class TestScenario1LazyInit:
    """hook.sh creates ~/.claude-pace-maker/ and config when it doesn't exist.

    All assertions below are read-only checks against the SAME real
    session_start outcome (prebaked_session_start_home, module-scoped) --
    none of these tests mutate the shared home, so sharing one real
    bootstrap across all seven is safe (issue #144)."""

    def test_lazy_init_creates_config_dir(self, prebaked_session_start_home):
        """SessionStart hook creates ~/.claude-pace-maker/ when missing."""
        home, result = prebaked_session_start_home
        pacemaker_dir = home / ".claude-pace-maker"
        assert pacemaker_dir.exists(), (
            f"~/.claude-pace-maker must be created by lazy-init. "
            f"stdout={result.stdout} stderr={result.stderr}"
        )

    def test_lazy_init_creates_config_json_with_defaults(
        self, prebaked_session_start_home
    ):
        """Lazy-init creates config.json with production defaults."""
        home, result = prebaked_session_start_home
        config_file = home / ".claude-pace-maker" / "config.json"
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

    def test_lazy_init_copies_source_code_extensions(self, prebaked_session_start_home):
        """Lazy-init copies source_code_extensions.json to ~/.claude-pace-maker/."""
        home, _result = prebaked_session_start_home
        extensions_file = home / ".claude-pace-maker" / "source_code_extensions.json"
        assert (
            extensions_file.exists()
        ), "source_code_extensions.json must be copied by lazy-init"
        with open(extensions_file) as f:
            data = json.load(f)
        assert (
            "extensions" in data
        ), "Copied file must be valid JSON with 'extensions' key"

    def test_lazy_init_creates_cli_symlink(self, prebaked_session_start_home):
        """Lazy-init creates pace-maker symlink in ~/.local/bin/."""
        home, _result = prebaked_session_start_home
        symlink = home / ".local" / "bin" / "pace-maker"
        assert (
            symlink.exists() or symlink.is_symlink()
        ), "pace-maker must be symlinked to ~/.local/bin/pace-maker by lazy-init"

    def test_lazy_init_links_pacemaker_package(self, prebaked_session_start_home):
        """SessionStart full bootstrap symlinks pacemaker package for CLI imports."""
        home, _result = prebaked_session_start_home
        pkg_link = home / ".claude-pace-maker" / "pacemaker"
        assert pkg_link.is_symlink() or (
            pkg_link.is_dir() and (pkg_link / "user_commands.py").exists()
        ), "pacemaker package must be linked or present for CLI"
        if pkg_link.is_symlink():
            target = os.readlink(str(pkg_link))
            assert "pacemaker" in target

    def test_session_start_writes_bootstrap_ok(self, prebaked_session_start_home):
        """SessionStart full bootstrap writes .bootstrap_ok marker."""
        home, _result = prebaked_session_start_home
        marker = home / ".claude-pace-maker" / ".bootstrap_ok"
        assert marker.exists(), ".bootstrap_ok must be created on session_start"

    def test_hook_exits_zero_after_lazy_init(self, prebaked_session_start_home):
        """hook.sh completes successfully (exit 0) after lazy-init."""
        _home, result = prebaked_session_start_home
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
    def home_with_existing_config(self, tmp_path, prebaked_session_start_home):
        """A cloned, already-bootstrapped home with a pre-existing
        customized config.json overlaid on top -- clone (not a from-empty
        HOME) so the run_hook() call each test makes below re-verifies
        this behavior via a REAL session_start invocation while hitting
        bootstrap_full's fast, already-satisfied-stamp path (issue #144)."""
        home = _clone_home(prebaked_session_start_home.home, tmp_path)
        pacemaker_dir = home / ".claude-pace-maker"
        # Write custom config with user-specific values, overwriting the
        # prebaked default.
        custom_config = {
            "enabled": True,
            "langfuse_enabled": True,
            "langfuse_host": "https://custom.langfuse.example.com",
            "custom_user_key": "user-specific-value",
        }
        with open(pacemaker_dir / "config.json", "w") as f:
            json.dump(custom_config, f)
        # Write custom extensions, overwriting the prebaked default.
        custom_extensions = {"extensions": [".custom"]}
        with open(pacemaker_dir / "source_code_extensions.json", "w") as f:
            json.dump(custom_extensions, f)
        return home

    @pytest.mark.timeout(30)
    def test_existing_config_is_not_overwritten(self, home_with_existing_config):
        """Lazy-init must NOT overwrite existing config.json."""
        home = home_with_existing_config
        result = run_hook(home, "session_start")
        assert result.returncode == 0, result.stderr
        config_file = home / ".claude-pace-maker" / "config.json"
        with open(config_file) as f:
            config = json.load(f)
        assert (
            config.get("langfuse_host") == "https://custom.langfuse.example.com"
        ), "Lazy-init must NOT overwrite existing config.json"
        assert (
            config.get("custom_user_key") == "user-specific-value"
        ), "User customizations must be preserved"

    @pytest.mark.timeout(30)
    def test_existing_extensions_not_overwritten(self, home_with_existing_config):
        """Lazy-init must NOT overwrite existing source_code_extensions.json."""
        home = home_with_existing_config
        result = run_hook(home, "session_start")
        assert result.returncode == 0, result.stderr
        extensions_file = home / ".claude-pace-maker" / "source_code_extensions.json"
        with open(extensions_file) as f:
            data = json.load(f)
        assert data.get("extensions") == [
            ".custom"
        ], "Existing source_code_extensions.json must NOT be overwritten"

    @pytest.mark.timeout(30)
    def test_cli_symlink_updated_on_repeated_run(self, home_with_existing_config):
        """CLI symlink is updated to current plugin root on each hook run."""
        home = home_with_existing_config
        local_bin = home / ".local" / "bin"
        local_bin.mkdir(parents=True, exist_ok=True)
        # Replace the (already-correct, from cloning the prebaked home) CLI
        # symlink with a stale one pointing to the wrong location.
        stale_symlink = local_bin / "pace-maker"
        if stale_symlink.exists() or stale_symlink.is_symlink():
            stale_symlink.unlink()
        stale_symlink.symlink_to("/tmp/old-plugin-root/scripts/pace-maker")

        result = run_hook(home, "session_start")
        assert result.returncode == 0, result.stderr

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

    @pytest.mark.timeout(30)
    def test_hook_exits_zero_when_python_fails(
        self, tmp_path, prebaked_session_start_home
    ):
        """hook.sh must exit 0 (graceful) even when Python module execution
        fails.

        Cloning the prebaked home is safe here because the ONLY assertion
        is `returncode == 0`, and hook.sh's final line is an unconditional
        `exit 0` regardless of whether the inner pacemaker.hook invocation
        succeeded or failed -- so this assertion holds identically whether
        the fake python3 shim actually gets reached or the pre-seeded venv
        (from the clone) short-circuits past it. See
        test_hook_logs_error_when_python_fails below for the DIFFERENT
        test where cloning would NOT be safe."""
        home = _clone_home(prebaked_session_start_home.home, tmp_path)
        pacemaker_dir = home / ".claude-pace-maker"
        # Write a valid config so we get past the enabled check (overwrites
        # the prebaked default -- keeps the original test's exact config).
        config = {
            "enabled": True,
            "log_level": 2,
            "langfuse_enabled": False,
            "intent_validation_enabled": False,
            "tdd_enabled": False,
        }
        with open(pacemaker_dir / "config.json", "w") as f:
            json.dump(config, f)

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

    @pytest.mark.timeout(30)
    def test_hook_logs_error_when_python_fails(self, tmp_path):
        """hook.sh logs an error to hook_debug.log when Python execution fails.

        Deliberately NOT cloning prebaked_session_start_home: this test's
        fake python3 shims cover every interpreter name resolve_python()
        tries on this box (python3.11, python3.10, bare python3), so the
        REAL failure path -- no working interpreter found anywhere --
        genuinely triggers, and hook.sh fails fast (resolve_python()
        aborts before any real venv/pip work, so this is already cheap on
        a fresh HOME). A pre-seeded working venv from a clone would let
        resolve_runtime_python()'s stamp-only fast check succeed WITHOUT
        ever invoking (or needing) any of these shims, silently defeating
        the failure this test exists to verify -- so a fresh HOME here is
        not just acceptable, it is required for correctness."""
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

        # Fake every interpreter name resolve_python() tries (python3.13
        # down to python3.10, plus bare python3) -- must be first on PATH.
        # A narrower list here previously missed a real python3.13 this
        # box has on PATH via ~/.local/bin, so resolve_python() found that
        # REAL interpreter and hook.sh attempted a genuine ~20-90s full
        # bootstrap instead of failing fast, blowing this test's timeout.
        fake_python_dir = tmp_path / "fake_python"
        fake_python_dir.mkdir()
        fake_body = (
            "#!/bin/bash\n"
            "echo 'ModuleNotFoundError: No module named requests' >&2\n"
            "exit 1\n"
        )
        for name in (
            "python3",
            "python3.10",
            "python3.11",
            "python3.12",
            "python3.13",
            "python3.14",
        ):
            fake_py = fake_python_dir / name
            fake_py.write_text(fake_body)
            fake_py.chmod(0o755)

        result = run_hook(
            home,
            "post_tool_use",
            extra_env={
                "PATH": f"{fake_python_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            },
        )
        # hook.sh always exits 0 regardless of the inner failure (graceful
        # degradation) -- this test's focus is the LOG content, not the
        # exit code, so `result` is captured but its returncode is not
        # separately asserted.
        assert result.returncode == 0

        debug_log = pacemaker_dir / "hook_debug.log"
        assert debug_log.exists(), (
            "hook_debug.log must exist -- resolve_python() fails on every "
            f"candidate here, so a fallback warning must be logged. "
            f"stdout={result.stdout} stderr={result.stderr}"
        )
        content = debug_log.read_text()
        assert len(content) > 0, "hook_debug.log must contain error output"


# ---------------------------------------------------------------------------
# Fallback-to-system-python visibility (no more silent degradation)
# ---------------------------------------------------------------------------


class TestHookLogsFallbackToSystemPython:
    """When the managed venv is unavailable, hook.sh must log a clear warning
    naming the fallback interpreter and pointing the user at `pace-maker
    doctor`. Previously the fallback was completely silent."""

    @staticmethod
    def _seed_failed_bootstrap(home, tmp_path):
        """Create state where bootstrap_full fails because no Python 3.10+
        is on PATH. Shims for all python3 variants intercept the version
        check that resolve_python() uses and exit 1, so bootstrap aborts
        at 'Python 3.10+ not found' and resolve_hook_python falls back.

        This forces resolve_python() to fail on every invocation
        regardless of any pre-existing venv, so these tests are already
        fast (no real bootstrap is ever attempted) -- no prebaked-home
        cloning needed for the first two tests below."""
        pacemaker_dir = home / ".claude-pace-maker"
        pacemaker_dir.mkdir(exist_ok=True)
        with open(pacemaker_dir / "config.json", "w") as f:
            json.dump({"enabled": True, "log_level": 2}, f)
        import shutil as _shutil

        real_py = _shutil.which("python3") or "/usr/bin/python3"
        fake_bin = tmp_path / "fake_python_bin"
        fake_bin.mkdir(exist_ok=True)
        shim_script = (
            "#!/bin/bash\n"
            'for arg in "$@"; do\n'
            '    case "$arg" in\n'
            "        *sys.version_info*) exit 1 ;;\n"
            "    esac\n"
            "done\n"
            f'exec {real_py} "$@"\n'
        )
        for name in (
            "python3",
            "python3.10",
            "python3.11",
            "python3.12",
            "python3.13",
            "python3.14",
        ):
            shim = fake_bin / name
            shim.write_text(shim_script)
            shim.chmod(0o755)
        return pacemaker_dir, fake_bin

    @pytest.mark.timeout(30)
    def test_warning_emitted_when_venv_missing(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        pacemaker_dir, fake_bin = self._seed_failed_bootstrap(home, tmp_path)
        result = run_hook(
            home,
            "post_tool_use",
            input_data="{}",
            extra_env={
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            },
        )
        assert result.returncode == 0, result.stderr

        debug_log = pacemaker_dir / "hook_debug.log"
        assert debug_log.exists(), "hook_debug.log should exist after first hook"
        content = debug_log.read_text()
        assert (
            "managed venv" in content and "unavailable" in content
        ), f"hook.sh should warn about unavailable venv; got log:\n{content}"
        assert (
            "pace-maker doctor" in content
        ), f"fallback log should point user at `pace-maker doctor`; got:\n{content}"
        assert (pacemaker_dir / ".python_fallback_warn").exists()

    @pytest.mark.timeout(30)
    def test_warning_is_throttled_within_one_hour(self, tmp_path):
        """Hooks fire dozens of times per session; the fallback warning is
        throttled to once per hour via a marker file so the log stays
        readable."""
        home = tmp_path / "home"
        home.mkdir()
        pacemaker_dir, fake_bin = self._seed_failed_bootstrap(home, tmp_path)

        for _ in range(3):
            result = run_hook(
                home,
                "post_tool_use",
                input_data="{}",
                extra_env={
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                },
            )
            assert result.returncode == 0, result.stderr

        debug_log = pacemaker_dir / "hook_debug.log"
        content = debug_log.read_text()
        assert content.count("managed venv at") == 1, (
            f"fallback warning must be throttled to once per hour; got "
            f"{content.count('managed venv at')} occurrences:\n{content}"
        )

    @pytest.mark.timeout(30)
    def test_marker_cleared_after_successful_bootstrap(
        self, tmp_path, prebaked_session_start_home
    ):
        """bootstrap_full clears the throttle marker so a venv that breaks
        AFTER recovery re-warns the user instead of staying silent.

        Clones the prebaked home (issue #144) so this real
        bootstrap-plugin.sh --full call hits the fast, already-satisfied
        stamp path instead of paying for another real venv + pip install."""
        home = _clone_home(prebaked_session_start_home.home, tmp_path)
        pacemaker_dir = home / ".claude-pace-maker"
        marker = pacemaker_dir / ".python_fallback_warn"
        marker.touch()
        assert marker.exists()

        # Run real bootstrap_full (fast path here; clears marker on success).
        result = subprocess.run(
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
