#!/usr/bin/env python3
"""
Extension Registry for Source Code Files.

Provides functionality to:
- Load source code file extensions from external config
- Validate if a file is source code based on its extension
- Provide default extension list as fallback
"""

import json
import os
from typing import List
from pathlib import Path

from .logger import log_warning


def get_default_extensions() -> List[str]:
    """
    Get default list of source code file extensions.

    Returns:
        List of file extensions including the leading dot (e.g., [".py", ".js"])
    """
    return [
        ".py",  # Python
        ".js",  # JavaScript
        ".ts",  # TypeScript
        ".tsx",  # TypeScript React
        ".jsx",  # JavaScript React
        ".go",  # Go
        ".java",  # Java
        ".cpp",  # C++
        ".c",  # C
        ".h",  # C/C++ Header
        ".hpp",  # C++ Header
        ".rs",  # Rust
        ".rb",  # Ruby
        ".php",  # PHP
        ".cs",  # C#
        ".swift",  # Swift
        ".kt",  # Kotlin
        ".scala",  # Scala
        ".sh",  # Shell
        ".bash",  # Bash
        ".zsh",  # Zsh
    ]


def load_extensions(config_path: str) -> List[str]:
    """
    Load source code extensions from config file.

    Falls back to default extensions if:
    - Config file doesn't exist
    - Config file has invalid JSON
    - Config file missing 'extensions' key
    - Extensions list is empty

    Args:
        config_path: Path to JSON config file with "extensions" key

    Returns:
        List of file extensions (e.g., [".py", ".js", ".ts"])
    """
    # Try to load from config file
    try:
        if not os.path.exists(config_path):
            return get_default_extensions()

        with open(config_path, "r") as f:
            config_data = json.load(f)

        extensions = config_data.get("extensions", [])

        # Validate extensions list
        if not isinstance(extensions, list):
            return get_default_extensions()

        if len(extensions) == 0:
            return get_default_extensions()

        return extensions

    except (json.JSONDecodeError, OSError, Exception) as e:
        log_warning(
            "extension_registry", "Failed to load extensions config, using defaults", e
        )
        return get_default_extensions()


def is_source_code_file(filepath: str, extensions: List[str]) -> bool:
    """
    Check if a file is source code based on its extension.

    Performs case-insensitive extension matching.

    Args:
        filepath: Path to file (can be absolute or relative)
        extensions: List of source code extensions to check against

    Returns:
        True if file extension matches any in the registry, False otherwise
    """
    if not extensions:
        return False

    # Extract file extension
    path = Path(filepath)
    file_ext = path.suffix  # Gets extension including dot (e.g., ".py")

    if not file_ext:
        return False

    # Case-insensitive matching
    file_ext_lower = file_ext.lower()

    for ext in extensions:
        if ext.lower() == file_ext_lower:
            return True

    return False
