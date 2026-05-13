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

import json
import sqlite3

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


# ── Config defaults ───────────────────────────────────────────────────────────


class TestConfigDefaultMinClaudeVersion:
    """Test that DEFAULT_CONFIG includes min_claude_version."""

    def test_default_config_has_min_claude_version(self):
        """DEFAULT_CONFIG should contain 'min_claude_version' key."""
        from pacemaker.constants import DEFAULT_CONFIG

        assert "min_claude_version" in DEFAULT_CONFIG

    def test_default_min_claude_version_value(self):
        """Default min_claude_version should be '2.1.39'."""
        from pacemaker.constants import DEFAULT_CONFIG

        assert DEFAULT_CONFIG["min_claude_version"] == "2.1.39"

    def test_default_min_claude_version_is_parseable(self):
        """Default min_claude_version string should be parseable."""
        from pacemaker.constants import DEFAULT_CONFIG
        from pacemaker.claude_code_version import ClaudeCodeVersion

        v = ClaudeCodeVersion.parse(DEFAULT_CONFIG["min_claude_version"])
        assert v is not None
        assert (v.major, v.minor, v.patch) == (2, 1, 39)


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


# ── CLI commands ──────────────────────────────────────────────────────────────


class TestMinClaudeVersionCli:
    """Tests for pace-maker min-claude-version CLI commands."""

    def test_parse_command_show_bare(self):
        """'pace-maker min-claude-version' parses to min-claude-version command."""
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker min-claude-version")
        assert result["is_pace_maker_command"] is True
        assert result["command"] == "min-claude-version"

    def test_parse_command_show_explicit(self):
        """'pace-maker min-claude-version show' parses correctly."""
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker min-claude-version show")
        assert result["is_pace_maker_command"] is True
        assert result["command"] == "min-claude-version"

    def test_parse_command_set_valid(self):
        """'pace-maker min-claude-version set 3.0.0' parses correctly."""
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker min-claude-version set 3.0.0")
        assert result["is_pace_maker_command"] is True
        assert result["command"] == "min-claude-version"
        assert "3.0.0" in (result.get("subcommand") or "")

    def test_execute_show_command(self, config_file):
        """execute_command show returns current configured minimum."""
        path = config_file({"min_claude_version": "2.1.39"})

        from pacemaker.user_commands import execute_command

        result = execute_command(
            "min-claude-version", config_path=path, subcommand="show"
        )
        assert result["success"] is True
        assert "2.1.39" in result["message"]

    def test_execute_set_valid_version(self, config_file):
        """execute_command set with valid version writes config."""
        path = config_file({})

        from pacemaker.user_commands import execute_command

        result = execute_command(
            "min-claude-version", config_path=path, subcommand="set 3.0.0"
        )
        assert result["success"] is True

        with open(path) as f:
            saved_config = json.load(f)
        assert saved_config["min_claude_version"] == "3.0.0"

    def test_execute_set_invalid_version(self, config_file):
        """execute_command set with invalid version fails gracefully."""
        path = config_file({})

        from pacemaker.user_commands import execute_command

        result = execute_command(
            "min-claude-version",
            config_path=path,
            subcommand="set not-a-version",
        )
        assert result["success"] is False
        assert (
            "invalid" in result["message"].lower()
            or "format" in result["message"].lower()
        )
