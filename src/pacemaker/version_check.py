"""
Session-start Claude Code version check (Story #66).

Public API:
- perform_session_start_version_check(state, config, stderr) -> None
    Called early in run_session_start_hook().
    Probes 'claude --version', compares to configured minimum, sets
    state["version_block_active"], writes a hard-block message to stderr
    when below minimum, and records the status to version_status_db.
    Fails open on any exception — the check must never break a session start.
"""

import sys
from typing import Any, Dict, Optional, TextIO

from .claude_code_version import ClaudeCodeVersion, probe_installed_version
from .logger import log_warning

# Minimum version string used as fallback when config key is absent.
_FALLBACK_MIN_VERSION = "2.1.39"

# Stderr block message template.
_BLOCK_MESSAGE = (
    "\n"
    "╔══════════════════════════════════════════════════════════════╗\n"
    "║  PACE-MAKER: Claude Code version too old                     ║\n"
    "╠══════════════════════════════════════════════════════════════╣\n"
    "║  Installed : {current:<46}║\n"
    "║  Required  : >= {minimum:<43}║\n"
    "║                                                              ║\n"
    "║  Run: claude upgrade                                         ║\n"
    "╚══════════════════════════════════════════════════════════════╝\n"
    "\n"
)


def perform_session_start_version_check(
    state: Any,
    config: Any,
    stderr: Optional[TextIO] = None,
) -> None:
    """Check the installed Claude Code version against the configured minimum.

    Mutates `state` to set `version_block_active` (True/False).
    Writes a hard-block message to `stderr` when the installed version is below
    the minimum. Records status to version_status_db for the monitor.

    Fail-open guarantee: ANY exception at ANY point sets no block and returns
    normally. The check must never prevent a session from starting.

    Input validation:
    - state must be a mutable dict; if not, logs and returns immediately.
    - config must support .get(); if not, logs and returns immediately.
    - stderr falls back to sys.stderr when None or non-writable.

    Args:
        state:   Mutable hook state dict (will be modified in place).
        config:  Loaded config dict (read-only).
        stderr:  Stream to write the block message to. Defaults to sys.stderr.
    """
    # Validate state — we need to write to it; fail silently if unusable.
    if not isinstance(state, dict):
        log_warning(
            "version_check",
            f"perform_session_start_version_check: state is not a dict ({type(state).__name__}); skipping",
        )
        return

    # Validate config — we need .get(); fall back to empty dict.
    if not hasattr(config, "get"):
        log_warning(
            "version_check",
            f"perform_session_start_version_check: config has no .get() ({type(config).__name__}); using defaults",
        )
        config = {}

    # Validate / resolve stderr.
    if stderr is None or not hasattr(stderr, "write"):
        stderr = sys.stderr

    try:
        _do_version_check(state, config, stderr)
    except Exception as e:
        log_warning("version_check", f"Version check failed unexpectedly: {e}")
        # Fail open: only assign when state is still a valid dict.
        if isinstance(state, dict):
            state["version_block_active"] = False


def _do_version_check(
    state: Dict[str, Any],
    config: Dict[str, Any],
    stderr: TextIO,
) -> None:
    """Inner implementation — exceptions propagate to perform_session_start_version_check."""
    min_version_str = config.get("min_claude_version", _FALLBACK_MIN_VERSION)
    minimum = ClaudeCodeVersion.parse(min_version_str)

    if minimum is None:
        log_warning(
            "version_check",
            f"Could not parse configured min_claude_version: {min_version_str!r}; skipping check",
        )
        state["version_block_active"] = False
        _record(
            current_str=None,
            min_str=str(min_version_str),
            blocked=False,
            reason="parse_failed",
        )
        return

    installed = probe_installed_version()

    if installed is None:
        log_warning(
            "version_check",
            "Could not probe installed Claude Code version; proceeding without block",
        )
        state["version_block_active"] = False
        _record(
            current_str=None,
            min_str=str(min_version_str),
            blocked=False,
            reason="probe_failed",
        )
        return

    if installed.is_below(minimum):
        current_label = f"{installed.major}.{installed.minor}.{installed.patch}"
        min_label = f"{minimum.major}.{minimum.minor}.{minimum.patch}"
        message = _BLOCK_MESSAGE.format(current=current_label, minimum=min_label)
        stderr.write(message)
        state["version_block_active"] = True
        _record(
            current_str=current_label,
            min_str=str(min_version_str),
            blocked=True,
            reason="below_minimum",
        )
        return

    current_label = f"{installed.major}.{installed.minor}.{installed.patch}"
    state["version_block_active"] = False
    _record(
        current_str=current_label,
        min_str=str(min_version_str),
        blocked=False,
        reason="ok",
    )


def _record(
    current_str: Optional[str],
    min_str: str,
    blocked: bool,
    reason: str,
) -> None:
    """Write version status to DB; silently ignore any DB write failure."""
    try:
        from .version_status_db import record_status

        record_status(current_str, min_str, blocked=blocked, reason=reason)
    except Exception as e:
        log_warning("version_check", f"Failed to record version status: {e}")
