#!/usr/bin/env python3
"""
Unit tests for core_path_markers module (issue #92, Layer 2 / Layer 2c).

Covers the structural marker-file fallback: an upward directory walk from a
file's containing directory to the filesystem root, looking for per-ecosystem
project markers (*.csproj, *.sln, pyproject.toml, setup.py, package.json,
build.gradle, build.gradle.kts, pom.xml, go.mod, Cargo.toml), plus the
test-project-marker exclusion (Layer 2c: a found marker whose own filename
matches a test-project naming pattern — e.g. MyProject.Tests.csproj — is NOT
production code) and the filename-suffix test-file exclusion (e.g. _test.go,
which has no directory-level signal at all).

No artificial depth cap: the walk climbs to the filesystem root. Termination
is proven by construction (a real filesystem path has a finite number of
ancestors), not by a fixed number — see TestFindProjectMarkerTermination.
"""

import pytest

from pacemaker import core_path_markers


# ---------------------------------------------------------------------------
# matches_test_filename_pattern (Layer 0 filename-suffix test-file check)
# ---------------------------------------------------------------------------


class TestMatchesTestFilenamePattern:
    def test_go_test_file_matches(self):
        assert (
            core_path_markers.matches_test_filename_pattern("pkg/foo_test.go") is True
        )

    def test_non_test_go_file_does_not_match(self):
        assert core_path_markers.matches_test_filename_pattern("pkg/foo.go") is False

    def test_non_go_file_with_test_suffix_shape_does_not_match(self):
        """Only the *_test.go pattern is defined — a Python _test.py file
        is not covered by this filename-suffix check (Go has no directory
        signal; Python test files are covered by the tests/ directory
        exclusion already)."""
        assert (
            core_path_markers.matches_test_filename_pattern("pkg/foo_test.py") is False
        )


# ---------------------------------------------------------------------------
# matches_test_project_marker (Layer 2c)
# ---------------------------------------------------------------------------


class TestMatchesTestProjectMarkerPositive:
    def test_tests_csproj_matches(self):
        assert (
            core_path_markers.matches_test_project_marker("MyProject.Tests.csproj")
            is True
        )

    def test_test_csproj_matches(self):
        assert (
            core_path_markers.matches_test_project_marker("MyProject.Test.csproj")
            is True
        )

    def test_tests_sln_matches_without_dot(self):
        assert (
            core_path_markers.matches_test_project_marker("MyProjectTests.sln") is True
        )


class TestMatchesTestProjectMarkerNegative:
    def test_regular_csproj_does_not_match(self):
        assert (
            core_path_markers.matches_test_project_marker("MyProject.csproj") is False
        )

    def test_regular_sln_does_not_match(self):
        assert core_path_markers.matches_test_project_marker("MySolution.sln") is False


# ---------------------------------------------------------------------------
# find_project_marker — positive cases per ecosystem
# ---------------------------------------------------------------------------


class TestFindProjectMarkerDotnet:
    def test_finds_csproj_in_same_dir(self, tmp_path):
        proj = tmp_path / "MyProject"
        proj.mkdir()
        (proj / "MyProject.csproj").write_text("<Project/>")
        target = proj / "Program.cs"
        target.write_text("class Program {}")

        marker = core_path_markers.find_project_marker(str(target))
        assert marker == "MyProject.csproj"

    def test_finds_csproj_in_ancestor_dir(self, tmp_path):
        """No src/ wrapper — the .cs file sits directly inside the project
        dir, but the marker walk climbs from a NESTED subdirectory too."""
        proj = tmp_path / "MyProject"
        nested = proj / "Sub" / "Deeper"
        nested.mkdir(parents=True)
        (proj / "MyProject.csproj").write_text("<Project/>")
        target = nested / "Widget.cs"
        target.write_text("class Widget {}")

        marker = core_path_markers.find_project_marker(str(target))
        assert marker == "MyProject.csproj"

    def test_finds_sln_marker(self, tmp_path):
        proj = tmp_path / "Solution"
        proj.mkdir()
        (proj / "MySolution.sln").write_text("")
        target = proj / "readme_helper.cs"
        target.write_text("")

        marker = core_path_markers.find_project_marker(str(target))
        assert marker == "MySolution.sln"


class TestFindProjectMarkerOtherEcosystems:
    @pytest.mark.parametrize(
        "marker_filename",
        [
            "pyproject.toml",
            "setup.py",
            "package.json",
            "build.gradle",
            "build.gradle.kts",
            "pom.xml",
            "go.mod",
            "Cargo.toml",
        ],
    )
    def test_finds_each_ecosystem_marker(self, tmp_path, marker_filename):
        proj = tmp_path / "flatpkg"
        proj.mkdir()
        (proj / marker_filename).write_text("")
        target = proj / "module_source_file.txt"
        target.write_text("")

        marker = core_path_markers.find_project_marker(str(target))
        assert marker == marker_filename


# ---------------------------------------------------------------------------
# find_project_marker — negative cases
# ---------------------------------------------------------------------------


class TestFindProjectMarkerNegative:
    def test_no_marker_anywhere_returns_none(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        target = nested / "main.tf"
        target.write_text("")

        assert core_path_markers.find_project_marker(str(target)) is None

    def test_nonexistent_intermediate_directory_treated_as_no_marker(self, tmp_path):
        """A directory in the walk chain that doesn't exist on disk (e.g. a
        race-deleted directory, or a synthetic path in a test) must be
        treated the same as 'no marker found', not raise."""
        target = tmp_path / "ghost" / "sub" / "file.py"  # "ghost"/"sub" never created

        assert core_path_markers.find_project_marker(str(target)) is None

    def test_marker_in_unrelated_sibling_dir_not_found(self, tmp_path):
        """A marker in a SIBLING directory (not an ancestor) must not count."""
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        (sibling / "go.mod").write_text("")

        target_dir = tmp_path / "target_dir"
        target_dir.mkdir()
        target = target_dir / "main.go"
        target.write_text("")

        assert core_path_markers.find_project_marker(str(target)) is None


class TestFindProjectMarkerRelativePathsSkipped:
    """Layer 2 requires an absolute path: production (Claude Code's
    Write/Edit tool_input) always supplies one, so requiring it here makes
    the walk deterministic and CWD-independent rather than silently
    resolving a relative path against whatever directory the process
    happens to be running in (which caused real false positives against
    this very repo's own pyproject.toml in the test suite)."""

    def test_relative_path_returns_none_even_with_marker_in_cwd_ancestry(
        self, tmp_path, monkeypatch
    ):
        proj = tmp_path / "hasmarker"
        proj.mkdir()
        (proj / "pyproject.toml").write_text("")
        subdir = proj / "helpers"
        subdir.mkdir()
        monkeypatch.chdir(subdir)

        # A relative path resolved against subdir's CWD would find the
        # marker at proj/pyproject.toml if abspath() were used — it must
        # not, because the path is relative.
        assert core_path_markers.find_project_marker("utils.py") is None

    def test_absolute_path_still_finds_marker(self, tmp_path):
        """Sanity check: the fix is scoped to relative paths only —
        absolute paths still resolve normally."""
        proj = tmp_path / "hasmarker"
        proj.mkdir()
        (proj / "pyproject.toml").write_text("")
        target = proj / "client.py"
        target.write_text("")

        assert core_path_markers.find_project_marker(str(target)) == "pyproject.toml"


# ---------------------------------------------------------------------------
# find_project_marker — uncapped-walk termination (Messi Rule 14)
# ---------------------------------------------------------------------------


class TestFindProjectMarkerTermination:
    def test_terminates_cleanly_on_200_level_deep_tree_with_no_marker(self, tmp_path):
        """No artificial depth cap: prove the walk still terminates cleanly
        (returns None, does not hang or raise) even for a pathologically
        deep tree, bounded only by the tree's own real depth."""
        current = tmp_path
        for i in range(200):
            current = current / f"level{i}"
        current.mkdir(parents=True)
        target = current / "deep_file.py"
        target.write_text("")

        assert core_path_markers.find_project_marker(str(target)) is None

    def test_finds_marker_placed_shallow_in_a_deep_tree(self, tmp_path):
        """A marker placed near the top of a deep tree is still found even
        though the file being checked is many levels below it."""
        root_proj = tmp_path / "flatpkg"
        root_proj.mkdir()
        (root_proj / "pyproject.toml").write_text("")

        current = root_proj
        for i in range(50):
            current = current / f"level{i}"
        current.mkdir(parents=True)
        target = current / "deep_module.py"
        target.write_text("")

        marker = core_path_markers.find_project_marker(str(target))
        assert marker == "pyproject.toml"


# ---------------------------------------------------------------------------
# has_core_marker — Layer 2 + 2c combined decision
# ---------------------------------------------------------------------------


class TestHasCoreMarker:
    def test_returns_true_for_normal_marker(self, tmp_path):
        proj = tmp_path / "MyProject"
        proj.mkdir()
        (proj / "MyProject.csproj").write_text("")
        target = proj / "Program.cs"
        target.write_text("")

        assert core_path_markers.has_core_marker(str(target)) is True

    def test_returns_false_for_test_project_marker(self, tmp_path):
        """AC: MyProject.Tests/Program.cs with MyProject.Tests.csproj as the
        nearest marker — the marker's OWN filename is a test-project
        pattern, so this is NOT flagged as core, even without a bare
        tests/ or test/ directory segment."""
        proj = tmp_path / "MyProject.Tests"
        proj.mkdir()
        (proj / "MyProject.Tests.csproj").write_text("")
        target = proj / "Program.cs"
        target.write_text("")

        assert core_path_markers.has_core_marker(str(target)) is False

    def test_returns_false_when_no_marker_found(self, tmp_path):
        nested = tmp_path / "infra"
        nested.mkdir()
        target = nested / "main.tf"
        target.write_text("")

        assert core_path_markers.has_core_marker(str(target)) is False


# ---------------------------------------------------------------------------
# _find_marker_in_dir — candidate-name-first check (issue #92 review
# finding F-2). Uses dependency injection (an optional test-only
# _isfile_check callback on _find_marker_in_dir) rather than monkeypatching
# os.path globally or asserting on wall-clock time — deterministic, no
# flakiness, no hidden control flow.
# ---------------------------------------------------------------------------

_CANDIDATE_TEST_UNRELATED_FILE_COUNT = 3000
_STABILITY_TEST_REPEAT_COUNT = 20


class TestFindMarkerInDirCandidateNameFirst:
    def test_isfile_check_only_invoked_for_candidate_named_entries(self, tmp_path):
        """Regression lock for issue #92 review finding F-2: the pre-fix
        implementation called os.path.isfile() on EVERY directory entry
        before checking whether the entry's name even looked like a
        marker (measured ~566ms on a real 106k-entry directory). The
        fixed implementation must check the entry's name against the
        marker patterns FIRST, and only invoke the is-file check for
        entries whose name already looks like a candidate marker."""
        d = tmp_path / "bigdir"
        d.mkdir()
        for i in range(_CANDIDATE_TEST_UNRELATED_FILE_COUNT):
            (d / f"unrelated_file_{i}.txt").write_text("")
        (d / "go.mod").write_text("")

        checked_names = []

        def _counting_isfile_check(entry):
            checked_names.append(entry.name)
            return entry.is_file()

        marker = core_path_markers._find_marker_in_dir(
            str(d), _isfile_check=_counting_isfile_check
        )

        assert marker == "go.mod"
        assert checked_names == ["go.mod"]


# ---------------------------------------------------------------------------
# _find_marker_in_dir / find_project_marker — deterministic multi-marker
# selection (issue #92 review finding F-7)
# ---------------------------------------------------------------------------


class TestFindMarkerInDirDeterministicPriority:
    def test_literal_marker_wins_over_glob_suffix_marker(self, tmp_path):
        """When a directory contains both a literal marker (pyproject.toml)
        and a glob-suffix marker (.csproj), selection must be deterministic
        regardless of filesystem directory-read order — literal markers are
        checked in a fixed priority list order, ahead of glob-suffix ones."""
        proj = tmp_path / "MyProject"
        proj.mkdir()
        (proj / "pyproject.toml").write_text("")
        (proj / "MyProject.csproj").write_text("")
        target = proj / "file.py"
        target.write_text("")

        marker = core_path_markers.find_project_marker(str(target))
        assert marker == "pyproject.toml"

    def test_multiple_glob_suffix_markers_deterministic_alphabetical(self, tmp_path):
        """When multiple .csproj files exist in the same directory with no
        other signal to prefer one, the tie-break must be deterministic
        (alphabetical by filename), not filesystem-read-order-dependent."""
        proj = tmp_path / "MultiProj"
        proj.mkdir()
        (proj / "Zebra.csproj").write_text("")
        (proj / "Alpha.csproj").write_text("")
        target = proj / "file.cs"
        target.write_text("")

        marker = core_path_markers.find_project_marker(str(target))
        assert marker == "Alpha.csproj"

    def test_repeated_calls_give_stable_result_with_multiple_literal_markers(
        self, tmp_path
    ):
        """package.json precedes go.mod in LITERAL_MARKER_FILENAMES, so it
        must win consistently across repeated calls, never flipping due to
        os.listdir/os.scandir iteration order."""
        proj = tmp_path / "MultiMarker"
        proj.mkdir()
        (proj / "go.mod").write_text("")
        (proj / "package.json").write_text("")
        target = proj / "file.go"
        target.write_text("")

        results = {
            core_path_markers.find_project_marker(str(target))
            for _ in range(_STABILITY_TEST_REPEAT_COUNT)
        }
        assert results == {"package.json"}
