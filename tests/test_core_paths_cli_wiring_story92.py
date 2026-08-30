#!/usr/bin/env python3
"""
Integration test for issue #92 review finding F-3.

Acceptance Criteria #8 requires that `pace-maker core-paths add <segment>`
immediately affects `_is_core_path()`'s decision — the central claim of
story #92 (core_paths.yaml is no longer dead config; see the "Wiring fix"
section in CLAUDE.md). Before this test, that claim had zero end-to-end
coverage: other tests exercise `_is_core_path()` directly with in-memory
segment lists (tests/unit/test_is_core_path_story92.py), or exercise
`core_paths.add_path()` directly (tests/unit/test_core_paths.py) — nothing
drove the REAL CLI command dispatch (user_commands.execute_command) and
then re-loaded segments the same way the real hook does
(core_paths.load_paths_with_migration) to prove the two are actually wired
together.

IMPORTANT: `_execute_core_paths()` (the function `execute_command("core-
paths", ...)` dispatches to) ignores the `config_path` argument entirely —
it always reads/writes `pacemaker.constants.DEFAULT_CORE_PATHS_PATH` via a
local (call-time) import. tests/conftest.py's autouse `_guard_production_db`
fixture already redirects that constant to a fake per-test tmp path, so
these tests read/write through that same redirected constant rather than
an unrelated tmp_path file.
"""

from pacemaker import constants, core_paths, user_commands
from pacemaker.intent_validator import _regex_stage1_check
from pacemaker.excluded_paths import get_default_exclusions
from pacemaker.extension_registry import get_default_extensions


def _stage1_verdict(file_path: str) -> str:
    """Mirror the real hook wiring in validate_intent_and_code(): load
    core_path_segments via load_paths_with_migration() from the SAME path
    the CLI just wrote to (constants.DEFAULT_CORE_PATHS_PATH), then run
    Stage 1 with an INTENT-bearing, file-mentioning message that
    deliberately omits a "Test coverage:" declaration — so the verdict
    flips between YES (not core) and NO_TDD (core, TDD required but not
    declared) purely based on whether the segment added via the CLI is
    recognized."""
    segments = core_paths.load_paths_with_migration(constants.DEFAULT_CORE_PATHS_PATH)
    current_message = (
        f"INTENT: Modify {file_path} to add a new handler function, "
        "to support widget management."
    )
    return _regex_stage1_check(
        current_message,
        file_path,
        get_default_exclusions(),
        segments,
        get_default_extensions(),
    )


class TestCorePathsCliAddImmediatelyAffectsGating:
    def test_add_new_segment_via_real_cli_flips_stage1_from_yes_to_no_tdd(
        self, tmp_path
    ):
        # A file under a real tmp_path directory tree that has no project
        # marker anywhere in its ancestry, so Layer 2 cannot rescue it —
        # only Layer 1's word-list match can flag it as core.
        target_dir = tmp_path / "widgets"
        target_dir.mkdir()
        file_path = str(target_dir / "handler.py")

        # Before: "widgets/" is not a default core segment and the config
        # file doesn't exist yet (defaults apply) -> not core -> Stage 1
        # says YES (no TDD declaration required).
        before = _stage1_verdict(file_path)
        assert before == "YES"

        # Real CLI command dispatch — exactly what `pace-maker core-paths
        # add widgets` runs.
        result = user_commands.execute_command(
            "core-paths", constants.DEFAULT_CORE_PATHS_PATH, subcommand="add widgets"
        )
        assert result["success"] is True

        # After: "widgets/" is now a core segment -> Stage 1 says NO_TDD
        # (core path, TDD required, not declared).
        after = _stage1_verdict(file_path)
        assert after == "NO_TDD"

    def test_remove_segment_via_real_cli_flips_stage1_back(self, tmp_path):
        """The inverse direction: removing a segment via the real CLI
        must also immediately affect the gate, proving the wiring isn't
        one-directional (e.g. cached after the first load)."""
        target_dir = tmp_path / "gadgets"
        target_dir.mkdir()
        file_path = str(target_dir / "handler.py")

        # Seed a config (at the real DEFAULT_CORE_PATHS_PATH, redirected
        # by conftest to a fake path) that already has "gadgets/" as a
        # core segment.
        core_paths.add_path(constants.DEFAULT_CORE_PATHS_PATH, "gadgets/")
        seeded = _stage1_verdict(file_path)
        assert seeded == "NO_TDD"

        result = user_commands.execute_command(
            "core-paths",
            constants.DEFAULT_CORE_PATHS_PATH,
            subcommand="remove gadgets/",
        )
        assert result["success"] is True

        after = _stage1_verdict(file_path)
        assert after == "YES"
