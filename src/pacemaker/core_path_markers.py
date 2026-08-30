#!/usr/bin/env python3
"""
Structural Marker-File Fallback for Core-Path Detection (issue #92).

Implements:
  - Layer 2: an upward directory walk from a file's containing directory to
    the filesystem root, looking for per-ecosystem project marker files
    (*.csproj, *.sln, pyproject.toml, setup.py, package.json, build.gradle,
    build.gradle.kts, pom.xml, go.mod, Cargo.toml). This closes the
    structural gap no bare-directory-name word list can ever cover: repos
    where the source-root directory name IS the project/package name
    (.NET solutions, Python flat-layout packages), which varies per repo by
    construction.
  - Layer 2c: a found marker whose OWN filename matches a per-ecosystem
    test-project naming pattern (e.g. MyProject.Tests.csproj) denotes a
    dedicated test project, not production code — excluded before the
    marker is allowed to flag the file as core.
  - Layer 0's filename-suffix test-file check (e.g. Go's *_test.go, which
    has no directory-level exclusion signal at all: test files sit in the
    SAME directory as production code, under the SAME go.mod).

No artificial depth cap on the walk (removed on explicit direction — see
issue #92): a real filesystem path has a finite number of ancestor
directories by construction, and the walk strictly climbs toward the root
on every iteration, so termination (Messi Rule 14) is provably bounded by
the actual depth of the path being walked, not an arbitrary number.
"""

import os
from typing import Optional

from .excluded_paths import matches_filename_pattern

# Layer 2 — ecosystem project markers with a fixed, exact filename.
LITERAL_MARKER_FILENAMES = [
    "pyproject.toml",
    "setup.py",
    "package.json",
    "build.gradle",
    "build.gradle.kts",
    "pom.xml",
    "go.mod",
    "Cargo.toml",
]

# Layer 2 — .csproj/.sln filenames are project-name-specific (there is no
# fixed name to match exactly), so they are matched by suffix instead.
GLOB_MARKER_SUFFIXES = (".csproj", ".sln")

# Layer 2c — a found marker whose own filename matches one of these
# patterns denotes a dedicated test project, not production code.
TEST_PROJECT_MARKER_PATTERNS = ["*.Tests.csproj", "*.Test.csproj", "*Tests.sln"]

# Layer 0 — filename-suffix test-file patterns with no directory-level
# exclusion signal at all.
TEST_FILENAME_SUFFIX_PATTERNS = ["*_test.go"]


def matches_test_filename_pattern(file_path: str) -> bool:
    """
    Return True if file_path's basename matches a test-filename-suffix
    pattern (e.g. *_test.go) with no directory-level exclusion signal.

    Args:
        file_path: File path to check (relative or absolute)

    Returns:
        True if the basename matches a test-filename-suffix pattern
    """
    basename = os.path.basename(file_path)
    return matches_filename_pattern(basename, TEST_FILENAME_SUFFIX_PATTERNS)


def matches_test_project_marker(marker_filename: str) -> bool:
    """
    Return True if a found project-marker's own filename denotes a
    dedicated test project (e.g. MyProject.Tests.csproj), not production
    code.

    Args:
        marker_filename: Bare marker filename (e.g. "MyProject.Tests.csproj")

    Returns:
        True if marker_filename matches a test-project naming pattern
    """
    return matches_filename_pattern(marker_filename, TEST_PROJECT_MARKER_PATTERNS)


_LITERAL_MARKER_SET = set(LITERAL_MARKER_FILENAMES)


def _find_marker_in_dir(dir_path: str, _isfile_check=None) -> Optional[str]:
    """
    Return the first project-marker filename found directly inside
    dir_path, or None if none is present.

    Directory-read errors (permission denied, race-deleted directory, etc.)
    are treated the same as "no marker found" — fail safe, never raise.

    Performance (issue #92 review finding F-2): candidate NAMES are
    checked first (cheap in-memory set/suffix comparison via
    os.scandir()), and the is-file check is only performed for entries
    that already look like a candidate marker — never for every entry in
    the directory. The pre-fix implementation called os.path.isfile() on
    every entry before checking the name at all, measured at ~566ms on a
    real 106k-entry directory vs ~21-35ms with this approach.

    Determinism (issue #92 review finding F-7): when a directory contains
    more than one recognized marker, selection is NOT filesystem-read-order
    dependent. Literal-filename markers are checked in the fixed priority
    order of LITERAL_MARKER_FILENAMES; only if none are present are
    glob-suffix markers (.csproj/.sln) considered, tie-broken by sorted
    filename.

    Args:
        dir_path: Directory to scan (not walked — direct children only)
        _isfile_check: Optional injectable is-file predicate taking an
            os.DirEntry and returning bool. Test-only seam (dependency
            injection) so tests can observe exactly which entries are
            checked without monkeypatching os.path globally. Defaults to
            entry.is_file() from os.scandir(), which reuses directory-read
            type information where the platform provides it (e.g. d_type
            on Linux) instead of issuing a separate stat() syscall.
    """
    isfile_check = (
        _isfile_check if _isfile_check is not None else (lambda entry: entry.is_file())
    )

    literal_hits: dict = {}
    glob_candidates = []
    try:
        with os.scandir(dir_path) as it:
            for entry in it:
                name = entry.name
                if name in _LITERAL_MARKER_SET:
                    literal_hits[name] = entry
                elif name.endswith(GLOB_MARKER_SUFFIXES):
                    glob_candidates.append(entry)
    except OSError:
        return None

    for literal_name in LITERAL_MARKER_FILENAMES:
        entry = literal_hits.get(literal_name)
        if entry is None:
            continue
        try:
            if isfile_check(entry):
                return literal_name
        except OSError:
            continue

    for entry in sorted(glob_candidates, key=lambda e: e.name):
        try:
            if isfile_check(entry):
                return entry.name
        except OSError:
            continue

    return None


def find_project_marker(file_path: str) -> Optional[str]:
    """
    Walk up from dirname(file_path) to the filesystem root looking for a
    project-marker file (Layer 2).

    Requires file_path to be absolute. Production (Claude Code's Write/Edit
    tool_input) always supplies an absolute path, so this loses no real
    coverage. A relative path has no CWD-independent meaning for a
    structural filesystem fact like a project marker — resolving it via
    os.path.abspath() against whatever directory the current process
    happens to be running in is non-deterministic and can spuriously find
    an unrelated marker (e.g. this very hook process's own project root).
    Callers that only have a bare filename/relative path get None here,
    which is the correct "no marker found" answer for that case.

    Args:
        file_path: Target file path (must be absolute to walk; a relative
            path returns None immediately)

    Returns:
        The marker's bare filename if found anywhere in the ancestor chain,
        otherwise None (file_path is relative, or the walk reached the
        filesystem root with no marker present).
    """
    if not os.path.isabs(file_path):
        return None

    current_dir = os.path.dirname(file_path)
    while True:
        marker = _find_marker_in_dir(current_dir)
        if marker is not None:
            return marker

        parent_dir = os.path.dirname(current_dir)
        if parent_dir == current_dir:
            # Reached the filesystem root (os.path.dirname("/") == "/").
            return None
        current_dir = parent_dir


def has_core_marker(file_path: str) -> bool:
    """
    Layer 2 + Layer 2c combined decision.

    Args:
        file_path: Target file path (relative or absolute)

    Returns:
        True if a project marker is found in the ancestor chain AND that
        marker is not itself a dedicated test-project marker; False if no
        marker is found, or the found marker is a test-project marker.
    """
    marker = find_project_marker(file_path)
    if marker is None:
        return False
    return not matches_test_project_marker(marker)
