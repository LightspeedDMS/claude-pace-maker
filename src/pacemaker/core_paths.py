#!/usr/bin/env python3
"""
Core Paths Management Module.

Provides CRUD operations for managing TDD-enforced core code paths:
- Load paths from YAML config or use defaults
- Add, modify, and remove paths
- Match file paths against core paths
- Format paths for display and prompt injection
"""

import os
import yaml
from typing import List

from .logger import log_warning


# The 4 words added by issue #92's evidence-based survey (app/ — 7 repos,
# routes/ — 5 repos incl. the org's own FastAPI-serverless template,
# services/ — 2 root-level repos, internal/ — Go-compiler-enforced). See
# .analysis/core_paths_survey_by_class.md for the full evidence trail.
NEW_STORY_92_WORDS = ["app/", "routes/", "services/", "internal/"]

# One-time migration guard field persisted in core_paths.yaml — prevents a
# later manual removal of one of NEW_STORY_92_WORDS from being silently
# re-added on a future load (issue #92).
MIGRATION_MARKER_KEY = "_migrated_story_92"


def get_default_paths() -> List[str]:
    """
    Get default core code paths (hardcoded).

    These paths are enforced for TDD when intent validation is enabled.

    Returns:
        List of path strings with trailing slashes
    """
    return [
        "src/",
        "lib/",
        "code/",
        "core/",
        "source/",
        "libraries/",
        "kernel/",
    ] + list(NEW_STORY_92_WORDS)


def load_paths(config_path: str, strict: bool = False) -> List[str]:
    """
    Load core paths from YAML config file.

    Falls back to default paths when:
    - Config file doesn't exist (both strict and non-strict)
    - Config file has invalid YAML (non-strict only)
    - Config file missing 'paths' key (both strict and non-strict)
    - Paths list is empty (both strict and non-strict)

    If strict=True, raises exception ONLY on YAML parsing errors.

    Args:
        config_path: Path to YAML config file with "paths" key
        strict: If True, raise exception on YAML syntax errors; if False, return defaults

    Returns:
        List of path strings (e.g., ["src/", "lib/", "custom/"])

    Raises:
        ValueError: If strict=True and YAML file has syntax errors
    """
    # Try to load from config file
    try:
        # Missing file → always return defaults (even in strict mode)
        if not os.path.exists(config_path):
            return get_default_paths()

        with open(config_path, "r") as f:
            config_data = yaml.safe_load(f)

        # Empty file → return defaults (even in strict mode)
        if config_data is None:
            return get_default_paths()

        paths = config_data.get("paths", [])

        # Missing 'paths' key or not a list → return defaults (even in strict mode)
        if not isinstance(paths, list):
            return get_default_paths()

        # Empty paths list → return defaults (even in strict mode)
        if len(paths) == 0:
            return get_default_paths()

        return paths

    except yaml.YAMLError as e:
        # YAML parsing error → strict mode raises, non-strict returns defaults
        if strict:
            raise ValueError(f"Invalid YAML syntax in config file:\n{str(e)}") from e
        log_warning("core_paths", "Failed to parse YAML config, using defaults", e)
        return get_default_paths()
    except OSError as e:
        # File I/O error → always log and return defaults
        log_warning("core_paths", "Failed to read config file, using defaults", e)
        return get_default_paths()


def migrate_if_needed(config_path: str) -> None:
    """
    One-time migration: append issue #92's 4 new default words to an
    existing core_paths.yaml that predates them, without touching any
    existing entry.

    No-ops when:
    - config_path does not exist (nothing to migrate — a fresh load
      already gets the new 11-entry default list via get_default_paths())
    - the file was already migrated (guarded by MIGRATION_MARKER_KEY, so a
      later manual removal of a new word is never silently re-added)
    - the YAML is malformed/unreadable (fail safe — never risk corrupting
      or losing a user's customized file)

    Args:
        config_path: Path to YAML config file with "paths" key
    """
    if not os.path.exists(config_path):
        return

    try:
        with open(config_path, "r") as f:
            config_data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as e:
        log_warning("core_paths", "Migration skipped: failed to read config file", e)
        return

    if not isinstance(config_data, dict):
        return

    if config_data.get(MIGRATION_MARKER_KEY) is True:
        return

    existing_paths = config_data.get("paths", [])
    if isinstance(existing_paths, list) and len(existing_paths) > 0:
        missing = [w for w in NEW_STORY_92_WORDS if w not in existing_paths]
        if missing:
            config_data["paths"] = existing_paths + missing
    # else: no real customization present (missing/empty 'paths' key) —
    # leave it untouched so load_paths()'s existing fallback to the (now
    # 11-entry) defaults continues to apply naturally.

    config_data[MIGRATION_MARKER_KEY] = True

    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w") as f:
            yaml.safe_dump(config_data, f, default_flow_style=False, sort_keys=False)
    except OSError as e:
        log_warning("core_paths", "Migration skipped: failed to write config file", e)


def load_paths_with_migration(config_path: str) -> List[str]:
    """
    Load core paths, running the one-time story #92 migration first.

    This is the function the live hook path (intent_validator._is_core_path
    via _regex_stage1_check) uses. Plain load_paths() is left untouched for
    other callers (e.g. the CLI) that must not perform migration as a side
    effect of a read.

    Args:
        config_path: Path to YAML config file with "paths" key

    Returns:
        List of path strings, including any newly-migrated words
    """
    migrate_if_needed(config_path)
    return load_paths(config_path)


def _normalize_path(path: str) -> str:
    """
    Normalize path by ensuring trailing slash.

    Args:
        path: Path string (may or may not have trailing slash)

    Returns:
        Path with trailing slash
    """
    if not path.endswith("/"):
        return path + "/"
    return path


def add_path(config_path: str, path: str) -> None:
    """
    Add a new core path to the YAML config file.

    Creates the config file with defaults if it doesn't exist.
    Normalizes path by ensuring trailing slash.

    Args:
        config_path: Path to YAML config file
        path: Path to add (will be normalized with trailing slash)

    Raises:
        ValueError: If path already exists in config
    """
    # Normalize path
    normalized = _normalize_path(path)

    # Reject degenerate segments (bare "/", "", "///", ...) that rstrip("/")
    # to an empty string — _is_core_path's Layer 1 regex builder turns an
    # empty segment into an empty regex alternative, which poisons the
    # pattern into matching every absolute path (issue #92 review finding
    # F-5). Must be rejected here, before any write to disk.
    if not normalized.rstrip("/"):
        raise ValueError(
            f"Invalid path '{path}': cannot add an empty or slash-only path segment"
        )

    # Load existing paths or get defaults
    paths = load_paths(config_path)

    # Check for duplicate
    if normalized in paths:
        raise ValueError(f"Path '{normalized}' already exists in configuration")

    # Append new path
    paths.append(normalized)

    # Write back to file
    _write_paths(config_path, paths)


def _write_paths(config_path: str, paths: List[str]) -> None:
    """
    Write paths list to YAML config file.

    Read-modify-write: preserves any other top-level key already present
    in the on-disk YAML (e.g. MIGRATION_MARKER_KEY) instead of overwriting
    the whole file with just {"paths": paths}. Without this, every CLI
    add/remove silently dropped the migration marker, letting a later
    migrate_if_needed() call silently re-add a manually-removed word
    (issue #92 review finding F-1).

    Args:
        config_path: Path to YAML config file
        paths: List of path strings
    """
    os.makedirs(os.path.dirname(config_path), exist_ok=True)

    existing_data: dict = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                existing_data = loaded
        except (yaml.YAMLError, OSError) as e:
            log_warning(
                "core_paths",
                "Failed to read existing config before write; other top-level "
                "keys (if any) will not be preserved",
                e,
            )

    existing_data["paths"] = paths

    with open(config_path, "w") as f:
        yaml.safe_dump(existing_data, f, default_flow_style=False, sort_keys=False)


def remove_path(config_path: str, path: str) -> None:
    """
    Remove a core path from the YAML config file.

    Args:
        config_path: Path to YAML config file
        path: Path to remove (exact match required)

    Raises:
        ValueError: If path is not found in config
    """
    # Load existing paths
    paths = load_paths(config_path)

    # Filter out the path to remove
    filtered_paths = [p for p in paths if p != path]

    if len(filtered_paths) == len(paths):
        raise ValueError(f"Path '{path}' not found in configuration")

    # Write back to file
    _write_paths(config_path, filtered_paths)


def is_core_path(file_path: str, paths: List[str]) -> bool:
    """
    Check if file path matches any core path prefix.

    Works with both relative and absolute paths by checking if any
    core path appears in the file path.

    Args:
        file_path: File path to check (relative or absolute)
        paths: List of core paths to match against

    Returns:
        True if file_path starts with or contains any core path, False otherwise
    """
    if not paths:
        return False

    # Normalize file path to use forward slashes
    normalized_file = file_path.replace("\\", "/")

    # Check if any core path is a prefix of the file path
    for core_path in paths:
        # For absolute paths, check if core path appears anywhere
        # For relative paths, check if it's a prefix
        if normalized_file.startswith(core_path) or f"/{core_path}" in normalized_file:
            return True

    return False


def format_paths_for_display(paths: List[str]) -> str:
    """
    Format paths for CLI display output.

    Args:
        paths: List of path strings

    Returns:
        Formatted string for display
    """
    if not paths:
        return "No core paths configured."

    output = ["Core paths requiring TDD enforcement:"]
    for path in paths:
        output.append(f"  - {path}")

    return "\n".join(output)


def format_paths_for_prompt(paths: List[str]) -> str:
    """
    Format paths for prompt injection.

    Args:
        paths: List of path strings

    Returns:
        Formatted string for prompt template
    """
    if not paths:
        return "  - No core paths configured"

    formatted = []
    for path in paths:
        formatted.append(f"  - {path}")

    return "\n".join(formatted)
