#!/usr/bin/env python3
"""
Unit tests for excluded_paths.py filename-suffix pattern support (issue #92).

Story #92 needs a negative-signal matcher that can express "this filename
ends with X" (e.g. ``*_test.go``, ``*.Tests.csproj``) — something
``is_excluded_path``'s original directory-substring-only matcher could not
do (Go's ``_test.go`` has no directory-level signal at all: test files sit
in the SAME directory as production code, under the same ``go.mod``).

These tests cover the EXTENDED matcher only:
  - New: patterns beginning with ``*`` are treated as filename-suffix
    matches against the file's basename.
  - Regression-locked: existing directory-substring patterns (``tests/``,
    ``fixtures/``, etc.) are completely unaffected.
"""

import os
import tempfile

import pytest

from pacemaker import excluded_paths


class TestMatchesFilenamePatternSuffix:
    """Unit tests for the new standalone suffix-pattern matcher."""

    def test_suffix_pattern_matches_filename_ending_with_suffix(self):
        assert (
            excluded_paths.matches_filename_pattern("foo_test.go", ["*_test.go"])
            is True
        )

    def test_suffix_pattern_does_not_match_non_matching_filename(self):
        assert excluded_paths.matches_filename_pattern("foo.go", ["*_test.go"]) is False

    def test_empty_patterns_list_never_matches(self):
        assert excluded_paths.matches_filename_pattern("foo_test.go", []) is False


class TestMatchesFilenamePatternExact:
    """Unit tests for exact (non-'*') pattern matching."""

    def test_exact_pattern_matches_identical_filename(self):
        assert excluded_paths.matches_filename_pattern("go.mod", ["go.mod"]) is True

    def test_exact_pattern_does_not_match_suffix_superset(self):
        assert (
            excluded_paths.matches_filename_pattern("go.mod.bak", ["go.mod"]) is False
        )


class TestMatchesFilenamePatternDotnetTestProjects:
    """Unit tests for the .NET test-project suffix patterns specifically."""

    def test_matches_dotnet_test_project_csproj(self):
        assert (
            excluded_paths.matches_filename_pattern(
                "MyProject.Tests.csproj",
                ["*.Tests.csproj", "*.Test.csproj", "*Tests.sln"],
            )
            is True
        )

    def test_matches_dotnet_test_project_sln_no_dot(self):
        assert (
            excluded_paths.matches_filename_pattern(
                "MyProjectTests.sln", ["*.Tests.csproj", "*.Test.csproj", "*Tests.sln"]
            )
            is True
        )

    def test_regular_csproj_does_not_match_test_project_patterns(self):
        """A normal .csproj (not a dedicated test project) must NOT match."""
        assert (
            excluded_paths.matches_filename_pattern(
                "MyProject.csproj", ["*.Tests.csproj", "*.Test.csproj", "*Tests.sln"]
            )
            is False
        )


class TestIsExcludedPathSuffixPatterns:
    """New: is_excluded_path() accepts '*'-prefixed suffix patterns."""

    def test_suffix_pattern_excludes_matching_go_test_file(self):
        exclusions = ["*_test.go"]
        assert excluded_paths.is_excluded_path("pkg/foo_test.go", exclusions) is True

    def test_suffix_pattern_does_not_exclude_non_test_go_file(self):
        exclusions = ["*_test.go"]
        assert excluded_paths.is_excluded_path("pkg/foo.go", exclusions) is False

    def test_suffix_pattern_matches_basename_not_full_path(self):
        """A suffix pattern must match the basename, not some substring mid-path."""
        exclusions = ["*_test.go"]
        # Directory literally contains "_test.go" as a substring but the
        # basename does not end with it — must NOT match.
        assert (
            excluded_paths.is_excluded_path("pkg/_test.go_dir/foo.go", exclusions)
            is False
        )


class TestIsExcludedPathSuffixPatternsMixed:
    """New: suffix patterns compose correctly with directory patterns."""

    def test_mixed_suffix_and_directory_patterns_both_apply(self):
        exclusions = ["tests/", "*_test.go"]
        assert excluded_paths.is_excluded_path("tests/helper.py", exclusions) is True
        assert excluded_paths.is_excluded_path("pkg/foo_test.go", exclusions) is True
        assert excluded_paths.is_excluded_path("pkg/foo.go", exclusions) is False


class TestIsExcludedPathDirectorySubstringRegression:
    """Regression lock: existing directory-substring behavior is unaffected."""

    @pytest.mark.parametrize(
        "file_path,exclusion,expected",
        [
            ("tests/test_auth.py", "tests/", True),
            ("test/unit/test_utils.py", "test/", True),
            ("src/fixtures/data.py", "fixtures/", True),
            ("vendor/pkg/lib.go", "vendor/", True),
            ("node_modules/react/index.js", "node_modules/", True),
            ("dist/bundle.js", "dist/", True),
            ("build/output.o", "build/", True),
            (".git/HEAD", ".git/", True),
            ("src/auth.py", "tests/", False),
        ],
    )
    def test_directory_substring_matching_unchanged(
        self, file_path, exclusion, expected
    ):
        assert excluded_paths.is_excluded_path(file_path, [exclusion]) is expected

    def test_default_exclusions_still_all_directory_patterns(self):
        """get_default_exclusions() must not have gained any '*' pattern —
        the new suffix-pattern capability is opt-in, not a default change."""
        defaults = excluded_paths.get_default_exclusions()
        assert all(not d.startswith("*") for d in defaults)


class TestAddExclusionSuffixPatternRoundTrip:
    """Issue #92 review finding F-6: add_exclusion() unconditionally
    appended a trailing '/' via _normalize_path(), which makes any
    '*'-prefixed filename-suffix pattern permanently unmatchable once
    added through the CLI (`pace-maker excluded-paths add *.min.js`
    would be stored as '*.min.js/', and matches_filename_pattern's
    suffix check against a real filename can never end in '/')."""

    def test_cli_added_suffix_pattern_round_trips_without_trailing_slash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "excluded_paths.yaml")

            excluded_paths.add_exclusion(config_path, "*.min.js")

            exclusions = excluded_paths.load_exclusions(config_path)
            assert "*.min.js" in exclusions
            assert "*.min.js/" not in exclusions

    def test_cli_added_suffix_pattern_actually_matches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "excluded_paths.yaml")

            excluded_paths.add_exclusion(config_path, "*_test.go")

            exclusions = excluded_paths.load_exclusions(config_path)
            assert (
                excluded_paths.is_excluded_path("pkg/widget_test.go", exclusions)
                is True
            )

    def test_directory_pattern_still_gets_trailing_slash_normalized(self):
        """Regression lock: the fix must be scoped to '*'-prefixed
        patterns only — plain directory entries still get normalized with
        a trailing slash exactly as before."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "excluded_paths.yaml")

            excluded_paths.add_exclusion(config_path, ".custom")

            exclusions = excluded_paths.load_exclusions(config_path)
            assert ".custom/" in exclusions
            assert ".custom" not in exclusions
