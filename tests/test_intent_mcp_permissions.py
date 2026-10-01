"""
Story #155 review M3 -- the declare_intent tool must be allow-listed, or
interactive / non-bypass sessions get a permission prompt (and subagents a
denial), and every edit silently falls back to the transcript path.

Probed against Claude Code 2.1.286 in a hook-free throwaway session:
- default permission mode, no rule: the call is DENIED ("Claude requested
  permissions to use mcp__pace-maker__declare_intent, but you haven't granted
  it yet");
- with ``permissions.allow`` containing ``mcp__pace-maker__declare_intent``: it
  runs;
- a PLUGIN cannot ship the permission (a plugin ``settings.json`` or a
  ``permissions`` key in ``plugin.json`` is ignored), while a user-settings
  allow rule naming ``mcp__plugin_claude-pace-maker_pace-maker__declare_intent``
  works -- plugin users must add that rule themselves (CLAUDE.md, Story #155).

``intent_mcp.permissions`` is the merge logic ``install.sh`` and
``migrate-to-plugin.sh`` call. It works on a settings FILE PATH it is given, so
these tests only ever touch tmp files, never the real settings.json.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pacemaker.constants import DECLARE_INTENT_TOOL_NAMES
from pacemaker.intent_mcp import permissions
from pacemaker.intent_mcp.permissions import (
    ALLOW_RULE,
    PermissionsError,
    add_allow_rule,
    main,
    remove_allow_rule,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write(path, data):
    path.write_text(data if isinstance(data, str) else json.dumps(data, indent=2))
    return path


def _read(path):
    return json.loads(path.read_text())


@pytest.fixture
def settings(tmp_path):
    """A settings path alone in its own directory (so leftover temp files
    are observable)."""
    directory = tmp_path / "cfg"
    directory.mkdir()
    return directory / "settings.json"


class TestRuleName:
    def test_rule_is_the_user_scope_tool_name(self):
        assert ALLOW_RULE == "mcp__pace-maker__declare_intent"
        assert ALLOW_RULE in DECLARE_INTENT_TOOL_NAMES


class TestAdd:
    def test_missing_file_is_created_with_the_rule(self, settings):
        assert add_allow_rule(str(settings)) is True
        assert _read(settings) == {"permissions": {"allow": [ALLOW_RULE]}}

    def test_empty_or_whitespace_file_is_treated_as_empty_settings(self, settings):
        _write(settings, "  \n")
        add_allow_rule(str(settings))
        assert _read(settings) == {"permissions": {"allow": [ALLOW_RULE]}}

    def test_existing_settings_and_allow_entries_are_preserved_in_order(self, settings):
        original = {
            "model": "opus",
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]},
            "permissions": {
                "allow": ["Bash(ls:*)", "mcp__other__tool"],
                "deny": ["Read(./.env)"],
                "defaultMode": "default",
            },
        }
        _write(settings, original)
        assert add_allow_rule(str(settings)) is True
        merged = _read(settings)
        assert merged["permissions"]["allow"] == [
            "Bash(ls:*)",
            "mcp__other__tool",
            ALLOW_RULE,
        ]
        assert merged["permissions"]["deny"] == ["Read(./.env)"]
        assert merged["permissions"]["defaultMode"] == "default"
        assert merged["model"] == "opus"
        assert merged["hooks"] == original["hooks"]

    def test_permissions_without_allow_gets_an_allow_list(self, settings):
        _write(settings, {"permissions": {"deny": ["x"]}})
        add_allow_rule(str(settings))
        assert _read(settings)["permissions"] == {"deny": ["x"], "allow": [ALLOW_RULE]}

    def test_idempotent_no_duplicates_and_no_rewrite(self, settings):
        add_allow_rule(str(settings))
        before = settings.read_text()
        assert add_allow_rule(str(settings)) is False
        assert add_allow_rule(str(settings)) is False
        assert settings.read_text() == before
        assert _read(settings)["permissions"]["allow"].count(ALLOW_RULE) == 1

    def test_rule_already_present_among_others_is_left_untouched(self, settings):
        _write(settings, {"permissions": {"allow": [ALLOW_RULE, "Bash(ls:*)"]}})
        before = settings.read_text()
        assert add_allow_rule(str(settings)) is False
        assert settings.read_text() == before

    def test_custom_rule_can_be_added(self, settings):
        add_allow_rule(str(settings), "mcp__x__y")
        assert _read(settings)["permissions"]["allow"] == ["mcp__x__y"]

    def test_file_mode_is_preserved(self, settings):
        _write(settings, {})
        os.chmod(settings, 0o600)
        add_allow_rule(str(settings))
        assert (settings.stat().st_mode & 0o777) == 0o600

    def test_no_temp_files_are_left_behind(self, settings):
        add_allow_rule(str(settings))
        assert sorted(p.name for p in settings.parent.iterdir()) == ["settings.json"]

    def test_parent_directory_is_created(self, tmp_path):
        target = tmp_path / ".claude" / "settings.json"
        add_allow_rule(str(target))
        assert target.exists()

    @pytest.mark.parametrize(
        "content",
        [
            "{not json",
            "[1, 2]",
            '"str"',
            json.dumps({"permissions": []}),
            json.dumps({"permissions": {"allow": "mcp__x"}}),
        ],
    )
    def test_malformed_settings_are_refused_and_left_untouched(self, settings, content):
        _write(settings, content)
        with pytest.raises(PermissionsError):
            add_allow_rule(str(settings))
        assert settings.read_text() == content


class TestSymlinkedSettings:
    """settings.json -> real.json (e.g. dotfiles managers): the link must
    survive and the TARGET must receive the change."""

    @pytest.fixture
    def linked(self, tmp_path):
        directory = tmp_path / "dots"
        directory.mkdir()
        real = directory / "real.json"
        link = directory / "settings.json"
        link.symlink_to(real.name)  # relative link, like most dotfile setups
        return link, real

    def test_add_writes_through_the_symlink(self, linked):
        link, real = linked
        _write(real, {"model": "opus"})
        assert add_allow_rule(str(link)) is True
        assert link.is_symlink()
        assert os.readlink(link) == "real.json"
        assert _read(real) == {
            "model": "opus",
            "permissions": {"allow": [ALLOW_RULE]},
        }

    def test_remove_writes_through_the_symlink(self, linked):
        link, real = linked
        _write(real, {"permissions": {"allow": [ALLOW_RULE, "a"]}})
        assert remove_allow_rule(str(link)) is True
        assert link.is_symlink()
        assert _read(real) == {"permissions": {"allow": ["a"]}}

    def test_target_mode_is_preserved_through_the_symlink(self, linked):
        link, real = linked
        _write(real, {})
        os.chmod(real, 0o600)
        add_allow_rule(str(link))
        assert (real.stat().st_mode & 0o777) == 0o600

    def test_dangling_symlink_creates_the_target(self, linked):
        link, real = linked
        assert not real.exists()
        add_allow_rule(str(link))
        assert link.is_symlink()
        assert _read(real) == {"permissions": {"allow": [ALLOW_RULE]}}

    def test_no_temp_files_left_next_to_the_target(self, linked):
        link, real = linked
        _write(real, {})
        add_allow_rule(str(link))
        assert sorted(p.name for p in real.parent.iterdir()) == [
            "real.json",
            "settings.json",
        ]


class TestNonAsciiPreserved:
    def test_non_ascii_content_is_not_escaped(self, settings):
        _write(
            settings,
            {"permissions": {"allow": ["Bash(echo →:*)"]}, "note": "héllo ✓"},
        )
        add_allow_rule(str(settings))
        raw = settings.read_text(encoding="utf-8")
        assert "Bash(echo →:*)" in raw
        assert "héllo ✓" in raw
        assert "\\u" not in raw
        assert _read(settings)["note"] == "héllo ✓"

    def test_remove_keeps_non_ascii_too(self, settings):
        _write(settings, {"permissions": {"allow": [ALLOW_RULE]}, "note": "→"})
        remove_allow_rule(str(settings))
        assert settings.read_text(encoding="utf-8").count("→") == 1
        assert "\\u" not in settings.read_text(encoding="utf-8")


class TestRemove:
    def test_removes_only_our_rule(self, settings):
        _write(
            settings,
            {"permissions": {"allow": ["Bash(ls:*)", ALLOW_RULE, "mcp__other__tool"]}},
        )
        assert remove_allow_rule(str(settings)) is True
        assert _read(settings)["permissions"]["allow"] == [
            "Bash(ls:*)",
            "mcp__other__tool",
        ]

    def test_removes_duplicates_of_the_rule(self, settings):
        _write(settings, {"permissions": {"allow": [ALLOW_RULE, "a", ALLOW_RULE]}})
        remove_allow_rule(str(settings))
        assert _read(settings)["permissions"]["allow"] == ["a"]

    def test_empty_allow_and_permissions_we_emptied_are_pruned(self, settings):
        add_allow_rule(str(settings))
        assert remove_allow_rule(str(settings)) is True
        assert _read(settings) == {}

    def test_other_permission_keys_survive_pruning(self, settings):
        _write(settings, {"permissions": {"allow": [ALLOW_RULE], "deny": ["x"]}})
        remove_allow_rule(str(settings))
        assert _read(settings) == {"permissions": {"deny": ["x"]}}

    def test_absent_rule_or_file_is_a_noop(self, settings):
        assert remove_allow_rule(str(settings)) is False
        assert not settings.exists()
        _write(settings, {"permissions": {"allow": ["a"]}})
        before = settings.read_text()
        assert remove_allow_rule(str(settings)) is False
        assert settings.read_text() == before

    def test_an_intentionally_empty_allow_list_is_not_pruned_when_rule_absent(
        self, settings
    ):
        _write(settings, {"permissions": {"allow": []}})
        remove_allow_rule(str(settings))
        assert _read(settings) == {"permissions": {"allow": []}}

    def test_malformed_settings_are_refused(self, settings):
        _write(settings, "{nope")
        with pytest.raises(PermissionsError):
            remove_allow_rule(str(settings))
        assert settings.read_text() == "{nope"

    def test_add_then_remove_restores_the_original_content(self, settings):
        original = {"model": "opus", "permissions": {"allow": ["a"], "deny": ["b"]}}
        _write(settings, original)
        add_allow_rule(str(settings))
        remove_allow_rule(str(settings))
        assert _read(settings) == original


class TestCli:
    def test_add_and_remove_exit_codes_and_messages(self, settings, capsys):
        assert main(["add", "--settings-file", str(settings)]) == 0
        assert ALLOW_RULE in _read(settings)["permissions"]["allow"]
        assert "added" in capsys.readouterr().out.lower()
        assert main(["add", "--settings-file", str(settings)]) == 0
        assert "already" in capsys.readouterr().out.lower()
        assert main(["remove", "--settings-file", str(settings)]) == 0
        assert _read(settings) == {}

    def test_failure_exits_one_with_the_reason_on_stderr(self, settings, capsys):
        _write(settings, "{nope")
        assert main(["add", "--settings-file", str(settings)]) == 1
        assert "settings" in capsys.readouterr().err.lower()

    def test_runs_as_a_module(self, settings, tmp_path):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pacemaker.intent_mcp.permissions",
                "add",
                "--settings-file",
                str(settings),
            ],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(tmp_path),
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert _read(settings)["permissions"]["allow"] == [ALLOW_RULE]

    def test_module_is_the_entry_point_install_sh_uses(self):
        assert permissions.main


class TestScriptWiring:
    """install.sh / migrate-to-plugin.sh must actually call the helper
    (anti-orphan), non-fatally, from the installed snapshot."""

    def _install(self):
        return (REPO_ROOT / "install.sh").read_text()

    def test_install_allows_the_tool_in_the_settings_file_it_registers_hooks_in(self):
        text = self._install()
        assert "pacemaker.intent_mcp.permissions" in text
        assert '--settings-file "$SETTINGS_FILE"' in text

    def test_allow_step_is_non_fatal_and_skippable(self):
        text = self._install()
        body = text.split("allow_intent_mcp_tool() {", 1)[1].split("\n}\n", 1)[0]
        assert "exit 1" not in body
        assert "return 0" in body
        assert "PACEMAKER_SKIP_MCP_REGISTRATION" in body

    def test_install_main_calls_the_allow_step_after_registration(self):
        text = self._install()
        main_body = text.split("\nmain() {", 1)[1]
        assert main_body.index("register_intent_mcp_server") < main_body.index(
            "allow_intent_mcp_tool"
        )

    def test_plugin_mode_skips_it(self):
        text = self._install()
        plugin_block = text.split('if [ -n "$CLAUDE_PLUGIN_ROOT" ]; then', 1)[1]
        plugin_block = plugin_block.split("return 0", 1)[0]
        assert "allow_intent_mcp_tool" not in plugin_block

    def test_migrate_removes_the_rule_before_deleting_the_snapshot(self):
        text = (REPO_ROOT / "migrate-to-plugin.sh").read_text()
        assert "pacemaker.intent_mcp.permissions" in text
        assert text.index("pacemaker.intent_mcp.permissions") < text.index(
            'rm -rf "$PACEMAKER_HOOKS_DIR"'
        )


class TestE2eInstallSuitesNeverCallTheRealClaudeMcpCli:
    """L7: the legacy install e2e files run the real install.sh; they must set
    PACEMAKER_SKIP_MCP_REGISTRATION=1 so it never reaches `claude mcp`."""

    def test_e2e_conftest_sets_the_skip_variable_for_install_suites(self):
        conftest = REPO_ROOT / "tests" / "e2e" / "conftest.py"
        assert conftest.is_file()
        text = conftest.read_text()
        assert "PACEMAKER_SKIP_MCP_REGISTRATION" in text
        assert "test_install" in text
