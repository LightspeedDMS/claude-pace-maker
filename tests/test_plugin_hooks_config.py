"""
Tests for Plugin Architecture - Hooks, CLI, and Config Files (Story #39).

Covers:
- Scenario 2: Plugin hooks fire correctly for all 7 hook types (hooks.json)
- Scenario 3: pace-maker CLI works via auto-symlink (scripts/pace-maker)
- plugin.json metadata structure
- config/config.defaults.json production defaults

Strategy: Real filesystem operations, real file inspection (anti-mock principle).
"""

import json
import os
import re
import subprocess
from pathlib import Path


REPO_ROOT = Path("/home/jsbattig/Dev/claude-pace-maker")
HOOK_SH = REPO_ROOT / "scripts" / "hook.sh"
HOOKS_JSON = REPO_ROOT / "hooks" / "hooks.json"
PLUGIN_JSON = REPO_ROOT / ".claude-plugin" / "plugin.json"
CLI_WRAPPER = REPO_ROOT / "scripts" / "pace-maker"
CONFIG_DEFAULTS = REPO_ROOT / "config" / "config.defaults.json"


# ---------------------------------------------------------------------------
# Scenario 2: hooks.json structure with all 7 hook types
# ---------------------------------------------------------------------------


class TestScenario2HooksJson:
    """hooks/hooks.json contains all 7 hook types with correct structure."""

    def test_hooks_json_exists(self):
        """hooks/hooks.json must exist."""
        assert HOOKS_JSON.exists(), f"hooks/hooks.json must exist at {HOOKS_JSON}"

    def test_hooks_json_is_valid_json(self):
        """hooks/hooks.json must be valid JSON."""
        with open(HOOKS_JSON) as f:
            data = json.load(f)
        assert isinstance(data, dict), "hooks.json must be a JSON object"

    def test_hooks_json_has_all_7_hook_types(self):
        """hooks.json must declare all 7 hook types."""
        with open(HOOKS_JSON) as f:
            data = json.load(f)
        expected_hooks = {
            "PreToolUse",
            "PostToolUse",
            "UserPromptSubmit",
            "SessionStart",
            "Stop",
            "SubagentStart",
            "SubagentStop",
        }
        actual_hooks = set(data.get("hooks", {}).keys())
        assert expected_hooks == actual_hooks, (
            f"hooks.json must have exactly 7 hook types. "
            f"Missing: {expected_hooks - actual_hooks}, "
            f"Extra: {actual_hooks - expected_hooks}"
        )

    def test_hooks_json_uses_plugin_root_variable(self):
        """All hook commands must reference ${CLAUDE_PLUGIN_ROOT}/scripts/hook.sh."""
        with open(HOOKS_JSON) as f:
            data = json.load(f)
        for hook_name, hook_entries in data.get("hooks", {}).items():
            for entry in hook_entries:
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    assert (
                        "${CLAUDE_PLUGIN_ROOT}" in cmd
                    ), f"Hook {hook_name} command must use ${{CLAUDE_PLUGIN_ROOT}}: {cmd}"
                    assert (
                        "scripts/hook.sh" in cmd
                    ), f"Hook {hook_name} command must invoke scripts/hook.sh: {cmd}"

    def test_hooks_json_pre_tool_use_has_matcher(self):
        """PreToolUse hook must have Write|Edit matcher."""
        with open(HOOKS_JSON) as f:
            data = json.load(f)
        pre_tool_entries = data["hooks"]["PreToolUse"]
        assert len(pre_tool_entries) > 0, "PreToolUse must have at least one entry"
        matchers = [entry.get("matcher", "") for entry in pre_tool_entries]
        assert any(
            "Write" in m and "Edit" in m for m in matchers
        ), f"PreToolUse must have Write|Edit matcher. Got: {matchers}"

    def test_hooks_json_post_tool_use_timeout(self):
        """PostToolUse must have timeout >= 120s for Langfuse pushes."""
        with open(HOOKS_JSON) as f:
            data = json.load(f)
        post_entries = data["hooks"]["PostToolUse"]
        for entry in post_entries:
            for hook in entry.get("hooks", []):
                timeout = hook.get("timeout", 0)
                assert (
                    timeout >= 120
                ), f"PostToolUse timeout must be >= 120s (got {timeout})"

    def test_hooks_json_stop_timeout(self):
        """Stop hook must have timeout >= 60s."""
        with open(HOOKS_JSON) as f:
            data = json.load(f)
        stop_entries = data["hooks"]["Stop"]
        for entry in stop_entries:
            for hook in entry.get("hooks", []):
                timeout = hook.get("timeout", 0)
                assert timeout >= 60, f"Stop timeout must be >= 60s (got {timeout})"

    def test_hooks_json_each_hook_passes_correct_type_arg(self):
        """Each hook command must pass the correct hook_type argument to hook.sh."""
        expected_args = {
            "PreToolUse": "pre_tool_use",
            "PostToolUse": "post_tool_use",
            "UserPromptSubmit": "user_prompt_submit",
            "SessionStart": "session_start",
            "Stop": "stop",
            "SubagentStart": "subagent_start",
            "SubagentStop": "subagent_stop",
        }
        with open(HOOKS_JSON) as f:
            data = json.load(f)
        for hook_name, expected_arg in expected_args.items():
            entries = data["hooks"].get(hook_name, [])
            for entry in entries:
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    assert expected_arg in cmd, (
                        f"Hook {hook_name} command must pass '{expected_arg}' argument. "
                        f"Got: {cmd}"
                    )


class TestScenario2HookSh:
    """scripts/hook.sh entry point has correct structure."""

    def test_hook_sh_exists(self):
        """scripts/hook.sh must exist."""
        assert HOOK_SH.exists(), f"scripts/hook.sh must exist at {HOOK_SH}"

    def test_hook_sh_is_executable(self):
        """scripts/hook.sh must be executable."""
        assert os.access(HOOK_SH, os.X_OK), "scripts/hook.sh must be executable"

    def test_hook_sh_accepts_hook_type_argument(self):
        """hook.sh must use first argument as hook type."""
        with open(HOOK_SH) as f:
            content = f.read()
        assert (
            "HOOK_TYPE" in content or '"$1"' in content or "'$1'" in content
        ), "hook.sh must capture first argument as hook type"

    def test_hook_sh_sets_pythonpath_from_plugin_root(self):
        """hook.sh must set PYTHONPATH to include plugin root /src."""
        with open(HOOK_SH) as f:
            content = f.read()
        assert "PYTHONPATH" in content, "hook.sh must set PYTHONPATH"
        assert "src" in content, "hook.sh PYTHONPATH must include /src path"

    def test_hook_sh_requires_hook_type_argument(self, tmp_path):
        """hook.sh must fail with error when called without hook_type argument."""
        home = tmp_path / "home"
        home.mkdir()
        result = subprocess.run(
            ["bash", str(HOOK_SH)],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(home)},
        )
        assert (
            result.returncode != 0
        ), "hook.sh must exit non-zero when called without hook_type argument"


# ---------------------------------------------------------------------------
# Scenario 3: pace-maker CLI bash wrapper
# ---------------------------------------------------------------------------


class TestScenario3CLIWrapper:
    """CLI wrapper at scripts/pace-maker resolves Python path via plugin root."""

    def test_cli_wrapper_exists(self):
        """scripts/pace-maker bash wrapper must exist."""
        assert CLI_WRAPPER.exists(), f"scripts/pace-maker must exist at {CLI_WRAPPER}"

    def test_cli_wrapper_is_executable(self):
        """scripts/pace-maker must be executable."""
        assert os.access(CLI_WRAPPER, os.X_OK), "scripts/pace-maker must be executable"

    def test_cli_wrapper_is_bash_script(self):
        """scripts/pace-maker must be a bash script (not Python)."""
        with open(CLI_WRAPPER) as f:
            first_line = f.readline().strip()
        assert (
            first_line.startswith("#!") and "bash" in first_line
        ), f"scripts/pace-maker must start with bash shebang, got: {first_line}"

    def test_cli_wrapper_sets_pythonpath(self):
        """scripts/pace-maker must set PYTHONPATH relative to script location."""
        with open(CLI_WRAPPER) as f:
            content = f.read()
        assert "PYTHONPATH" in content, "CLI wrapper must set PYTHONPATH"
        assert "src" in content, "CLI wrapper PYTHONPATH must reference /src"

    def test_cli_wrapper_invokes_user_commands(self):
        """scripts/pace-maker must invoke pacemaker.user_commands module."""
        with open(CLI_WRAPPER) as f:
            content = f.read()
        assert (
            "user_commands" in content
        ), "CLI wrapper must invoke pacemaker.user_commands module"

    def test_cli_wrapper_uses_exec(self):
        """scripts/pace-maker should use exec to replace process."""
        with open(CLI_WRAPPER) as f:
            content = f.read()
        assert (
            "exec " in content or "exec\t" in content
        ), "CLI wrapper should use exec to replace process (efficient)"


# ---------------------------------------------------------------------------
# plugin.json metadata
# ---------------------------------------------------------------------------


class TestPluginJson:
    """plugin.json has correct metadata structure."""

    def test_plugin_json_exists(self):
        """.claude-plugin/plugin.json must exist."""
        assert (
            PLUGIN_JSON.exists()
        ), f".claude-plugin/plugin.json must exist at {PLUGIN_JSON}"

    def test_plugin_json_is_valid_json(self):
        """.claude-plugin/plugin.json must be valid JSON."""
        with open(PLUGIN_JSON) as f:
            data = json.load(f)
        assert isinstance(data, dict), "plugin.json must be a JSON object"

    def test_plugin_json_name(self):
        """plugin.json name must be 'claude-pace-maker'."""
        with open(PLUGIN_JSON) as f:
            data = json.load(f)
        assert (
            data.get("name") == "claude-pace-maker"
        ), f"plugin.json name must be 'claude-pace-maker', got: {data.get('name')}"

    def test_plugin_json_has_description(self):
        """plugin.json must have non-empty description."""
        with open(PLUGIN_JSON) as f:
            data = json.load(f)
        assert (
            "description" in data and data["description"]
        ), "plugin.json must have non-empty description"

    def test_plugin_json_version_matches_pyproject(self):
        """plugin.json version must match pyproject.toml version."""
        with open(REPO_ROOT / "pyproject.toml") as f:
            content = f.read()
        match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
        assert match, "pyproject.toml must have version field"
        pyproject_version = match.group(1)

        with open(PLUGIN_JSON) as f:
            data = json.load(f)
        assert data.get("version") == pyproject_version, (
            f"plugin.json version ({data.get('version')}) must match "
            f"pyproject.toml version ({pyproject_version})"
        )

    def test_plugin_json_author_name(self):
        """plugin.json author must be an object with name containing Lightspeed."""
        with open(PLUGIN_JSON) as f:
            data = json.load(f)
        author = data.get("author", {})
        assert isinstance(author, dict), "plugin.json author must be an object"
        assert "name" in author, "plugin.json author must have name"
        assert (
            "Lightspeed" in author["name"]
        ), f"plugin.json author name must contain 'Lightspeed', got: {author.get('name')}"


# ---------------------------------------------------------------------------
# config/config.defaults.json
# ---------------------------------------------------------------------------


class TestConfigDefaults:
    """config/config.defaults.json has production defaults."""

    def test_config_defaults_exists(self):
        """config/config.defaults.json must exist."""
        assert (
            CONFIG_DEFAULTS.exists()
        ), f"config/config.defaults.json must exist at {CONFIG_DEFAULTS}"

    def test_config_defaults_is_valid_json(self):
        """config/config.defaults.json must be valid JSON."""
        with open(CONFIG_DEFAULTS) as f:
            data = json.load(f)
        assert isinstance(data, dict), "config.defaults.json must be JSON object"

    def test_config_defaults_has_required_keys(self):
        """config.defaults.json must have all required production default keys."""
        with open(CONFIG_DEFAULTS) as f:
            data = json.load(f)
        required_keys = [
            "enabled",
            "log_level",
            "langfuse_enabled",
            "intent_validation_enabled",
            "tdd_enabled",
        ]
        for key in required_keys:
            assert key in data, f"config.defaults.json must have '{key}'"

    def test_config_defaults_langfuse_disabled_by_default(self):
        """langfuse_enabled must default to false."""
        with open(CONFIG_DEFAULTS) as f:
            data = json.load(f)
        assert (
            data.get("langfuse_enabled") is False
        ), "langfuse_enabled must default to false in config.defaults.json"

    def test_config_defaults_enabled_true(self):
        """enabled must default to true."""
        with open(CONFIG_DEFAULTS) as f:
            data = json.load(f)
        assert data.get("enabled") is True, "enabled must default to true"

    def test_config_defaults_no_secrets(self):
        """config.defaults.json must not contain langfuse keys or secrets."""
        with open(CONFIG_DEFAULTS) as f:
            content = f.read()
        for secret_pattern in ["pk-lf-", "sk-lf-"]:
            assert (
                secret_pattern not in content
            ), f"config.defaults.json must not contain secrets: found '{secret_pattern}'"
