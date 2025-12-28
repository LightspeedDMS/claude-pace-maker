#!/usr/bin/env python3
"""
Excluded Paths Management Module.

Provides CRUD operations for managing folders excluded from TDD enforcement:
- Load paths from YAML config or use defaults
- Add and remove exclusion paths
- Match file paths against exclusions
"""

import os
import yaml
from typing import List

from .logger import log_warning


def get_default_exclusions() -> List[str]:
    """
    Get default excluded paths (hardcoded).

    These paths are excluded from TDD enforcement when intent validation is enabled.

    Returns:
        List of path strings with trailing slashes
    """
    return [
        ".tmp/",
        "test/",
        "tests/",
        "fixtures/",
        "__pycache__/",
        "node_modules/",
        "vendor/",
        "dist/",
        "build/",
        ".git/",
    ]


def load_exclusions(config_path: str) -> List[str]:
    """
    Load excluded paths from YAML config file.

    Falls back to default exclusions when:
    - Config file doesn't exist
    - Config file has invalid YAML
    - Config file missing 'excluded_paths' key
    - Excluded paths list is empty or not a list

    Args:
        config_path: Path to YAML config file with "excluded_paths" key

    Returns:
        List of path strings (e.g., [".tmp/", "tests/", ".custom/"])
    """
    # Try to load from config file
    try:
        # Missing file → return defaults
        if not os.path.exists(config_path):
            return get_default_exclusions()

        with open(config_path, "r") as f:
            config_data = yaml.safe_load(f)

        # Empty file → return defaults
        if config_data is None:
            return get_default_exclusions()

        excluded_paths = config_data.get("excluded_paths", [])

        # Missing 'excluded_paths' key or not a list → return defaults
        if not isinstance(excluded_paths, list):
            return get_default_exclusions()

        # Empty paths list → return defaults
        if len(excluded_paths) == 0:
            return get_default_exclusions()

        return excluded_paths

    except yaml.YAMLError as e:
        # YAML parsing error → return defaults
        log_warning("excluded_paths", "Failed to parse YAML config, using defaults", e)
        return get_default_exclusions()
    except OSError as e:
        # File I/O error → log and return defaults
        log_warning("excluded_paths", "Failed to read config file, using defaults", e)
        return get_default_exclusions()


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


def add_exclusion(config_path: str, path: str) -> None:
    """
    Add a new excluded path to the YAML config file.

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

    # Load existing paths or get defaults
    paths = load_exclusions(config_path)

    # Check for duplicate
    if normalized in paths:
        raise ValueError(f"Path '{normalized}' already exists in configuration")

    # Append new path
    paths.append(normalized)

    # Write back to file
    _write_exclusions(config_path, paths)


def _write_exclusions(config_path: str, paths: List[str]) -> None:
    """
    Write exclusions list to YAML config file.

    Args:
        config_path: Path to YAML config file
        paths: List of excluded path strings
    """
    os.makedirs(os.path.dirname(config_path), exist_ok=True)

    with open(config_path, "w") as f:
        yaml.safe_dump(
            {"excluded_paths": paths}, f, default_flow_style=False, sort_keys=False
        )


def remove_exclusion(config_path: str, path: str) -> None:
    """
    Remove an excluded path from the YAML config file.

    Args:
        config_path: Path to YAML config file
        path: Path to remove (exact match required)

    Raises:
        ValueError: If path is not found in config
    """
    # Load existing paths
    paths = load_exclusions(config_path)

    # Filter out the path to remove
    filtered_paths = [p for p in paths if p != path]

    if len(filtered_paths) == len(paths):
        raise ValueError(f"Path '{path}' not found in configuration")

    # Write back to file
    _write_exclusions(config_path, filtered_paths)


def is_excluded_path(file_path: str, exclusions: List[str]) -> bool:
    """
    Check if file path matches any excluded path prefix.

    Works with both relative and absolute paths by checking if any
    exclusion appears in the file path.

    Args:
        file_path: File path to check (relative or absolute)
        exclusions: List of excluded paths to match against

    Returns:
        True if file_path contains any exclusion pattern, False otherwise
    """
    if not exclusions:
        return False

    # Normalize file path to use forward slashes (handle Windows paths)
    normalized_file = file_path.replace("\\", "/")

    # Check if any exclusion is present in the file path
    for exclusion in exclusions:
        # For absolute paths, check if exclusion appears anywhere
        # For relative paths, check if it's a prefix
        if normalized_file.startswith(exclusion) or f"/{exclusion}" in normalized_file:
            return True

    return False


def format_exclusions_for_display(exclusions: List[str]) -> str:
    """
    Format exclusions for CLI display output.

    Args:
        exclusions: List of excluded path strings

    Returns:
        Formatted string for display
    """
    if not exclusions:
        return "No excluded paths configured."

    output = ["Excluded paths (TDD enforcement bypassed):"]
    for path in exclusions:
        output.append(f"  - {path}")

    return "\n".join(output)


def format_exclusions_for_prompt(exclusions: List[str]) -> str:
    """
    Format exclusions for prompt injection.

    Args:
        exclusions: List of excluded path strings

    Returns:
        Formatted string for prompt template
    """
    if not exclusions:
        return "  - No excluded paths configured"

    formatted = []
    for path in exclusions:
        formatted.append(f"  - {path}")

    return "\n".join(formatted)
