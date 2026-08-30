#!/usr/bin/env python3
"""
Regression tests for issue #104.

Bug: `pace-maker excluded-paths add` prints a confirmation message that
unconditionally re-derives a trailing-slash-normalized display string
(`path if path.endswith("/") else path + "/"`), independent of what
`excluded_paths.add_exclusion()` actually persisted to the YAML config.

Per story #92's F-6 fix, `add_exclusion()` does NOT append a trailing
slash to filename-suffix-style patterns (anything starting with "*") —
only to directory-style patterns. So the confirmation message for
`pace-maker excluded-paths add *.generated.cs` printed
`'*.generated.cs/' added successfully` while the YAML actually stored
`'*.generated.cs'` (no trailing slash) — a user who copy-pastes the
printed confirmation into `excluded-paths remove` gets a confusing
"not found" failure.

These tests assert the printed confirmation message's quoted path is
EXACTLY equal to the value actually persisted in the YAML, for both a
filename-suffix pattern and a directory-style pattern (so the fix does
not regress the correct trailing-slash behavior for directory patterns),
and that copy-pasting the confirmed path into `remove` round-trips
successfully.
"""

import re

from unittest.mock import patch

from pacemaker import user_commands
from pacemaker import excluded_paths


def _extract_quoted_path(message: str) -> str:
    """Extract the single-quoted path from a confirmation message."""
    match = re.search(r"'([^']*)'", message)
    assert match is not None, f"No quoted path found in message: {message!r}"
    return match.group(1)


def _add_exclusion_via_cli(config_path, path: str):
    """Run 'pace-maker excluded-paths add <path>' against config_path and
    return (result_dict, stored_exclusions_list).
    """
    with patch("pacemaker.constants.DEFAULT_EXCLUDED_PATHS_PATH", str(config_path)):
        result = user_commands.handle_user_prompt(
            f"pace-maker excluded-paths add {path}",
            "/tmp/config.json",
            "/tmp/db.sqlite",
        )
    stored_exclusions = excluded_paths.load_exclusions(str(config_path))
    return result, stored_exclusions


class TestIssue104ConfirmationMessageMatchesStoredValue:
    """Confirmation message must reflect the actual persisted value."""

    def test_add_filename_suffix_pattern_message_matches_stored_value(self, tmp_path):
        """FAILS PRE-FIX: '*.generated.cs' (a "*"-prefixed suffix pattern)
        is stored WITHOUT a trailing slash, but the buggy code
        unconditionally appends one to the printed message — so the
        printed path does not equal the stored value.
        """
        config_path = tmp_path / "excluded_paths.yaml"

        result, stored_exclusions = _add_exclusion_via_cli(
            config_path, "*.generated.cs"
        )

        assert result["intercepted"] is True
        assert "added successfully" in result["output"].lower()

        # Sanity check on the story #92 F-6 storage behavior this bug depends on.
        assert "*.generated.cs" in stored_exclusions
        assert "*.generated.cs/" not in stored_exclusions

        printed_path = _extract_quoted_path(result["output"])
        assert printed_path in stored_exclusions, (
            f"Confirmation message quoted '{printed_path}' but the YAML "
            f"actually stored {stored_exclusions!r}"
        )
        assert printed_path == "*.generated.cs"

    def test_add_directory_pattern_message_matches_stored_value(self, tmp_path):
        """PASSING REGRESSION GUARD: 'mytests' (a directory-style pattern,
        no leading "*") is normalized WITH a trailing slash in BOTH
        storage and (even pre-fix) the printed message, so this test
        already passes today — it locks in that the fix must not break
        this existing correct behavior.
        """
        config_path = tmp_path / "excluded_paths.yaml"

        result, stored_exclusions = _add_exclusion_via_cli(config_path, "mytests")

        assert result["intercepted"] is True
        assert "added successfully" in result["output"].lower()
        assert "mytests/" in stored_exclusions

        printed_path = _extract_quoted_path(result["output"])
        assert printed_path in stored_exclusions, (
            f"Confirmation message quoted '{printed_path}' but the YAML "
            f"actually stored {stored_exclusions!r}"
        )
        assert printed_path == "mytests/"

    def test_add_then_remove_using_exact_confirmation_message_path_succeeds(
        self, tmp_path
    ):
        """FAILS PRE-FIX: a user copy-pasting the exact quoted path from
        the add confirmation into 'excluded-paths remove' must succeed —
        this is the concrete user-facing symptom from issue #104 (the
        printed '*.generated.cs/' cannot be found because the YAML
        actually stored '*.generated.cs').
        """
        config_path = tmp_path / "excluded_paths.yaml"

        add_result, _ = _add_exclusion_via_cli(config_path, "*.generated.cs")
        printed_path = _extract_quoted_path(add_result["output"])

        with patch("pacemaker.constants.DEFAULT_EXCLUDED_PATHS_PATH", str(config_path)):
            remove_result = user_commands.handle_user_prompt(
                f"pace-maker excluded-paths remove {printed_path}",
                "/tmp/config.json",
                "/tmp/db.sqlite",
            )

        assert remove_result["intercepted"] is True
        assert "removed successfully" in remove_result["output"].lower(), (
            f"Removing the exact path from the add confirmation failed: "
            f"{remove_result['output']!r}"
        )
