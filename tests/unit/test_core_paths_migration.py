#!/usr/bin/env python3
"""
Unit tests for core_paths.py's one-time story #92 migration.

Because core_paths.yaml was dead code before this story (only read by an
unreachable prompt-generation path), nobody was benefiting from whatever
they'd saved in it. Wiring it into the live hook path without a migration
would take an inert file and — for anyone who'd saved a customized list
before this story shipped — suddenly start enforcing it verbatim, silently
WITHOUT this story's 4 new default words (app/, routes/, services/,
internal/), since a saved file is a fixed on-disk snapshot that doesn't
automatically pick up new code defaults.

The primary fixture below is modeled on a REAL local file found during spec
review (not synthetic): 8 entries — 6 of the 7 pre-story defaults (missing
lib/, for unknown reasons predating this story) plus 2 hand-added custom
entries (myapp/, custom/).
"""

import os

import yaml

from pacemaker import core_paths


REAL_FIXTURE_PATHS = [
    "src/",
    "code/",
    "core/",
    "source/",
    "libraries/",
    "kernel/",
    "myapp/",
    "custom/",
]
REAL_FIXTURE_ENTRY_COUNT = len(REAL_FIXTURE_PATHS)
NEW_STORY_92_WORDS = ["app/", "routes/", "services/", "internal/"]


def _write_yaml(config_path, data):
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def _read_yaml(config_path):
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


class TestGetDefaultPathsExpanded:
    def test_default_paths_include_all_four_new_words(self):
        defaults = core_paths.get_default_paths()
        assert "app/" in defaults
        assert "routes/" in defaults
        assert "services/" in defaults
        assert "internal/" in defaults

    def test_default_paths_still_include_all_seven_original_words(self):
        defaults = core_paths.get_default_paths()
        for word in [
            "src/",
            "lib/",
            "code/",
            "core/",
            "source/",
            "libraries/",
            "kernel/",
        ]:
            assert word in defaults

    def test_default_paths_has_exactly_eleven_entries(self):
        assert len(core_paths.get_default_paths()) == 11


class TestMigrateRealFixture:
    """The primary fixture: modeled on the real local file found during
    spec review — 6 of 7 pre-story defaults (missing lib/) plus 2 custom
    entries (myapp/, custom/)."""

    def test_migration_appends_missing_new_words(self, tmp_path):
        config_path = str(tmp_path / "core_paths.yaml")
        _write_yaml(config_path, {"paths": list(REAL_FIXTURE_PATHS)})

        core_paths.migrate_if_needed(config_path)

        result = _read_yaml(config_path)
        for word in NEW_STORY_92_WORDS:
            assert word in result["paths"]

    def test_migration_leaves_all_prior_entries_untouched(self, tmp_path):
        """Every original entry — including custom ones and lib/'s
        absence — is left exactly as-is; only the 4 new words are
        appended."""
        config_path = str(tmp_path / "core_paths.yaml")
        _write_yaml(config_path, {"paths": list(REAL_FIXTURE_PATHS)})

        core_paths.migrate_if_needed(config_path)

        result = _read_yaml(config_path)
        assert result["paths"][:REAL_FIXTURE_ENTRY_COUNT] == REAL_FIXTURE_PATHS
        assert "lib/" not in result["paths"]

    def test_migration_result_matches_exact_expected_final_list(self, tmp_path):
        """Applied to the exact real fixture, the migration result must be
        the precise 12-entry list documented in issue #92."""
        config_path = str(tmp_path / "core_paths.yaml")
        _write_yaml(config_path, {"paths": list(REAL_FIXTURE_PATHS)})

        core_paths.migrate_if_needed(config_path)

        result = _read_yaml(config_path)
        expected = REAL_FIXTURE_PATHS + NEW_STORY_92_WORDS
        assert result["paths"] == expected


class TestMigrateMarkerAndIdempotency:
    def test_migration_sets_marker_field(self, tmp_path):
        config_path = str(tmp_path / "core_paths.yaml")
        _write_yaml(config_path, {"paths": list(REAL_FIXTURE_PATHS)})

        core_paths.migrate_if_needed(config_path)

        result = _read_yaml(config_path)
        assert result.get(core_paths.MIGRATION_MARKER_KEY) is True

    def test_migration_is_idempotent_on_repeated_calls(self, tmp_path):
        config_path = str(tmp_path / "core_paths.yaml")
        _write_yaml(config_path, {"paths": list(REAL_FIXTURE_PATHS)})

        core_paths.migrate_if_needed(config_path)
        core_paths.migrate_if_needed(config_path)
        core_paths.migrate_if_needed(config_path)

        result = _read_yaml(config_path)
        # No duplicates — each of the 4 new words appears exactly once.
        for word in NEW_STORY_92_WORDS:
            assert result["paths"].count(word) == 1

    def test_migration_does_not_readd_word_manually_removed_after_migration(
        self, tmp_path
    ):
        """The one-time marker prevents a later manual removal of a
        migrated word from being silently re-added on a future load.

        This exercises the REAL CLI removal path (core_paths.remove_path —
        what `pace-maker core-paths remove` calls), not a hand-written
        dict rewrite. A hand-write bypasses _write_paths() entirely and
        would pass vacuously even if _write_paths() dropped the migration
        marker on every real CLI write (issue #92 review finding F-1)."""
        config_path = str(tmp_path / "core_paths.yaml")
        _write_yaml(config_path, {"paths": list(REAL_FIXTURE_PATHS)})

        core_paths.migrate_if_needed(config_path)

        # User removes "app/" via the real CLI-backing function.
        core_paths.remove_path(config_path, "app/")

        # The marker must survive the real write path, not just a
        # hand-crafted one.
        after_remove = _read_yaml(config_path)
        assert after_remove.get(core_paths.MIGRATION_MARKER_KEY) is True

        # Migration runs again (e.g. next hook load) — marker already set.
        core_paths.migrate_if_needed(config_path)

        final = _read_yaml(config_path)
        assert "app/" not in final["paths"]


class TestWritePathsPreservesMigrationMarker:
    """Issue #92 review finding F-1 (BLOCKING): _write_paths() previously
    serialized only {"paths": paths}, silently dropping any other
    top-level key already in the loaded YAML — including
    _migrated_story_92. Reproduced by: migrate once (marker set), then run
    `pace-maker core-paths remove app/` (-> core_paths.remove_path), then
    reload -> the marker vanishes and a later migrate_if_needed() call
    silently re-adds "app/", exactly the failure mode AC #10 exists to
    prevent. These tests call the REAL CLI-backing functions
    (add_path/remove_path), not a hand-written dict rewrite."""

    def test_add_path_preserves_existing_migration_marker(self, tmp_path):
        config_path = str(tmp_path / "core_paths.yaml")
        _write_yaml(
            config_path,
            {"paths": ["src/"], core_paths.MIGRATION_MARKER_KEY: True},
        )

        core_paths.add_path(config_path, "custom/")

        result = _read_yaml(config_path)
        assert result.get(core_paths.MIGRATION_MARKER_KEY) is True
        assert "custom/" in result["paths"]

    def test_remove_path_preserves_existing_migration_marker(self, tmp_path):
        config_path = str(tmp_path / "core_paths.yaml")
        _write_yaml(
            config_path,
            {
                "paths": ["src/", "app/"],
                core_paths.MIGRATION_MARKER_KEY: True,
            },
        )

        core_paths.remove_path(config_path, "app/")

        result = _read_yaml(config_path)
        assert result.get(core_paths.MIGRATION_MARKER_KEY) is True
        assert "app/" not in result["paths"]

    def test_remove_then_migrate_again_does_not_readd_removed_word(self, tmp_path):
        """The full reproduction from the review finding: migrate once,
        CLI-remove a migrated word, reload -> marker must have survived,
        so a subsequent migrate_if_needed() call must NOT silently
        re-add the removed word."""
        config_path = str(tmp_path / "core_paths.yaml")
        _write_yaml(config_path, {"paths": list(REAL_FIXTURE_PATHS)})

        core_paths.migrate_if_needed(config_path)
        core_paths.remove_path(config_path, "app/")
        core_paths.migrate_if_needed(config_path)

        result = _read_yaml(config_path)
        assert "app/" not in result["paths"]

    def test_add_path_on_file_with_no_marker_still_writes_correctly(self, tmp_path):
        """A file that was never migrated (no marker key at all) must
        still round-trip correctly through add_path — no marker key is
        spuriously introduced."""
        config_path = str(tmp_path / "core_paths.yaml")
        _write_yaml(config_path, {"paths": ["src/"]})

        core_paths.add_path(config_path, "custom/")

        result = _read_yaml(config_path)
        assert core_paths.MIGRATION_MARKER_KEY not in result
        assert "custom/" in result["paths"]


class TestMigrateEdgeCases:
    def test_migration_noop_when_file_does_not_exist(self, tmp_path):
        config_path = str(tmp_path / "nonexistent.yaml")

        core_paths.migrate_if_needed(config_path)

        assert not os.path.exists(config_path)

    def test_migration_noop_when_already_migrated_marker_present(self, tmp_path):
        config_path = str(tmp_path / "core_paths.yaml")
        # Marker already set with a deliberately-reduced list (simulating
        # a user's post-migration removal) — must be left untouched.
        _write_yaml(
            config_path,
            {"paths": ["src/", "myapp/"], core_paths.MIGRATION_MARKER_KEY: True},
        )

        core_paths.migrate_if_needed(config_path)

        result = _read_yaml(config_path)
        assert result["paths"] == ["src/", "myapp/"]

    def test_migration_with_empty_paths_list_marks_migrated_without_synthesizing(
        self, tmp_path
    ):
        """An explicitly-empty paths list means 'no customization' — the
        migration must not synthesize a partial 4-word list; load_paths()'s
        existing empty-list fallback to the (now 11-entry) defaults must
        keep applying, and the marker must still be set so this counts as
        migrated (avoiding re-running the check forever)."""
        config_path = str(tmp_path / "core_paths.yaml")
        _write_yaml(config_path, {"paths": []})

        core_paths.migrate_if_needed(config_path)

        loaded = core_paths.load_paths(config_path)
        assert loaded == core_paths.get_default_paths()
        on_disk = _read_yaml(config_path)
        assert on_disk.get(core_paths.MIGRATION_MARKER_KEY) is True


class TestMigrateMalformedFile:
    def test_migration_handles_malformed_yaml_gracefully(self, tmp_path):
        config_path = str(tmp_path / "core_paths.yaml")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w") as f:
            f.write(": : : not valid yaml : : :\n[unterminated")

        # Must not raise — fail-safe no-op.
        core_paths.migrate_if_needed(config_path)


class TestMigrateReadAndWriteFailureCoverage:
    """Closes coverage gaps on migrate_if_needed()'s remaining defensive
    branches: a valid-but-non-dict YAML top level, and an OSError raised
    while writing the migrated file back to disk."""

    def test_migration_noop_when_yaml_top_level_is_not_a_dict(self, tmp_path):
        """Valid YAML that parses to a non-dict (e.g. a bare list) must be
        a safe no-op — not a crash, not a misinterpreted 'paths' lookup."""
        config_path = str(tmp_path / "core_paths.yaml")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w") as f:
            f.write("- src/\n- lib/\n")

        core_paths.migrate_if_needed(config_path)

        with open(config_path, "r") as f:
            assert f.read() == "- src/\n- lib/\n"

    def test_migration_noop_on_os_error_during_write(self, tmp_path, monkeypatch):
        """A write failure (e.g. permission denied) must be caught and
        logged, not propagated — the on-disk file is simply left as-is."""
        config_path = str(tmp_path / "core_paths.yaml")
        _write_yaml(config_path, {"paths": list(REAL_FIXTURE_PATHS)})

        def _raise_os_error(*args, **kwargs):
            raise OSError("simulated permission denied")

        monkeypatch.setattr(os, "makedirs", _raise_os_error)

        # Must not raise — fail-safe no-op on the write side.
        core_paths.migrate_if_needed(config_path)

        # File untouched since the write never completed.
        result = _read_yaml(config_path)
        assert result["paths"] == REAL_FIXTURE_PATHS
        assert core_paths.MIGRATION_MARKER_KEY not in result


class TestLoadPathsWithMigration:
    def test_load_paths_with_migration_returns_migrated_list(self, tmp_path):
        config_path = str(tmp_path / "core_paths.yaml")
        _write_yaml(config_path, {"paths": list(REAL_FIXTURE_PATHS)})

        result = core_paths.load_paths_with_migration(config_path)

        assert "app/" in result
        assert "myapp/" in result

    def test_load_paths_with_migration_on_missing_file_returns_defaults(self, tmp_path):
        config_path = str(tmp_path / "nonexistent.yaml")

        result = core_paths.load_paths_with_migration(config_path)

        assert result == core_paths.get_default_paths()

    def test_load_paths_without_migration_is_unaffected(self, tmp_path):
        """Plain load_paths() (used by the CLI) must NOT perform migration
        as a side effect — only the new load_paths_with_migration() does."""
        config_path = str(tmp_path / "core_paths.yaml")
        _write_yaml(config_path, {"paths": list(REAL_FIXTURE_PATHS)})

        result = core_paths.load_paths(config_path)

        assert result == REAL_FIXTURE_PATHS
        on_disk = _read_yaml(config_path)
        assert core_paths.MIGRATION_MARKER_KEY not in on_disk
