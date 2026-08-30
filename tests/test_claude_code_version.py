#!/usr/bin/env python3
"""
Unit tests for Claude Code minimum version check (Story #66).

Tests:
- ClaudeCodeVersion.parse() — happy path, empty, null, unparseable, tagged versions
- ClaudeCodeVersion.compare() — equal, greater, lesser at each level; 2.1.39 vs 2.1.126
- ClaudeCodeVersion.is_below() — boundary cases
- probe_installed_version() — success and failure paths
- Config default for min_claude_version
- CLI show and set commands — valid and invalid
- version_status_db — writer with all four reason types, idempotent overwrite
"""

import io
import json
import sqlite3
import sys

import pytest

# ── Shared helpers ────────────────────────────────────────────────────────────


def _make_version(major, minor, patch):
    """Construct a ClaudeCodeVersion with given numeric components."""
    from pacemaker.claude_code_version import ClaudeCodeVersion

    return ClaudeCodeVersion(major=major, minor=minor, patch=patch, raw="")


class _FakeSubprocessResult:
    """Minimal subprocess.CompletedProcess stand-in for test stubs."""

    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def version_db(tmp_path, monkeypatch):
    """Isolated version_status DB path with PACEMAKER_VERSION_STATUS_PATH set."""
    db_path = str(tmp_path / "version_status.db")
    monkeypatch.setenv("PACEMAKER_VERSION_STATUS_PATH", db_path)
    return db_path


@pytest.fixture
def config_file(tmp_path):
    """Factory fixture: call with a dict to write config.json, returns path."""

    def _factory(content=None):
        path = str(tmp_path / "config.json")
        with open(path, "w") as f:
            json.dump(content or {}, f)
        return path

    return _factory


# ── ClaudeCodeVersion.parse() ─────────────────────────────────────────────────


class TestClaudeCodeVersionParse:
    """Tests for ClaudeCodeVersion.parse() classmethod."""

    def test_parse_standard_version(self):
        """Parse standard 'X.Y.Z (Claude Code)' output."""
        from pacemaker.claude_code_version import ClaudeCodeVersion

        v = ClaudeCodeVersion.parse("2.1.126 (Claude Code)")
        assert v is not None
        assert v.major == 2
        assert v.minor == 1
        assert v.patch == 126
        assert v.raw == "2.1.126 (Claude Code)"

    def test_parse_bare_version(self):
        """Parse bare 'X.Y.Z' with no suffix."""
        from pacemaker.claude_code_version import ClaudeCodeVersion

        v = ClaudeCodeVersion.parse("3.0.0")
        assert v is not None
        assert v.major == 3
        assert v.minor == 0
        assert v.patch == 0

    def test_parse_prerelease_tagged_version(self):
        """Parse version with pre-release suffix like '2.1.39-beta.1'."""
        from pacemaker.claude_code_version import ClaudeCodeVersion

        v = ClaudeCodeVersion.parse("2.1.39-beta.1 (Claude Code)")
        assert v is not None
        assert (v.major, v.minor, v.patch) == (2, 1, 39)

    def test_parse_build_metadata_version(self):
        """Parse version with build metadata like '2.1.39+build.123'."""
        from pacemaker.claude_code_version import ClaudeCodeVersion

        v = ClaudeCodeVersion.parse("2.1.39+build.123")
        assert v is not None
        assert (v.major, v.minor, v.patch) == (2, 1, 39)

    def test_parse_empty_string_returns_none(self):
        """Empty string returns None."""
        from pacemaker.claude_code_version import ClaudeCodeVersion

        assert ClaudeCodeVersion.parse("") is None

    def test_parse_none_returns_none(self):
        """None input returns None."""
        from pacemaker.claude_code_version import ClaudeCodeVersion

        assert ClaudeCodeVersion.parse(None) is None

    def test_parse_whitespace_only_returns_none(self):
        """Whitespace-only string returns None."""
        from pacemaker.claude_code_version import ClaudeCodeVersion

        assert ClaudeCodeVersion.parse("   ") is None

    def test_parse_unparseable_text_returns_none(self):
        """Completely unparseable text returns None."""
        from pacemaker.claude_code_version import ClaudeCodeVersion

        assert ClaudeCodeVersion.parse("not-a-version-at-all") is None

    def test_parse_partial_version_returns_none(self):
        """Partial version like '2.1' (no patch) returns None."""
        from pacemaker.claude_code_version import ClaudeCodeVersion

        assert ClaudeCodeVersion.parse("2.1") is None

    def test_parse_alpha_in_numbers_returns_none(self):
        """Version with letters in numeric parts returns None."""
        from pacemaker.claude_code_version import ClaudeCodeVersion

        assert ClaudeCodeVersion.parse("2.1.abc") is None

    def test_parse_min_version_39(self):
        """Parse the minimum version 2.1.39 correctly."""
        from pacemaker.claude_code_version import ClaudeCodeVersion

        v = ClaudeCodeVersion.parse("2.1.39")
        assert v is not None
        assert (v.major, v.minor, v.patch) == (2, 1, 39)

    def test_parse_extracts_first_token(self):
        """Parse extracts first whitespace-separated token."""
        from pacemaker.claude_code_version import ClaudeCodeVersion

        v = ClaudeCodeVersion.parse("  2.1.50  (Claude Code) extra stuff")
        assert v is not None
        assert (v.major, v.minor, v.patch) == (2, 1, 50)


# ── ClaudeCodeVersion.compare() ──────────────────────────────────────────────


class TestClaudeCodeVersionCompare:
    """Tests for ClaudeCodeVersion.compare() method."""

    def test_compare_equal_versions(self):
        """Equal versions return 0."""
        assert _make_version(2, 1, 39).compare(_make_version(2, 1, 39)) == 0

    def test_compare_major_greater(self):
        """Greater major returns positive."""
        assert _make_version(3, 0, 0).compare(_make_version(2, 9, 9)) > 0

    def test_compare_major_lesser(self):
        """Lesser major returns negative."""
        assert _make_version(1, 0, 0).compare(_make_version(2, 0, 0)) < 0

    def test_compare_minor_greater(self):
        """Greater minor (same major) returns positive."""
        assert _make_version(2, 2, 0).compare(_make_version(2, 1, 99)) > 0

    def test_compare_minor_lesser(self):
        """Lesser minor (same major) returns negative."""
        assert _make_version(2, 0, 99).compare(_make_version(2, 1, 0)) < 0

    def test_compare_patch_greater(self):
        """Greater patch (same major.minor) returns positive."""
        assert _make_version(2, 1, 126).compare(_make_version(2, 1, 39)) > 0

    def test_compare_patch_lesser(self):
        """Lesser patch (same major.minor) returns negative."""
        assert _make_version(2, 1, 38).compare(_make_version(2, 1, 39)) < 0

    def test_compare_semver_not_lexicographic(self):
        """Critical: 2.1.126 > 2.1.39 (numeric, NOT lexicographic)."""
        v126 = _make_version(2, 1, 126)
        v39 = _make_version(2, 1, 39)
        assert v126.compare(v39) > 0
        assert v39.compare(v126) < 0


# ── ClaudeCodeVersion.is_below() ─────────────────────────────────────────────


class TestClaudeCodeVersionIsBelow:
    """Tests for ClaudeCodeVersion.is_below() method."""

    def test_is_below_when_clearly_below(self):
        """Version below minimum returns True."""
        assert _make_version(2, 1, 38).is_below(_make_version(2, 1, 39)) is True

    def test_is_below_when_equal(self):
        """Version equal to minimum is NOT below — returns False."""
        assert _make_version(2, 1, 39).is_below(_make_version(2, 1, 39)) is False

    def test_is_below_when_above(self):
        """Version above minimum returns False."""
        assert _make_version(2, 1, 126).is_below(_make_version(2, 1, 39)) is False

    def test_is_below_major_version(self):
        """Major version below minimum returns True."""
        assert _make_version(1, 99, 99).is_below(_make_version(2, 0, 0)) is True

    def test_is_below_boundary_patch(self):
        """Patch one below minimum returns True."""
        assert _make_version(2, 1, 38).is_below(_make_version(2, 1, 39)) is True


# ── probe_installed_version() ─────────────────────────────────────────────────


class TestProbeInstalledVersion:
    """Tests for probe_installed_version()."""

    def test_probe_returns_version_on_success(self, monkeypatch):
        """Successful probe returns ClaudeCodeVersion."""
        import subprocess
        from pacemaker.claude_code_version import probe_installed_version

        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _FakeSubprocessResult("2.1.126 (Claude Code)\n"),
        )
        result = probe_installed_version()
        assert result is not None
        assert (result.major, result.minor, result.patch) == (2, 1, 126)

    def test_probe_returns_none_on_file_not_found(self, monkeypatch):
        """FileNotFoundError (binary missing) returns None — fail open."""
        import subprocess
        from pacemaker.claude_code_version import probe_installed_version

        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: (_ for _ in ()).throw(
                FileNotFoundError("claude not found")
            ),
        )
        assert probe_installed_version() is None

    def test_probe_returns_none_on_timeout(self, monkeypatch):
        """TimeoutExpired returns None — fail open."""
        import subprocess
        from pacemaker.claude_code_version import probe_installed_version

        def _raise_timeout(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="claude", timeout=5)

        monkeypatch.setattr(subprocess, "run", _raise_timeout)
        assert probe_installed_version() is None

    def test_probe_returns_none_on_unparseable_output(self, monkeypatch):
        """Unparseable output returns None — fail open."""
        import subprocess
        from pacemaker.claude_code_version import probe_installed_version

        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _FakeSubprocessResult("some unexpected output\n"),
        )
        assert probe_installed_version() is None

    def test_probe_returns_none_on_nonzero_exit(self, monkeypatch):
        """Non-zero exit code returns None — fail open."""
        import subprocess
        from pacemaker.claude_code_version import probe_installed_version

        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _FakeSubprocessResult("", returncode=1),
        )
        assert probe_installed_version() is None


# ── version_status_db ─────────────────────────────────────────────────────────


class TestVersionStatusDb:
    """Tests for version_status_db module."""

    def test_record_and_read_ok_status(self, version_db):
        """record_status with reason='ok' stores row; read_status retrieves it."""
        from pacemaker.version_status_db import record_status, read_status

        record_status("2.1.126", "2.1.39", blocked=False, reason="ok")
        row = read_status()
        assert row is not None
        assert row["current_version"] == "2.1.126"
        assert row["min_version"] == "2.1.39"
        assert row["blocked"] == 0
        assert row["reason"] == "ok"

    def test_record_and_read_blocked_status(self, version_db):
        """record_status with blocked=True stores row correctly."""
        from pacemaker.version_status_db import record_status, read_status

        record_status("2.1.10", "2.1.39", blocked=True, reason="below_minimum")
        row = read_status()
        assert row["blocked"] == 1
        assert row["reason"] == "below_minimum"
        assert row["current_version"] == "2.1.10"

    def test_record_probe_failed_status(self, version_db):
        """record_status with reason='probe_failed' is stored correctly."""
        from pacemaker.version_status_db import record_status, read_status

        record_status(None, "2.1.39", blocked=False, reason="probe_failed")
        row = read_status()
        assert row["reason"] == "probe_failed"
        assert row["blocked"] == 0

    def test_record_parse_failed_status(self, version_db):
        """record_status with reason='parse_failed' is stored correctly."""
        from pacemaker.version_status_db import record_status, read_status

        record_status("garbled output", "2.1.39", blocked=False, reason="parse_failed")
        row = read_status()
        assert row["reason"] == "parse_failed"

    def test_record_status_is_idempotent_overwrite(self, version_db):
        """Second record_status call overwrites first (single-row upsert)."""
        from pacemaker.version_status_db import record_status, read_status

        record_status("2.1.39", "2.1.39", blocked=False, reason="ok")
        record_status("2.1.126", "2.1.39", blocked=False, reason="ok")
        row = read_status()
        assert row["current_version"] == "2.1.126"

        with sqlite3.connect(version_db) as conn:
            count = conn.execute("SELECT COUNT(*) FROM version_status").fetchone()[0]
        assert count == 1

    def test_read_status_returns_none_when_no_db(self, tmp_path, monkeypatch):
        """read_status returns None when DB file does not exist."""
        db_path = str(tmp_path / "nonexistent.db")
        monkeypatch.setenv("PACEMAKER_VERSION_STATUS_PATH", db_path)

        from pacemaker.version_status_db import read_status

        assert read_status() is None

    def test_record_status_creates_checked_at_timestamp(self, version_db):
        """record_status stores a non-zero checked_at timestamp."""
        import time
        from pacemaker.version_status_db import record_status, read_status

        before = time.time()
        record_status("2.1.126", "2.1.39", blocked=False, reason="ok")
        after = time.time()

        row = read_status()
        assert row["checked_at"] >= before
        assert row["checked_at"] <= after

    def test_resolve_db_path_raises_in_test_mode_without_env(self, monkeypatch):
        """resolve_db_path raises RuntimeError in test mode when path not set."""
        monkeypatch.delenv("PACEMAKER_VERSION_STATUS_PATH", raising=False)

        from pacemaker.version_status_db import resolve_db_path

        with pytest.raises(RuntimeError, match="PACEMAKER_VERSION_STATUS_PATH"):
            resolve_db_path()


class TestDefaultConfigMinClaudeVersion:
    """Issue #96: min_claude_version must be a real DEFAULT_CONFIG key, not only
    the private _FALLBACK_MIN_VERSION constant inside version_check.py — a fresh
    install (no config.json on disk) must ship with this key set explicitly."""

    def test_default_config_has_min_claude_version(self):
        from pacemaker.constants import DEFAULT_CONFIG

        assert DEFAULT_CONFIG["min_claude_version"] == "2.1.39"

    def test_fresh_load_config_includes_min_claude_version(self, tmp_path):
        """load_config() with no config.json on disk returns DEFAULT_CONFIG.copy(),
        which must include min_claude_version — proving the wiring end-to-end
        rather than just asserting the constant in isolation."""
        from pacemaker.hook import load_config

        missing_config_path = str(tmp_path / "does_not_exist.json")
        config = load_config(missing_config_path)
        assert config.get("min_claude_version") == "2.1.39"


# ── SessionStart hook wiring (real hook, not the bare function) ────────────────
#
# These tests live here (not in test_version_check_integration.py) because
# this project's mock-abuse clean-code rule forbids ANY mocking in files
# whose name matches "*_integration*" ("real systems only"), while unit
# tests may mock external dependencies. subprocess.run — the external
# `claude` CLI binary, not any pace-maker component — is the only thing
# stubbed below; run_session_start_hook(), perform_session_start_version_check(),
# ClaudeCodeVersion, state.json read/write, and version_status_db all run for
# real, unmocked.


def _stub_probe(version_str):
    """Return a subprocess.run stub yielding the given 'claude --version' output."""

    def _stub(*args, **kwargs):
        return _FakeSubprocessResult(stdout=version_str, returncode=0)

    return _stub


@pytest.fixture
def hook_wiring_env(tmp_path, monkeypatch):
    """Isolated pace-maker environment for exercising the real hook layer."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    pm_dir = fake_home / ".claude-pace-maker"
    pm_dir.mkdir()

    monkeypatch.setenv("HOME", str(fake_home))

    config_path = str(pm_dir / "config.json")
    state_path = str(pm_dir / "state.json")
    version_db_path = str(pm_dir / "version_status.db")

    config = {
        "enabled": True,
        "min_claude_version": "2.1.39",
        "intent_validation_enabled": False,
    }
    with open(config_path, "w") as f:
        json.dump(config, f)

    monkeypatch.setenv("PACEMAKER_VERSION_STATUS_PATH", version_db_path)

    return {"config_path": config_path, "state_path": state_path}


class TestSessionStartHookWiring:
    """Proves perform_session_start_version_check() is actually called from
    run_session_start_hook() — issue #96's core complaint was that the
    function was "called only from tests", never from the hook layer."""

    def _run_session_start(
        self, env, monkeypatch, probe_stub, source="startup", session_id="test-session"
    ):
        import subprocess
        import pacemaker.hook as hook_module

        monkeypatch.setattr(subprocess, "run", probe_stub)
        monkeypatch.setattr(hook_module, "DEFAULT_CONFIG_PATH", env["config_path"])
        monkeypatch.setattr(hook_module, "DEFAULT_STATE_PATH", env["state_path"])

        stderr_capture = io.StringIO()
        monkeypatch.setattr(sys, "stderr", stderr_capture)

        hook_input = json.dumps({"session_id": session_id, "source": source})
        monkeypatch.setattr(sys, "stdin", io.StringIO(hook_input))

        hook_module.run_session_start_hook()

        with open(env["state_path"]) as f:
            state = json.load(f)
        return state, stderr_capture.getvalue()

    def test_below_minimum_hard_blocks_via_real_session_start_hook(
        self, hook_wiring_env, monkeypatch
    ):
        """The real run_session_start_hook() — not the bare function — sets
        the block flag, writes the upgrade message to stderr, and persists
        status to version_status_db."""
        state, stderr = self._run_session_start(
            hook_wiring_env,
            monkeypatch,
            _stub_probe("2.1.10 (Claude Code)\n"),
        )

        assert state.get("version_block_active") is True
        assert "upgrade" in stderr.lower()
        assert "2.1.39" in stderr

        from pacemaker.version_status_db import read_status

        status = read_status()
        assert status is not None
        assert status["blocked"] == 1
        assert status["reason"] == "below_minimum"

    def test_at_or_above_minimum_does_not_block_via_real_session_start_hook(
        self, hook_wiring_env, monkeypatch
    ):
        """Version at/above minimum: run_session_start_hook() leaves the flag
        False and writes nothing to stderr."""
        state, stderr = self._run_session_start(
            hook_wiring_env,
            monkeypatch,
            _stub_probe("2.1.126 (Claude Code)\n"),
        )

        assert state.get("version_block_active") is False
        assert stderr == ""

        from pacemaker.version_status_db import read_status

        status = read_status()
        assert status is not None
        assert status["blocked"] == 0
        assert status["reason"] == "ok"

    def test_recovery_flag_clears_once_installed_version_meets_minimum(
        self, hook_wiring_env, monkeypatch
    ):
        """Two successive real run_session_start_hook() invocations on the
        same env: first call probes a below-minimum version and confirms the
        block flag is set; second call probes an at/above-minimum version
        and confirms the SAME session's flag has cleared — proving automatic
        recovery on the next SessionStart after an upgrade."""
        first_call_blocked_state, _ = self._run_session_start(
            hook_wiring_env,
            monkeypatch,
            _stub_probe("2.1.10 (Claude Code)\n"),
        )
        assert first_call_blocked_state.get("version_block_active") is True

        second_call_recovered_state, second_call_stderr = self._run_session_start(
            hook_wiring_env,
            monkeypatch,
            _stub_probe("2.1.126 (Claude Code)\n"),
        )
        assert second_call_recovered_state.get("version_block_active") is False
        assert second_call_stderr == ""

    # ── issue #100: SessionStart user-visible signal ────────────────────────
    # All three tests below call this class's own pre-existing _run_session_start()
    # helper (defined above) — no new mocking is introduced here.

    def test_below_minimum_prints_tagged_notice_to_stdout(
        self, hook_wiring_env, monkeypatch, capsys
    ):
        """A version-blocked session must produce a user/Claude-visible
        signal via SessionStart's additionalContext channel (plain stdout
        text — Claude Code treats it as additionalContext for SessionStart,
        and SessionStart cannot be blocked via exit code)."""
        from pacemaker.prompt_provenance import TAG_SEPARATOR

        state, _stderr = self._run_session_start(
            hook_wiring_env,
            monkeypatch,
            _stub_probe("2.1.10 (Claude Code)\n"),
        )
        captured = capsys.readouterr()

        assert state.get("version_block_active") is True
        expected_tag = f"[pace-maker {TAG_SEPARATOR} version_block_notice]"
        assert expected_tag in captured.out
        assert "2.1.10" in captured.out
        assert "2.1.39" in captured.out

    def test_at_or_above_minimum_has_no_version_block_notice(
        self, hook_wiring_env, monkeypatch, capsys
    ):
        """Normal session: no spurious notice. Checks the actual TAGGED
        emission, not the bare channel name — "version_block_notice"
        legitimately appears in every SessionStart manifest's bullet list
        (it is a declared channel in prompt_provenance.CHANNELS)
        regardless of block state."""
        from pacemaker.prompt_provenance import TAG_SEPARATOR

        state, _stderr = self._run_session_start(
            hook_wiring_env,
            monkeypatch,
            _stub_probe("2.1.126 (Claude Code)\n"),
        )
        captured = capsys.readouterr()

        assert state.get("version_block_active") is False
        expected_tag = f"[pace-maker {TAG_SEPARATOR} version_block_notice]"
        assert expected_tag not in captured.out

    def test_probe_failure_prints_no_notice_and_session_proceeds_cleanly(
        self, hook_wiring_env, monkeypatch, capsys
    ):
        """Fail-open: a probe failure (e.g. the 'claude' binary missing) must
        not block AND must not print a spurious version-block notice — the
        new notice-emission code path must not weaken the pre-existing
        fail-open guarantee (see TestSessionStartVersionCheck in
        tests/test_version_check_integration.py)."""
        from pacemaker.prompt_provenance import TAG_SEPARATOR

        def _raise_file_not_found(*args, **kwargs):
            raise FileNotFoundError("claude not found")

        state, _stderr = self._run_session_start(
            hook_wiring_env,
            monkeypatch,
            _raise_file_not_found,
        )
        captured = capsys.readouterr()

        assert state.get("version_block_active") is False
        expected_tag = f"[pace-maker {TAG_SEPARATOR} version_block_notice]"
        assert expected_tag not in captured.out


# ── Config override / fallback for min_claude_version ──────────────────────────


class TestConfigOverrideAndFallback:
    """min_claude_version set in config overrides the default; an absent key
    in the config dict falls back to _FALLBACK_MIN_VERSION (2.1.39)."""

    def test_config_min_claude_version_override_changes_block_decision(
        self, hook_wiring_env, monkeypatch
    ):
        """Overriding min_claude_version to a HIGHER value than default makes
        a version that would pass at the default minimum now get blocked —
        proving the override is actually read, not just present in config."""
        import subprocess
        from pacemaker.hook import load_state, save_state
        from pacemaker.version_check import perform_session_start_version_check

        monkeypatch.setattr(subprocess, "run", _stub_probe("2.5.0 (Claude Code)\n"))

        state = load_state(hook_wiring_env["state_path"])
        overridden_config = {
            "enabled": True,
            "min_claude_version": "3.0.0",
            "intent_validation_enabled": False,
        }
        stderr_capture = io.StringIO()

        perform_session_start_version_check(
            state, overridden_config, stderr=stderr_capture
        )
        save_state(state, hook_wiring_env["state_path"])

        assert state.get("version_block_active") is True
        assert "3.0.0" in stderr_capture.getvalue()

    def test_absent_min_claude_version_key_falls_back_to_fallback_constant(
        self, hook_wiring_env, monkeypatch
    ):
        """A config dict with NO min_claude_version key at all (e.g. an old
        config.json written before this feature existed) must fall back to
        _FALLBACK_MIN_VERSION (2.1.39) — proven by checking the boundary on
        both sides of that exact version."""
        import subprocess
        from pacemaker.hook import load_state, save_state
        from pacemaker.version_check import (
            _FALLBACK_MIN_VERSION,
            perform_session_start_version_check,
        )

        assert _FALLBACK_MIN_VERSION == "2.1.39"
        config_without_key = {"enabled": True, "intent_validation_enabled": False}

        # Below the fallback minimum -> blocked
        monkeypatch.setattr(subprocess, "run", _stub_probe("2.1.10 (Claude Code)\n"))
        state_below = load_state(hook_wiring_env["state_path"])
        perform_session_start_version_check(
            state_below, config_without_key, stderr=io.StringIO()
        )
        save_state(state_below, hook_wiring_env["state_path"])
        assert state_below.get("version_block_active") is True

        # At/above the fallback minimum -> not blocked
        monkeypatch.setattr(subprocess, "run", _stub_probe("2.1.39 (Claude Code)\n"))
        state_ok = load_state(hook_wiring_env["state_path"])
        perform_session_start_version_check(
            state_ok, config_without_key, stderr=io.StringIO()
        )
        save_state(state_ok, hook_wiring_env["state_path"])
        assert state_ok.get("version_block_active") is False
