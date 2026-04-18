"""
Memory Localization package.

Provides Flow A (auto-link at SessionStart), Flow B (seed CLI),
and Flow C (unlink CLI) for git-tracked .claude-memory/ integration.

Public API (re-exported from core):
  link_if_local_exists(cwd, transcript_path, config) -> (status, target)
  seed_and_link(cwd) -> int
  unlink_and_restore(cwd) -> int
"""

from .core import link_if_local_exists, seed_and_link, unlink_and_restore

__all__ = ["link_if_local_exists", "seed_and_link", "unlink_and_restore"]
