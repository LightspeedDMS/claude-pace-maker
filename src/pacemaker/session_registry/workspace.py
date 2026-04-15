"""
Session Registry workspace resolver.

Public API:
- resolve_workspace_root(cwd: str) -> str
    Determine the canonical workspace root for a given working directory.
    Uses `git rev-parse --show-toplevel` when inside a git repo;
    falls back to os.path.realpath(cwd) for non-git dirs or on error.
"""

import os
import subprocess

from ..logger import log_debug, log_warning

# ── Git command ───────────────────────────────────────────────────────────────
_GIT_CMD = ["git", "rev-parse", "--show-toplevel"]

# ── Timeout: env var override or 2-second default ────────────────────────────
_GIT_TIMEOUT_SECONDS = float(os.environ.get("PACEMAKER_GIT_TIMEOUT_SECONDS", "2.0"))


def resolve_workspace_root(cwd: str) -> str:
    """Return the canonical workspace root for cwd.

    Resolution strategy:
    1. Run `git rev-parse --show-toplevel` from cwd.
       - returncode == 0  →  return os.path.realpath(stdout.strip())
       - returncode != 0  →  cwd is not inside a git repo; fall back
    2. On TimeoutExpired or FileNotFoundError (git not installed), log a
       warning and fall back.
    3. Fallback: return os.path.realpath(cwd).

    Args:
        cwd: The working directory to resolve.

    Returns:
        Canonical absolute path of the workspace root (symlinks resolved).

    Raises:
        ValueError: if cwd is empty or None.
    """
    if not cwd:
        raise ValueError("cwd must be a non-empty string")

    fallback = os.path.realpath(cwd)

    try:
        result = subprocess.run(
            _GIT_CMD,
            cwd=cwd,
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        if result.returncode == 0:
            toplevel = result.stdout.strip().decode("utf-8", errors="replace")
            return os.path.realpath(toplevel)
        log_debug(
            "session_registry",
            f"git rev-parse returned {result.returncode} for {cwd!r}; "
            f"not a git repo, falling back to {fallback!r}",
        )
    except subprocess.TimeoutExpired:
        log_warning(
            "session_registry",
            f"git rev-parse timed out for {cwd!r}; " f"falling back to {fallback!r}",
        )
    except FileNotFoundError:
        log_warning(
            "session_registry",
            f"git binary not found; falling back to {fallback!r}",
        )

    return fallback
