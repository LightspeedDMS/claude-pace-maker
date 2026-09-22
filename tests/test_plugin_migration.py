"""
Tests for Plugin Architecture - Migration and Install Plugin Mode (Story #39).

Covers:
- Scenario 5: Legacy hooks removed via migrate-to-plugin.sh
- Scenario 6: install.sh detects plugin mode (CLAUDE_PLUGIN_ROOT set)

Strategy: Real filesystem operations in temp directories (anti-mock principle).
All tests use subprocess to run actual shell scripts.
"""

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATE_SH = REPO_ROOT / "migrate-to-plugin.sh"
INSTALL_SH = REPO_ROOT / "install.sh"


def run_script(script_path, env_overrides=None, cwd=None):
    """Run a shell script with controlled environment."""
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd or REPO_ROOT),
    )


# ---------------------------------------------------------------------------
# Scenario 5: Legacy hooks removed via migrate-to-plugin.sh
# ---------------------------------------------------------------------------


class TestScenario5MigrateToPlugin:
    """migrate-to-plugin.sh removes legacy hooks from settings.json."""

    @pytest.fixture
    def legacy_settings(self, tmp_path):
        """Create a settings.json with legacy pace-maker hooks AND other hooks."""
        home = tmp_path / "home"
        claude_dir = home / ".claude"
        hooks_dir = claude_dir / "hooks"
        pacemaker_hooks_dir = hooks_dir / "pacemaker"
        claude_dir.mkdir(parents=True)
        hooks_dir.mkdir()
        pacemaker_hooks_dir.mkdir()

        # Create legacy hook script files
        legacy_scripts = [
            "pre-tool-use.sh",
            "post-tool-use.sh",
            "stop.sh",
            "user-prompt-submit.sh",
            "session-start.sh",
            "subagent-start.sh",
            "subagent-stop.sh",
        ]
        for script in legacy_scripts:
            (hooks_dir / script).write_text("#!/bin/bash\n# pace-maker hook\n")

        # Build settings with pace-maker hooks AND a non-pace-maker hook
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Write|Edit",
                        "hooks": [
                            {
                                "type": "command",
                                "command": str(hooks_dir / "pre-tool-use.sh"),
                            }
                        ],
                    },
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/usr/local/bin/other-tool-guard.sh",
                            }
                        ],
                    },
                ],
                "PostToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": str(hooks_dir / "post-tool-use.sh"),
                                "timeout": 360,
                            }
                        ]
                    }
                ],
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": str(hooks_dir / "stop.sh"),
                                "timeout": 120,
                            }
                        ]
                    }
                ],
                "UserPromptSubmit": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": str(hooks_dir / "user-prompt-submit.sh"),
                            }
                        ]
                    }
                ],
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": str(hooks_dir / "session-start.sh"),
                                "timeout": 10,
                            }
                        ]
                    }
                ],
                "SubagentStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": str(hooks_dir / "subagent-start.sh"),
                                "timeout": 10,
                            }
                        ]
                    }
                ],
                "SubagentStop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": str(hooks_dir / "subagent-stop.sh"),
                                "timeout": 10,
                            }
                        ]
                    }
                ],
            }
        }
        settings_file = claude_dir / "settings.json"
        with open(settings_file, "w") as f:
            json.dump(settings, f, indent=2)

        return {
            "home": home,
            "claude_dir": claude_dir,
            "hooks_dir": hooks_dir,
            "pacemaker_hooks_dir": pacemaker_hooks_dir,
            "settings_file": settings_file,
        }

    def test_migration_script_exists(self):
        """migrate-to-plugin.sh must exist."""
        assert MIGRATE_SH.exists(), f"migrate-to-plugin.sh must exist at {MIGRATE_SH}"

    def test_migration_script_is_executable(self):
        """migrate-to-plugin.sh must be executable."""
        assert os.access(MIGRATE_SH, os.X_OK), "migrate-to-plugin.sh must be executable"

    def test_migration_exits_zero(self, legacy_settings):
        """migrate-to-plugin.sh must exit 0."""
        result = run_script(
            MIGRATE_SH, env_overrides={"HOME": str(legacy_settings["home"])}
        )
        assert (
            result.returncode == 0
        ), f"migrate-to-plugin.sh must exit 0. stderr={result.stderr}"

    def test_migration_removes_pacemaker_hooks_from_settings(self, legacy_settings):
        """Migration removes all pace-maker hook entries from settings.json."""
        run_script(MIGRATE_SH, env_overrides={"HOME": str(legacy_settings["home"])})
        with open(legacy_settings["settings_file"]) as f:
            settings = json.load(f)
        pacemaker_pattern = str(legacy_settings["hooks_dir"])
        for hook_type, entries in settings.get("hooks", {}).items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    assert pacemaker_pattern not in cmd, (
                        f"Settings must not contain legacy hook paths after migration. "
                        f"Found in {hook_type}: {cmd}"
                    )

    def test_migration_preserves_non_pacemaker_hooks(self, legacy_settings):
        """Migration preserves all non-pace-maker hooks intact."""
        run_script(MIGRATE_SH, env_overrides={"HOME": str(legacy_settings["home"])})
        with open(legacy_settings["settings_file"]) as f:
            settings = json.load(f)
        pre_tool = settings.get("hooks", {}).get("PreToolUse", [])
        all_commands = [
            hook.get("command", "")
            for entry in pre_tool
            for hook in entry.get("hooks", [])
        ]
        assert any(
            "other-tool-guard.sh" in cmd for cmd in all_commands
        ), f"Non-pace-maker hook must be preserved. PreToolUse commands: {all_commands}"

    def test_migration_removes_legacy_hook_scripts(self, legacy_settings):
        """Migration removes legacy hook scripts from ~/.claude/hooks/."""
        run_script(MIGRATE_SH, env_overrides={"HOME": str(legacy_settings["home"])})
        hooks_dir = legacy_settings["hooks_dir"]
        for script in [
            "pre-tool-use.sh",
            "post-tool-use.sh",
            "stop.sh",
            "user-prompt-submit.sh",
            "session-start.sh",
            "subagent-start.sh",
            "subagent-stop.sh",
        ]:
            assert not (
                hooks_dir / script
            ).exists(), f"Legacy hook script {script} must be removed by migration"

    def test_migration_removes_pacemaker_directory(self, legacy_settings):
        """Migration removes ~/.claude/hooks/pacemaker/ directory."""
        run_script(MIGRATE_SH, env_overrides={"HOME": str(legacy_settings["home"])})
        assert not legacy_settings[
            "pacemaker_hooks_dir"
        ].exists(), "~/.claude/hooks/pacemaker/ directory must be removed by migration"

    def test_migration_settings_remains_valid_json(self, legacy_settings):
        """settings.json must remain valid JSON after migration."""
        run_script(MIGRATE_SH, env_overrides={"HOME": str(legacy_settings["home"])})
        with open(legacy_settings["settings_file"]) as f:
            data = json.load(f)
        assert isinstance(data, dict), "settings.json must remain a valid JSON object"


# ---------------------------------------------------------------------------
# Scenario 6: install.sh detects plugin mode (CLAUDE_PLUGIN_ROOT set)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def prebaked_plugin_mode_install(tmp_path_factory):
    """One real `install.sh` run with CLAUDE_PLUGIN_ROOT set (real
    filesystem writes, real venv/db init -- no mocking), shared read-only
    across every test in TestScenario6InstallPluginMode below.

    Issue #144: each of the 4 tests in that class independently ran a
    fresh `install.sh` (~26-43s each -- confirmed by direct measurement),
    which is what actually caused the "4 failed" the GitHub issue
    reported: `run_tests.sh`'s `--timeout=15` is shorter than a single
    real install.sh run, so every one of these 4 tests was being killed
    by pytest-timeout, not failing on its own assertion (re-running with
    `--timeout=170` showed 12 passed / 0 failed for the whole file). All
    four tests below are pure read-only post-condition checks (settings
    absent, config present, db table present, exit code) against the SAME
    install outcome, so one real run is enough."""
    home = tmp_path_factory.mktemp("plugin_mode_home")
    result = run_script(
        INSTALL_SH,
        env_overrides={
            "HOME": str(home),
            "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
        },
    )
    return home, result


@pytest.mark.timeout(90)
class TestScenario6InstallPluginMode:
    """When CLAUDE_PLUGIN_ROOT is set, install.sh skips hook registration."""

    def test_install_skips_settings_modification_in_plugin_mode(
        self, prebaked_plugin_mode_install
    ):
        """install.sh must NOT create/modify settings.json when CLAUDE_PLUGIN_ROOT is set."""
        home, _result = prebaked_plugin_mode_install
        settings_file = home / ".claude" / "settings.json"
        assert not settings_file.exists(), (
            "install.sh must NOT create settings.json in plugin mode. "
            "File was unexpectedly created."
        )

    def test_install_creates_config_in_plugin_mode(self, prebaked_plugin_mode_install):
        """install.sh still creates config.json in plugin mode."""
        home, result = prebaked_plugin_mode_install
        config_file = home / ".claude-pace-maker" / "config.json"
        assert config_file.exists(), (
            f"install.sh must still create config.json in plugin mode. "
            f"returncode={result.returncode} stderr={result.stderr[:300]}"
        )

    def test_install_initializes_db_in_plugin_mode(self, prebaked_plugin_mode_install):
        """install.sh still initializes usage.db in plugin mode."""
        home, _result = prebaked_plugin_mode_install
        db_file = home / ".claude-pace-maker" / "usage.db"
        assert (
            db_file.exists()
        ), "install.sh must still initialize usage.db in plugin mode"
        with sqlite3.connect(str(db_file)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='usage_snapshots'"
            )
            count = cursor.fetchone()[0]
        assert count == 1, "usage_snapshots table must exist in plugin mode"

    def test_install_exits_zero_in_plugin_mode(self, prebaked_plugin_mode_install):
        """install.sh exits 0 in plugin mode."""
        _home, result = prebaked_plugin_mode_install
        assert result.returncode == 0, (
            f"install.sh must exit 0 in plugin mode. "
            f"stdout={result.stdout[-500:]} stderr={result.stderr[-300:]}"
        )
