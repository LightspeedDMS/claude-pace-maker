#!/usr/bin/env python3
"""
Regression tests for issue #92 round-2 code review findings N-1 and N-2.

Both findings were self-inflicted by the round-1 fix pass:

N-1 (HIGH, fail-open, BLOCKING): F-6's fix made add_exclusion() stop
forcing a trailing slash so filename-suffix patterns like "*_test.go"
work. That accidentally turned `pace-maker excluded-paths add *` into a
working, silent, repo-wide OFF SWITCH for the entire TDD gate:
excluded_paths.py stored a bare "*" verbatim, and
matches_filename_pattern computed `filename.endswith("")` -> True for
every filename.

N-2 (LOW-MED): F-5's fix (rejecting a degenerate "/" path) landed only at
the CLI's add_path() layer, not at the actual regex-construction site in
intent_validator._is_core_path(): a hand-edited core_paths.yaml (bypassing
the CLI entirely, which this repo's own CLAUDE.md documents as a normal
path) can still smuggle in a degenerate segment like "/" and poison the
Layer 1 regex into matching every path.
"""

import os

import pytest
import yaml

from pacemaker import constants, core_paths, excluded_paths, user_commands
from pacemaker.excluded_paths import get_default_exclusions, matches_filename_pattern
from pacemaker.extension_registry import get_default_extensions
from pacemaker.intent_validator import _is_core_path, _regex_stage1_check


# ---------------------------------------------------------------------------
# N-1: bare "*" exclusion pattern must never become an allow-everything gate
# ---------------------------------------------------------------------------


class TestMatchesFilenamePatternRefusesDegenerateStar:
    """Defense in depth: matches_filename_pattern itself must never treat
    an empty-after-glob-strip pattern as "match everything", even if some
    other equally-degenerate pattern slips past validation elsewhere."""

    def test_bare_star_does_not_match_every_filename(self):
        assert matches_filename_pattern("anything.py", ["*"]) is False

    def test_bare_star_does_not_match_empty_filename(self):
        assert matches_filename_pattern("", ["*"]) is False

    def test_bare_star_mixed_with_real_pattern_still_matches_real_pattern(self):
        # A degenerate "*" alongside a legitimate pattern must not prevent
        # the legitimate pattern from matching.
        assert matches_filename_pattern("foo_test.go", ["*", "*_test.go"]) is True

    def test_real_suffix_pattern_unaffected_by_star_guard(self):
        assert matches_filename_pattern("foo_test.go", ["*_test.go"]) is True


class TestAddExclusionRejectsBareStar:
    """add_exclusion() must reject a degenerate '*' pattern at the CLI
    validation layer, mirroring the guard that already exists in
    core_paths.add_path() for the analogous '/' poisoning case (F-5)."""

    def test_add_exclusion_rejects_bare_star(self, tmp_path):
        config_path = str(tmp_path / "excluded_paths.yaml")

        with pytest.raises(ValueError):
            excluded_paths.add_exclusion(config_path, "*")

        # Must not have been written to disk.
        assert not os.path.exists(config_path)

    def test_add_exclusion_still_accepts_real_suffix_pattern(self, tmp_path):
        """Regression guard: the F-6 fix (suffix patterns bypassing
        trailing-slash normalization) must keep working."""
        config_path = str(tmp_path / "excluded_paths.yaml")

        excluded_paths.add_exclusion(config_path, "*_test.go")

        exclusions = excluded_paths.load_exclusions(config_path)
        assert "*_test.go" in exclusions


class TestExcludedPathsAddStarViaRealCliRejected:
    """End-to-end: `pace-maker excluded-paths add *` must be rejected via
    the real CLI dispatch, not just the underlying library function."""

    def test_cli_rejects_bare_star(self, tmp_path):
        config_path = tmp_path / "excluded_paths.yaml"

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(constants, "DEFAULT_EXCLUDED_PATHS_PATH", str(config_path))
            result = user_commands.execute_command(
                "excluded-paths", str(config_path), subcommand="add *"
            )

        assert result["success"] is False

    def test_cli_gate_not_poisoned_after_rejected_star_add(self, tmp_path):
        """Even attempting (and failing) to add '*' must never leave the
        TDD gate open: a core-path src file with no test declaration must
        still be flagged NO_TDD afterward."""
        config_path = tmp_path / "excluded_paths.yaml"

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(constants, "DEFAULT_EXCLUDED_PATHS_PATH", str(config_path))
            user_commands.execute_command(
                "excluded-paths", str(config_path), subcommand="add *"
            )

        exclusions = excluded_paths.load_exclusions(str(config_path))
        current_message = (
            "INTENT: Modify src/auth.py to add a validate_token() function "
            "that checks JWT expiration, to fix a security bug."
        )
        verdict = _regex_stage1_check(
            current_message,
            "src/auth.py",
            exclusions,
            core_paths.get_default_paths(),
            get_default_extensions(),
        )
        assert verdict == "NO_TDD"


class TestGateNotPoisonedByHandEditedStarExclusion:
    """Defense in depth for a hand-edited excluded_paths.yaml containing a
    bare '*' (bypassing add_exclusion()'s CLI-layer guard entirely) — the
    gate must still not degrade to allow-everything."""

    def test_hand_edited_star_exclusion_does_not_disable_gate(self, tmp_path):
        config_path = tmp_path / "excluded_paths.yaml"
        with open(config_path, "w") as f:
            yaml.safe_dump({"excluded_paths": ["*"]}, f)

        exclusions = excluded_paths.load_exclusions(str(config_path))
        assert exclusions == ["*"]

        current_message = (
            "INTENT: Modify src/auth.py to add a validate_token() function "
            "that checks JWT expiration, to fix a security bug."
        )
        verdict = _regex_stage1_check(
            current_message,
            "src/auth.py",
            exclusions,
            core_paths.get_default_paths(),
            get_default_extensions(),
        )
        assert verdict == "NO_TDD"


# ---------------------------------------------------------------------------
# N-2: degenerate core-path segment must not poison the regex, even when
# it enters via a hand-edited core_paths.yaml (bypassing the CLI entirely)
# ---------------------------------------------------------------------------


class TestIsCorePathIgnoresDegenerateSegment:
    """Unit-level: _is_core_path()'s Layer 1 regex builder must filter out
    a segment that reduces to an empty string after rstrip("/"), instead
    of turning it into an empty regex alternative that matches every
    path."""

    def test_degenerate_slash_segment_alone_never_matches(self, tmp_path):
        target_dir = tmp_path / "widgets"
        target_dir.mkdir()
        file_path = str(target_dir / "handler.py")

        # "/" is the only configured segment and reduces to "" after
        # rstrip("/") -- must never match ANY path.
        assert (
            _is_core_path(file_path, ["/"], get_default_exclusions(), [".py"]) is False
        )

    def test_degenerate_slash_segment_mixed_with_real_segment(self, tmp_path):
        target_dir = tmp_path / "widgets"
        target_dir.mkdir()
        file_path = str(target_dir / "handler.py")

        # A degenerate "/" alongside a real "src/" segment must not
        # poison matching for paths that don't actually contain "src/".
        assert (
            _is_core_path(file_path, ["src/", "/"], get_default_exclusions(), [".py"])
            is False
        )

    def test_real_segment_still_matches_with_degenerate_segment_present(self, tmp_path):
        target_dir = tmp_path / "src"
        target_dir.mkdir()
        file_path = str(target_dir / "handler.py")

        # Regression guard: filtering out "/" must not break real matches.
        assert (
            _is_core_path(file_path, ["src/", "/"], get_default_exclusions(), [".py"])
            is True
        )

    def test_empty_string_segment_still_filtered(self, tmp_path):
        target_dir = tmp_path / "widgets"
        target_dir.mkdir()
        file_path = str(target_dir / "handler.py")

        assert (
            _is_core_path(file_path, ["", "/"], get_default_exclusions(), [".py"])
            is False
        )


class TestHandEditedCorePathsYamlDegenerateSegment:
    """Reproduces the reviewer's exact repro: a hand-edited core_paths.yaml
    (bypassing add_path()'s CLI-layer guard entirely) containing a
    degenerate '/' segment must not poison _is_core_path() against the
    real deployed module."""

    def test_hand_edited_slash_segment_does_not_flag_unrelated_file_as_core(
        self, tmp_path
    ):
        config_path = tmp_path / "core_paths.yaml"
        with open(config_path, "w") as f:
            yaml.safe_dump({"paths": ["/"]}, f)

        segments = core_paths.load_paths(str(config_path))
        assert segments == ["/"]

        target_dir = tmp_path / "widgets"
        target_dir.mkdir()
        file_path = str(target_dir / "handler.py")

        current_message = (
            f"INTENT: Modify {file_path} to add a handler function, "
            "to support widget management."
        )
        verdict = _regex_stage1_check(
            current_message,
            file_path,
            get_default_exclusions(),
            segments,
            get_default_extensions(),
        )
        # Not a core path (no real segment matches, no project marker) ->
        # Stage 1 must say YES, not be poisoned into NO_TDD for every path.
        assert verdict == "YES"

    def test_hand_edited_slash_segment_mixed_with_real_word_still_gates_real_path(
        self, tmp_path
    ):
        """Regression guard: filtering the degenerate segment must not
        disable gating for a file that IS genuinely under a real core
        segment also present in the hand-edited file."""
        config_path = tmp_path / "core_paths.yaml"
        with open(config_path, "w") as f:
            yaml.safe_dump({"paths": ["src/", "/"]}, f)

        segments = core_paths.load_paths(str(config_path))

        target_dir = tmp_path / "src"
        target_dir.mkdir()
        file_path = str(target_dir / "handler.py")

        current_message = (
            f"INTENT: Modify {file_path} to add a handler function, "
            "to support widget management."
        )
        verdict = _regex_stage1_check(
            current_message,
            file_path,
            get_default_exclusions(),
            segments,
            get_default_extensions(),
        )
        assert verdict == "NO_TDD"
