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
        "core/",
        "source/",
        "libraries/",
        "kernel/",
    ]


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

    Args:
        config_path: Path to YAML config file
        paths: List of path strings
    """
    os.makedirs(os.path.dirname(config_path), exist_ok=True)

    with open(config_path, "w") as f:
        yaml.safe_dump({"paths": paths}, f, default_flow_style=False, sort_keys=False)


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
