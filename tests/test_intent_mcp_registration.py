"""
Story #155 AC2 -- idempotent user-scope registration of the declare_intent MCP
server, from the installed snapshot, plus the plugin manifest declaration.

The registration helper runs the ``claude`` CLI. These tests never touch a
real Claude config: ``claude_bin`` points at a small recording script we
control (a fake with the same observable add/remove behaviour the real CLI
showed when probed against a throwaway HOME: ``add`` on an existing name
fails, ``remove`` of a missing name fails with "No MCP server named ..."). The
script is deliberately NOT named ``claude`` (the conftest guard blocks that).
"""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from pacemaker.intent_mcp import registration
from pacemaker.intent_mcp.registration import (
    RegistrationError,
    build_add_argv,
    build_remove_argv,
    main,
    register,
    unregister,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

FAKE_CLAUDE = """#!/bin/bash
# Fake claude CLI: records argv, emulates user-scope add/remove semantics.
LOG="$FAKE_CLAUDE_DIR/calls.log"
STATE="$FAKE_CLAUDE_DIR/registered"
echo "$*" >> "$LOG"
[ "$1" = "mcp" ] || exit 64
case "$2" in
  remove)
    if [ -n "$FAKE_CLAUDE_REMOVE_FAIL" ]; then echo "$FAKE_CLAUDE_REMOVE_FAIL" >&2; exit 1; fi
    if [ -f "$STATE" ]; then rm -f "$STATE"; echo "Removed MCP server pace-maker from user config"; exit 0; fi
    echo "No MCP server named \\"pace-maker\\" in user scope" >&2; exit 1 ;;
  add)
    if [ -n "$FAKE_CLAUDE_ADD_FAIL" ]; then echo "$FAKE_CLAUDE_ADD_FAIL" >&2; exit 1; fi
    if [ -f "$STATE" ]; then echo "MCP server pace-maker already exists in user config" >&2; exit 1; fi
    echo "$*" > "$STATE"; echo "Added stdio MCP server pace-maker"; exit 0 ;;
  *) exit 65 ;;
esac
"""

SLOW_CLAUDE = """#!/bin/bash
sleep 30
"""


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    directory = tmp_path / "fake_claude"
    directory.mkdir()
    script = directory / "fake-claude-recorder"
    script.write_text(FAKE_CLAUDE)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("FAKE_CLAUDE_DIR", str(directory))
    for name in ("FAKE_CLAUDE_REMOVE_FAIL", "FAKE_CLAUDE_ADD_FAIL"):
        monkeypatch.delenv(name, raising=False)
    return directory, str(script)


@pytest.fixture
def snapshot(tmp_path):
    """A deployed-snapshot-shaped dir: <dir>/pacemaker/__init__.py."""
    directory = tmp_path / "hooks"
    (directory / "pacemaker").mkdir(parents=True)
    (directory / "pacemaker" / "__init__.py").write_text("")
    return str(directory)


def _calls(directory):
    log = directory / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


class TestArgvBuilders:
    def test_add_argv_registers_user_scope_with_snapshot_env(self):
        argv = build_add_argv("/usr/bin/python3.11", "/home/u/.claude/hooks", "claudex")
        assert argv == [
            "claudex",
            "mcp",
            "add",
            "--scope",
            "user",
            "pace-maker",
            "-e",
            "PYTHONPATH=/home/u/.claude/hooks",
            "-e",
            "PYTHONSAFEPATH=1",
            "--",
            "/usr/bin/python3.11",
            "-m",
            "pacemaker.intent_mcp",
        ]

    def test_name_precedes_variadic_env_options(self):
        """`-e` is variadic in the real CLI: the server name must come BEFORE
        it or it would be swallowed as an env value."""
        argv = build_add_argv("/p", "/s")
        assert argv.index("pace-maker") < argv.index("-e")

    def test_remove_argv(self):
        assert build_remove_argv("claudex") == [
            "claudex",
            "mcp",
            "remove",
            "--scope",
            "user",
            "pace-maker",
        ]

    def test_default_binary_is_claude(self):
        assert build_add_argv("/p", "/s")[0] == "claude"
        assert build_remove_argv()[0] == "claude"


class TestRegister:
    def test_fresh_registration_removes_then_adds(self, fake_claude, snapshot):
        directory, binary = fake_claude
        register(sys.executable, snapshot, claude_bin=binary)
        calls = _calls(directory)
        assert calls[0].startswith("mcp remove --scope user pace-maker")
        assert calls[1].startswith("mcp add --scope user pace-maker")
        assert (directory / "registered").exists()
        assert f"PYTHONPATH={snapshot}" in calls[1]
        assert "PYTHONSAFEPATH=1" in calls[1]
        assert f"{sys.executable} -m pacemaker.intent_mcp" in calls[1]

    def test_registration_is_idempotent(self, fake_claude, snapshot):
        directory, binary = fake_claude
        register(sys.executable, snapshot, claude_bin=binary)
        register(sys.executable, snapshot, claude_bin=binary)
        register(sys.executable, snapshot, claude_bin=binary)
        assert (directory / "registered").exists()
        adds = [c for c in _calls(directory) if c.startswith("mcp add")]
        assert len(adds) == 3  # each run re-added after removing the old one

    def test_unexpected_remove_failure_is_reported_and_add_not_attempted(
        self, fake_claude, snapshot, monkeypatch
    ):
        directory, binary = fake_claude
        monkeypatch.setenv("FAKE_CLAUDE_REMOVE_FAIL", "permission denied on config")
        with pytest.raises(RegistrationError, match="permission denied on config"):
            register(sys.executable, snapshot, claude_bin=binary)
        assert not [c for c in _calls(directory) if c.startswith("mcp add")]

    def test_add_failure_is_reported_with_cli_output(
        self, fake_claude, snapshot, monkeypatch
    ):
        _, binary = fake_claude
        monkeypatch.setenv("FAKE_CLAUDE_ADD_FAIL", "config is read-only")
        with pytest.raises(RegistrationError, match="config is read-only"):
            register(sys.executable, snapshot, claude_bin=binary)

    def test_missing_claude_binary_names_the_problem(self, snapshot, tmp_path):
        with pytest.raises(RegistrationError, match="not found"):
            register(
                sys.executable,
                snapshot,
                claude_bin=str(tmp_path / "no-such-claude"),
            )

    def test_hung_cli_times_out_instead_of_hanging_the_installer(
        self, snapshot, tmp_path
    ):
        script = tmp_path / "slow-claude"
        script.write_text(SLOW_CLAUDE)
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        with pytest.raises(RegistrationError, match="timed out"):
            register(
                sys.executable, snapshot, claude_bin=str(script), timeout_seconds=1
            )

    def test_snapshot_without_pacemaker_package_is_refused(self, fake_claude, tmp_path):
        directory, binary = fake_claude
        empty = tmp_path / "empty_hooks"
        empty.mkdir()
        with pytest.raises(RegistrationError, match="snapshot"):
            register(sys.executable, str(empty), claude_bin=binary)
        assert _calls(directory) == []

    def test_relative_python_path_is_refused(self, fake_claude, snapshot):
        directory, binary = fake_claude
        with pytest.raises(RegistrationError, match="absolute"):
            register("python3", snapshot, claude_bin=binary)
        assert _calls(directory) == []

    def test_registers_the_server_that_actually_starts_from_that_snapshot(
        self, fake_claude, tmp_path
    ):
        """End to end for the registered command: deploy a real snapshot
        (copy of src/pacemaker), run EXACTLY the argv/env the registration
        would hand to Claude Code, and get an MCP handshake back."""
        import shutil

        directory, binary = fake_claude
        snapshot_dir = tmp_path / "deployed"
        shutil.copytree(
            REPO_ROOT / "src" / "pacemaker",
            snapshot_dir / "pacemaker",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        register(sys.executable, str(snapshot_dir), claude_bin=binary)
        add_call = [c for c in _calls(directory) if c.startswith("mcp add")][0]
        assert f"PYTHONPATH={snapshot_dir}" in add_call
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["PYTHONPATH"] = str(snapshot_dir)
        env["PYTHONSAFEPATH"] = "1"
        proc = subprocess.run(
            [sys.executable, "-m", "pacemaker.intent_mcp"],
            input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n",
            capture_output=True,
            text=True,
            env=env,
            cwd=str(tmp_path),
            timeout=30,
        )
        assert json.loads(proc.stdout) == {"jsonrpc": "2.0", "id": 1, "result": {}}


class TestUnregister:
    def test_removes_an_existing_registration(self, fake_claude, snapshot):
        directory, binary = fake_claude
        register(sys.executable, snapshot, claude_bin=binary)
        unregister(claude_bin=binary)
        assert not (directory / "registered").exists()

    def test_absent_registration_is_not_an_error(self, fake_claude):
        unregister(claude_bin=fake_claude[1])

    def test_other_failures_are_reported(self, fake_claude, monkeypatch):
        monkeypatch.setenv("FAKE_CLAUDE_REMOVE_FAIL", "disk on fire")
        with pytest.raises(RegistrationError, match="disk on fire"):
            unregister(claude_bin=fake_claude[1])


class TestCli:
    def test_add_returns_zero_and_registers(self, fake_claude, snapshot, capsys):
        directory, binary = fake_claude
        rc = main(
            [
                "add",
                "--python",
                sys.executable,
                "--snapshot-dir",
                snapshot,
                "--claude-bin",
                binary,
            ]
        )
        assert rc == 0
        assert (directory / "registered").exists()
        assert "registered" in capsys.readouterr().out.lower()

    def test_add_failure_returns_nonzero_with_message_on_stderr(
        self, fake_claude, snapshot, monkeypatch, capsys
    ):
        monkeypatch.setenv("FAKE_CLAUDE_ADD_FAIL", "boom")
        rc = main(
            [
                "add",
                "--python",
                sys.executable,
                "--snapshot-dir",
                snapshot,
                "--claude-bin",
                fake_claude[1],
            ]
        )
        assert rc == 1
        assert "boom" in capsys.readouterr().err

    def test_remove_returns_zero(self, fake_claude, capsys):
        rc = main(["remove", "--claude-bin", fake_claude[1]])
        assert rc == 0

    def test_runs_as_a_module(self, fake_claude, snapshot, tmp_path):
        directory, binary = fake_claude
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pacemaker.intent_mcp.registration",
                "add",
                "--python",
                sys.executable,
                "--snapshot-dir",
                snapshot,
                "--claude-bin",
                binary,
            ],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(tmp_path),
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert (directory / "registered").exists()


class TestInstallScriptWiring:
    """install.sh / migrate-to-plugin.sh must actually CALL the helper and
    deploy the new packages (anti-orphan: a helper nobody invokes is dead)."""

    def _install_sh(self):
        return (REPO_ROOT / "install.sh").read_text()

    def test_install_deploys_the_new_packages_to_the_snapshot(self):
        text = self._install_sh()
        assert '_copy_subdir "intent_mcp"' in text
        assert '_copy_subdir "intent_declarations"' in text

    # `hooks` is a pre-existing, never-deployed legacy stub (a single
    # post_tool.py reminder function nothing imports at runtime) -- out of
    # scope for #155, listed here so this guard still catches any NEW
    # sub-package that install.sh forgets (the failure mode that would make
    # the MCP server crash on import from the snapshot).
    _NEVER_DEPLOYED_LEGACY = {"hooks"}

    def test_every_pacemaker_subpackage_is_deployed_by_install_sh(self):
        text = self._install_sh()
        packages = [
            p.name
            for p in (REPO_ROOT / "src" / "pacemaker").iterdir()
            if p.is_dir()
            and (p / "__init__.py").exists()
            and p.name not in self._NEVER_DEPLOYED_LEGACY
        ]
        missing = [p for p in packages if f'_copy_subdir "{p}"' not in text]
        assert not missing, f"sub-packages never copied into the snapshot: {missing}"

    def test_install_calls_the_registration_helper_from_the_snapshot(self):
        text = self._install_sh()
        assert "pacemaker.intent_mcp.registration" in text
        assert "register_intent_mcp_server" in text
        # issue #146 rules: snapshot on PYTHONPATH, cwd shadowing disabled.
        assert 'PYTHONPATH="$HOOKS_DIR"' in text
        assert "PYTHONSAFEPATH=1" in text

    def test_registration_is_skipped_in_plugin_mode(self):
        text = self._install_sh()
        plugin_block = text.split('if [ -n "$CLAUDE_PLUGIN_ROOT" ]; then', 1)[1]
        plugin_block = plugin_block.split("return 0", 1)[0]
        assert "register_intent_mcp_server" not in plugin_block

    def test_migrate_to_plugin_removes_the_user_scope_registration(self):
        text = (REPO_ROOT / "migrate-to-plugin.sh").read_text()
        assert "pacemaker.intent_mcp.registration" in text
        assert "remove" in text

    def test_registration_failure_is_non_fatal_in_install_sh(self):
        text = self._install_sh()
        body = text.split("register_intent_mcp_server() {", 1)[1].split("\n}\n", 1)[0]
        assert "return 0" in body
        assert "exit 1" not in body

    def test_helper_module_is_importable_and_cli_named_in_install(self):
        assert registration.main  # entry point install.sh invokes via -m


def _plugin_server():
    manifest = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text())
    return manifest["mcpServers"]["pace-maker"]


class TestPluginManifest:
    """The plugin manifest declares the server INLINE (plugin.json), not via
    a repo-root .mcp.json: the repo root is also a Claude Code *project*
    root, where a root .mcp.json would be read as a project-scope server with
    an unresolvable ${CLAUDE_PLUGIN_ROOT}."""

    def test_manifest_declares_the_server_via_plugin_root(self):
        server = _plugin_server()
        assert server["command"] == "python3"
        assert server["args"] == ["-m", "pacemaker.intent_mcp"]
        assert server["env"]["PYTHONPATH"] == "${CLAUDE_PLUGIN_ROOT}/src"
        assert server["env"]["PYTHONSAFEPATH"] == "1"

    def test_no_repo_root_mcp_json_that_would_be_a_project_scope_server(self):
        assert not (REPO_ROOT / ".mcp.json").exists()

    def test_plugin_declared_server_really_answers_the_handshake(self, tmp_path):
        """Run the declared command with ${CLAUDE_PLUGIN_ROOT} substituted the
        way Claude Code does."""
        server = _plugin_server()
        env = dict(os.environ)
        for key, value in server["env"].items():
            env[key] = value.replace("${CLAUDE_PLUGIN_ROOT}", str(REPO_ROOT))
        proc = subprocess.run(
            [sys.executable] + server["args"],  # python3 == the test interpreter
            input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            + "\n",
            capture_output=True,
            text=True,
            env=env,
            cwd=str(tmp_path),
            timeout=30,
        )
        tools = json.loads(proc.stdout)["result"]["tools"]
        assert [t["name"] for t in tools] == ["declare_intent"]
