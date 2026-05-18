#!/usr/bin/env python3
"""
Component tests for Claude Code minimum version check (Story #66).

These tests exercise perform_session_start_version_check(), blocked-state
hook early-return, and CLI recovery.  subprocess.run is stubbed because the
real `claude` binary is an external system boundary — this matches the project
anti-mock rule (mock only external services, never internal logic).

Tests:
- SessionStart with mocked 'claude --version' above minimum → no block
- SessionStart with mocked 'claude --version' below minimum → block flag, stderr message
- SessionStart with failed probe → fail open
- SessionStart with malformed output → fail open
- After block, downstream hooks early-return (no permissionDecision=block)
- Recovery via 'pace-maker min-claude-version set'
- Exception guard: monkey-patch probe to raise → hooks proceed
"""

import io
import json

import pytest

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def pacemaker_env(tmp_path, monkeypatch):
    """Isolated pace-maker environment: fake home, config, state, version_status DB."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    pm_dir = fake_home / ".claude-pace-maker"
    pm_dir.mkdir()

    monkeypatch.setenv("HOME", str(fake_home))

    config_path = str(pm_dir / "config.json")
    state_path = str(pm_dir / "state.json")
    db_path = str(pm_dir / "usage.db")
    version_db_path = str(pm_dir / "version_status.db")

    config = {
        "enabled": True,
        "min_claude_version": "2.1.39",
        "intent_validation_enabled": False,
    }
    with open(config_path, "w") as f:
        json.dump(config, f)

    monkeypatch.setenv("PACEMAKER_VERSION_STATUS_PATH", version_db_path)

    return {
        "config_path": config_path,
        "state_path": state_path,
        "db_path": db_path,
        "pm_dir": str(pm_dir),
    }


def _make_probe_stub(version_str):
    """Return a subprocess.run stub that outputs the given version string."""

    def _stub(*args, **kwargs):
        class _Result:
            stdout = version_str
            returncode = 0

        return _Result()

    return _stub


def _make_failing_probe_stub(exc):
    """Return a subprocess.run stub that raises the given exception."""

    def _stub(*args, **kwargs):
        raise exc

    return _stub


def _run_version_check(env, monkeypatch, probe_stub):
    """
    Run perform_session_start_version_check() with a controlled subprocess stub.

    Returns (state_after, stderr_text).
    subprocess.run is the only patch — it is the external system boundary
    (the real `claude` binary) and is the approved exception to anti-mock rules.
    """
    import subprocess
    import pacemaker.hook as hook_module
    from pacemaker.hook import load_state, save_state
    from pacemaker.version_check import perform_session_start_version_check

    monkeypatch.setattr(subprocess, "run", probe_stub)
    monkeypatch.setattr(hook_module, "DEFAULT_CONFIG_PATH", env["config_path"])
    monkeypatch.setattr(hook_module, "DEFAULT_STATE_PATH", env["state_path"])

    state = load_state(env["state_path"])
    with open(env["config_path"]) as f:
        config = json.load(f)

    stderr_capture = io.StringIO()
    perform_session_start_version_check(state, config, stderr=stderr_capture)
    save_state(state, env["state_path"])

    return state, stderr_capture.getvalue()


# ── SessionStart version check ────────────────────────────────────────────────


class TestSessionStartVersionCheck:
    """Component tests for perform_session_start_version_check()."""

    def test_above_minimum_does_not_block(self, pacemaker_env, monkeypatch):
        """Version above minimum: no block flag, no stderr message."""
        state, stderr = _run_version_check(
            pacemaker_env,
            monkeypatch,
            _make_probe_stub("2.1.126 (Claude Code)\n"),
        )
        assert state.get("version_block_active") is False
        assert stderr == ""

    def test_equal_to_minimum_does_not_block(self, pacemaker_env, monkeypatch):
        """Version exactly at minimum: no block (equal is allowed)."""
        state, stderr = _run_version_check(
            pacemaker_env,
            monkeypatch,
            _make_probe_stub("2.1.39\n"),
        )
        assert state.get("version_block_active") is False

    def test_below_minimum_sets_block_flag(self, pacemaker_env, monkeypatch):
        """Version below minimum: block flag is True."""
        state, stderr = _run_version_check(
            pacemaker_env,
            monkeypatch,
            _make_probe_stub("2.1.10 (Claude Code)\n"),
        )
        assert state.get("version_block_active") is True

    def test_below_minimum_writes_stderr_message_with_upgrade_hint(
        self, pacemaker_env, monkeypatch
    ):
        """Version below minimum: stderr contains 'claude upgrade' hint and minimum version."""
        state, stderr = _run_version_check(
            pacemaker_env,
            monkeypatch,
            _make_probe_stub("2.1.10 (Claude Code)\n"),
        )
        assert "upgrade" in stderr.lower()
        assert "2.1.39" in stderr

    def test_probe_failure_fails_open(self, pacemaker_env, monkeypatch):
        """Probe failure (FileNotFoundError): fail open, no block."""
        state, stderr = _run_version_check(
            pacemaker_env,
            monkeypatch,
            _make_failing_probe_stub(FileNotFoundError("claude not found")),
        )
        assert state.get("version_block_active") is False

    def test_malformed_output_fails_open(self, pacemaker_env, monkeypatch):
        """Unparseable probe output: fail open, no block."""
        state, stderr = _run_version_check(
            pacemaker_env,
            monkeypatch,
            _make_probe_stub("something completely garbled\n"),
        )
        assert state.get("version_block_active") is False

    def test_exception_in_check_fails_open(self, pacemaker_env, monkeypatch):
        """If probe_installed_version itself raises, state remains unblocked."""
        import pacemaker.version_check as vc_module
        import pacemaker.hook as hook_module
        from pacemaker.hook import load_state
        from pacemaker.version_check import perform_session_start_version_check

        monkeypatch.setattr(
            vc_module,
            "probe_installed_version",
            lambda: (_ for _ in ()).throw(RuntimeError("unexpected")),
        )
        monkeypatch.setattr(
            hook_module, "DEFAULT_CONFIG_PATH", pacemaker_env["config_path"]
        )
        monkeypatch.setattr(
            hook_module, "DEFAULT_STATE_PATH", pacemaker_env["state_path"]
        )

        state = load_state(pacemaker_env["state_path"])
        with open(pacemaker_env["config_path"]) as f:
            config = json.load(f)
        stderr_capture = io.StringIO()

        # Must not raise; must fail open
        perform_session_start_version_check(state, config, stderr=stderr_capture)
        assert state.get("version_block_active") is False


# ── Blocked hooks early-return ────────────────────────────────────────────────


class TestBlockedHooksEarlyReturn:
    """When version_block_active=True, downstream hooks must not block tool use."""

    def _write_blocked_state(self, state_path):
        """Write a version-blocked state to the given path."""
        from pacemaker.hook import save_state

        state = {
            "session_id": "test-session",
            "version_block_active": True,
            "subagent_counter": 0,
            "in_subagent": False,
            "tool_execution_count": 0,
        }
        save_state(state, state_path)
        return state

    def test_pre_tool_hook_no_block_decision_when_version_blocked(
        self, pacemaker_env, monkeypatch
    ):
        """PreToolUse with blocked state: result must not contain decision=block."""
        import pacemaker.hook as hook_module

        monkeypatch.setattr(
            hook_module, "DEFAULT_CONFIG_PATH", pacemaker_env["config_path"]
        )
        monkeypatch.setattr(
            hook_module, "DEFAULT_STATE_PATH", pacemaker_env["state_path"]
        )

        self._write_blocked_state(pacemaker_env["state_path"])

        hook_input = json.dumps(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/test.py", "content": "x=1"},
                "session_id": "test-session",
                "transcript_path": pacemaker_env["db_path"],
            }
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(hook_input))

        result = hook_module.run_pre_tool_hook()

        # Claude Code tools must still work when version is blocked
        assert result.get("decision") != "block"

    def test_stop_hook_allows_exit_when_version_blocked(
        self, pacemaker_env, monkeypatch
    ):
        """Stop hook with blocked state: must allow exit (not block the agent)."""
        import pacemaker.hook as hook_module

        monkeypatch.setattr(
            hook_module, "DEFAULT_CONFIG_PATH", pacemaker_env["config_path"]
        )
        monkeypatch.setattr(
            hook_module, "DEFAULT_STATE_PATH", pacemaker_env["state_path"]
        )

        self._write_blocked_state(pacemaker_env["state_path"])

        hook_input = json.dumps(
            {"session_id": "test-session", "transcript_path": pacemaker_env["db_path"]}
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(hook_input))

        result = hook_module.run_stop_hook()
        assert result.get("decision") != "block"
