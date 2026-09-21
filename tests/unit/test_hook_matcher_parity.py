"""Issue #107 — the two install paths must register the same PreToolUse matcher.

`hooks/hooks.json` (plugin mode) registered "Write|Edit" while `install.sh`
(classic mode) registered "Write|Edit|Bash". The danger-bash gate only runs on
Bash tool calls, so plugin-mode installs had ALL 55 danger rules silently
inert — while `pace-maker status` still reported the gate as enabled, because
status reads config.json rather than the registered matcher.

Nothing connected the two literals, so they drifted unnoticed. This test is
the connection.
"""

import json
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]


def _plugin_matcher():
    manifest = json.loads((REPO / "hooks/hooks.json").read_text())
    entries = manifest["hooks"]["PreToolUse"]
    matchers = [e["matcher"] for e in entries if "matcher" in e]
    assert len(matchers) == 1, f"expected one PreToolUse entry, got {matchers}"
    return matchers[0]


def _installer_matcher():
    text = (REPO / "install.sh").read_text()
    # The jq registration block: .hooks.PreToolUse += [{ "matcher": "..." ...
    block = re.search(
        r"\.hooks\.PreToolUse\s*\+=.*?\"matcher\"\s*:\s*\"([^\"]+)\"",
        text,
        re.DOTALL,
    )
    assert block, "could not locate the PreToolUse matcher in install.sh"
    return block.group(1)


def test_plugin_and_installer_matchers_match():
    """The whole bug: these two literals silently disagreed."""
    assert _plugin_matcher() == _installer_matcher()


def test_bash_present_in_both():
    """Explicit: Bash is what the danger-bash gate needs, and what went missing."""
    for name, matcher in (
        ("plugin manifest", _plugin_matcher()),
        ("installer", _installer_matcher()),
    ):
        assert "Bash" in matcher, f"{name} matcher lost Bash: {matcher!r}"


def test_write_and_edit_present_in_both():
    """Intent validation's own tools must not be dropped either."""
    for name, matcher in (
        ("plugin manifest", _plugin_matcher()),
        ("installer", _installer_matcher()),
    ):
        assert "Write" in matcher, f"{name} matcher lost Write: {matcher!r}"
        assert "Edit" in matcher, f"{name} matcher lost Edit: {matcher!r}"
