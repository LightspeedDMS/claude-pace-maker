#!/usr/bin/env python3
"""
Project context extraction for Langfuse trace metadata.

Extracts project information from current working directory and git repository:
- project_path: Full cwd path
- project_name: Last component of cwd (folder name)
- git_remote: Git remote URL (if available)
- git_branch: Current git branch (if available)

Caches results for performance since project context doesn't change during session.
"""

import os
import subprocess
from typing import Optional, Dict


# Module-level cache (project context doesn't change during session)
_cache: Optional[Dict[str, Optional[str]]] = None


def _clear_cache() -> None:
    """Clear the module-level cache (for testing)."""
    global _cache
    _cache = None


def _get_git_info(git_command: list) -> Optional[str]:
    """
    Execute git command and return output or None on failure.

    Args:
        git_command: List of command arguments (e.g., ["git", "config", "--get", "remote.origin.url"])

    Returns:
        Git command output (stripped) or None if command fails
    """
    try:
        result = subprocess.run(
            git_command,
            capture_output=True,
            text=True,
            timeout=2,  # Don't hang if git is slow
        )

        # Check for success
        if result.returncode != 0:
            return None

        # Extract and strip output
        output = result.stdout.strip()

        # Return None for empty output
        if not output:
            return None

        return output

    except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
        # Git command failed or timed out - not an error, just means git unavailable
        return None


def get_project_context() -> Dict[str, Optional[str]]:
    """
    Get project context from current working directory and git repository.

    Returns dict with:
    - project_path: str (cwd)
    - project_name: str (last component of cwd)
    - git_remote: Optional[str] (remote URL or None)
    - git_branch: Optional[str] (branch name or None)

    Results are cached since project context doesn't change during session.

    Returns:
        Dict with project context metadata
    """
    global _cache

    # Return cached result if available
    if _cache is not None:
        return _cache

    # Get project path and name from cwd
    project_path = os.getcwd()
    project_name = os.path.basename(project_path)

    # Get git remote URL (gracefully handle failures)
    git_remote = _get_git_info(["git", "config", "--get", "remote.origin.url"])

    # Get git branch (gracefully handle failures)
    git_branch = _get_git_info(["git", "rev-parse", "--abbrev-ref", "HEAD"])

    # Build context dict
    context = {
        "project_path": project_path,
        "project_name": project_name,
        "git_remote": git_remote,
        "git_branch": git_branch,
    }

    # Cache result
    _cache = context

    return context
