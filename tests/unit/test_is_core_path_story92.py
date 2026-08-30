#!/usr/bin/env python3
"""
Unit tests for the story #92 rewrite of _is_core_path() in intent_validator.py.

Implements the 3-layer algorithm from issue #92:

  Layer 0 (universal negative signals, run FIRST, before any positive match):
    - is_excluded_path(file_path, exclusions)      -> excluded -> not core
    - NOT is_source_code_file(file_path, extensions) -> not core
    - matches_test_filename_pattern(file_path)      -> not core (e.g. *_test.go)

  Layer 1 (fast path): bare-segment word-list match (config-driven).

  Layer 2 (+2c): structural marker-file fallback (only reached when Layer 1
    misses; Layer 0 already ran, so excluded/non-source/test-filename files
    never reach Layer 2 regardless of which layer would otherwise match).

These tests call _is_core_path directly with explicit, in-memory
core_path_segments/exclusions/extensions arguments (no filesystem config
I/O) — the pure decision function. Layer 2's marker walk uses REAL tmp_path
directories since it is inherently filesystem-based.
"""

import pytest

from pacemaker.intent_validator import _is_core_path
from pacemaker.core_paths import get_default_paths
from pacemaker.excluded_paths import get_default_exclusions
from pacemaker.extension_registry import get_default_extensions


DEFAULT_SEGMENTS = get_default_paths()
DEFAULT_EXCLUSIONS = get_default_exclusions()
DEFAULT_EXTENSIONS = get_default_extensions()


def _is_core(file_path, segments=None, exclusions=None, extensions=None):
    return _is_core_path(
        file_path,
        DEFAULT_SEGMENTS if segments is None else segments,
        DEFAULT_EXCLUSIONS if exclusions is None else exclusions,
        DEFAULT_EXTENSIONS if extensions is None else extensions,
    )


# ---------------------------------------------------------------------------
# Layer 1 — new word additions (issue #92 evidence-based survey)
# ---------------------------------------------------------------------------


class TestLayer1NewWordsMatch:
    @pytest.mark.parametrize(
        "file_path",
        [
            "app/handler.py",
            "routes/api.py",
            "services/billing.py",
            "internal/lookup.go",
        ],
    )
    def test_new_word_matches_as_bare_segment(self, file_path):
        assert _is_core(file_path) is True


class TestLayer1OriginalWordsUnaffected:
    """Regression: the original 7 words must still match exactly as before."""

    @pytest.mark.parametrize(
        "file_path",
        [
            "src/auth.py",
            "lib/helper.py",
            "code/main.py",
            "core/engine.py",
            "source/build.py",
            "libraries/util.py",
            "kernel/driver.c",
        ],
    )
    def test_original_word_still_matches(self, file_path):
        assert _is_core(file_path) is True


class TestLayer1RegressionLockRejectedWords:
    """apps/ and packages/ must NEVER match as bare segments — an exhaustive
    survey disproved an earlier shallow-sample proposal (real code always
    sits one level deeper, at apps/*/src/, already caught by 'src').

    Uses isolated tmp_path directories (no marker files anywhere) so the
    result reflects Layer 1's non-match, not an incidental Layer 2 marker
    found by resolving a bare relative path against the test runner's own
    working directory (which has a real pyproject.toml)."""

    def test_apps_bare_segment_config_file_does_not_match(self, tmp_path):
        """AC's own 'smoking gun': apps/storybook contains ONLY
        package.json/tsconfig.json, zero source — a non-source-extension
        file, correctly excluded at Layer 0 regardless of the word list."""
        proj = tmp_path / "apps" / "storybook"
        proj.mkdir(parents=True)
        target = proj / "package.json"
        target.write_text("{}")

        assert _is_core(str(target)) is False

    def test_packages_bare_segment_source_file_does_not_match(self, tmp_path):
        """A real source file (.js) directly under packages/<name>/ with no
        src/ wrapper and no project marker anywhere — Layer 1 correctly
        misses 'packages', and Layer 2 correctly finds no marker."""
        proj = tmp_path / "packages" / "framework"
        proj.mkdir(parents=True)
        target = proj / "index.js"
        target.write_text("")

        assert _is_core(str(target)) is False


# ---------------------------------------------------------------------------
# Layer 0 — excluded-path precedence (over BOTH Layer 1 AND Layer 2)
# ---------------------------------------------------------------------------


class TestLayer0ExcludedPathPrecedence:
    def test_excluded_path_wins_over_layer1_word_match(self):
        """tests/src/foo.py contains BOTH an excluded segment (tests/) and
        a core word (src/) — excluded must win."""
        assert _is_core("tests/src/foo.py") is False

    def test_excluded_path_wins_over_layer2_marker_fallback(self, tmp_path):
        proj = tmp_path / "vendor"
        proj.mkdir()
        (proj / "go.mod").write_text("")
        target = proj / "third_party.go"
        target.write_text("")

        exclusions = ["vendor/"]
        assert _is_core(str(target), exclusions=exclusions) is False


# ---------------------------------------------------------------------------
# Layer 0 — non-source-extension precedence
# ---------------------------------------------------------------------------


class TestLayer0NonSourceExtension:
    def test_non_source_extension_under_core_word_is_not_core(self):
        """A .md file under src/ is not source code — Layer 0 excludes it
        before Layer 1's word match ever applies."""
        assert _is_core("src/docs/notes.md") is False

    def test_non_source_extension_never_triggers_marker_fallback(self, tmp_path):
        proj = tmp_path / "flatpkg"
        proj.mkdir()
        (proj / "pyproject.toml").write_text("")
        target = proj / "README.md"
        target.write_text("")

        assert _is_core(str(target)) is False


# ---------------------------------------------------------------------------
# Layer 0 — test-filename-suffix precedence (the precedence-bug regression
# lock: Layer 0 must run BEFORE Layer 1, or internal/foo_test.go would match
# the internal/ word before the suffix check ever runs)
# ---------------------------------------------------------------------------


class TestLayer0TestFilenamePrecedenceOverLayer1:
    @pytest.mark.parametrize(
        "file_path",
        [
            "internal/foo_test.go",
            "src/foo_test.go",
            "app/foo_test.go",
        ],
    )
    def test_test_go_file_under_core_word_is_not_core(self, file_path):
        """Regression lock for the precedence bug caught by Codex
        pressure-testing this spec: no core-path segment can ever cause a
        genuine test file to be misclassified as core."""
        assert _is_core(file_path) is False

    def test_non_test_go_sibling_under_core_word_is_still_core(self):
        """Sanity check: the non-test sibling in the same core-word
        directory IS still core (proves the suffix check is specific to
        _test.go, not a blanket denial of the directory)."""
        assert _is_core("internal/foo.go") is True


# ---------------------------------------------------------------------------
# Layer 2 — structural marker-file fallback, positive cases
# ---------------------------------------------------------------------------


class TestLayer2DotnetFlatLayout:
    def test_cs_file_no_src_wrapper_with_ancestor_csproj_is_core(self, tmp_path):
        proj = tmp_path / "LSSteps.Domain"
        proj.mkdir()
        (proj / "LSSteps.Domain.csproj").write_text("<Project/>")
        target = proj / "Implementation.cs"
        target.write_text("")

        assert _is_core(str(target)) is True


class TestLayer2PythonFlatLayout:
    def test_py_file_flat_package_with_ancestor_pyproject_is_core(self, tmp_path):
        proj = tmp_path / "cet_auth"
        proj.mkdir()
        (proj / "pyproject.toml").write_text("")
        target = proj / "client.py"
        target.write_text("")

        assert _is_core(str(target)) is True


# ---------------------------------------------------------------------------
# Layer 2 — negative cases (no marker anywhere; Terraform / Kustomize)
# ---------------------------------------------------------------------------


class TestLayer2NoMarkerNegative:
    def test_terraform_file_with_no_marker_is_not_core(self, tmp_path):
        proj = tmp_path / "infra"
        proj.mkdir()
        target = proj / "main.tf"
        target.write_text("")

        assert _is_core(str(target)) is False

    def test_python_file_with_no_marker_anywhere_is_not_core(self, tmp_path):
        proj = tmp_path / "scratch"
        proj.mkdir()
        target = proj / "throwaway.py"
        target.write_text("")

        assert _is_core(str(target)) is False


# ---------------------------------------------------------------------------
# Layer 2c — test-project marker exclusion
# ---------------------------------------------------------------------------


class TestLayer2cDotnetTestProject:
    def test_dotnet_test_project_marker_is_not_core(self, tmp_path):
        """MyProject.Tests/Program.cs with MyProject.Tests.csproj as the
        nearest marker — the marker's own filename matches a test-project
        pattern, so the fallback does not fire, even though the directory
        name does NOT contain a bare tests/ or test/ segment."""
        proj = tmp_path / "MyProject.Tests"
        proj.mkdir()
        (proj / "MyProject.Tests.csproj").write_text("")
        target = proj / "Program.cs"
        target.write_text("")

        assert _is_core(str(target)) is False


class TestLayer2cGoTestFileBesideGoMod:
    def test_go_test_file_beside_sibling_under_shared_gomod_is_not_core(self, tmp_path):
        """foo_test.go sits in the SAME directory (same go.mod) as its
        non-test sibling foo.go, with no tests/ or test/ directory
        anywhere — the filename-suffix pattern excludes it before the
        marker walk is even consulted."""
        proj = tmp_path / "pkgroot"
        proj.mkdir()
        (proj / "go.mod").write_text("module example.com/pkgroot\n")
        (proj / "foo.go").write_text("package pkgroot")
        target = proj / "foo_test.go"
        target.write_text("package pkgroot")

        assert _is_core(str(target)) is False


class TestIssue103CaseInsensitiveDirectoryMarkerFallback:
    """Bug #103 repro: cs/{tests,Tests,TESTS}/unit/mod.py with a
    pyproject.toml at the fixture root, so the decision routes through
    Layer 2's marker-file fallback (not Layer 1 word-list)."""

    @pytest.mark.parametrize("dirname", ["tests", "Tests", "TESTS"])
    def test_tests_dir_not_core_any_case_bug103(self, tmp_path, dirname):
        proj = tmp_path / "cs"
        proj.mkdir()
        (proj / "pyproject.toml").write_text("")
        target = proj / dirname / "unit" / "mod.py"
        target.parent.mkdir(parents=True)
        target.write_text("")

        assert _is_core(str(target)) is False


class TestIssue103GenuineCoreFileNearMarkerRegression:
    """A genuine core file (no test-directory name involved) near the same
    kind of marker must still correctly resolve as core after the fix."""

    def test_genuine_core_file_near_marker_still_core(self, tmp_path):
        proj = tmp_path / "flatapp"
        proj.mkdir()
        (proj / "pyproject.toml").write_text("")
        target = proj / "impl" / "handler.py"
        target.parent.mkdir(parents=True)
        target.write_text("")

        assert _is_core(str(target)) is True
