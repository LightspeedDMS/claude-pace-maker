#!/usr/bin/env python3
"""
Langfuse state management for incremental push tracking.

Manages per-session state files that track:
- session_id: Claude Code session identifier
- trace_id: Langfuse trace ID (reused across all pushes in session)
- last_pushed_line: Line number in transcript of last successful push

State files are stored in ~/.claude-pace-maker/langfuse_state/<session_id>.json
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, Any, Optional, List

from ..logger import log_warning, log_debug

# Bug #160: a subagent's state file is dead once the subagent stops, so it gets
# a short TTL (2 days is generous: mtime refreshes on every tool call). Session
# state keeps the 7-day TTL.
SUBAGENT_STATE_PREFIX = "subagent-"
SUBAGENT_STATE_MAX_AGE_DAYS = 2
# Cleanup runs at most once per interval; the stamp's mtime records the last run
# (no ".json" suffix, so the cleanup glob never matches it).
CLEANUP_STAMP_FILENAME = ".last_cleanup"
CLEANUP_MIN_INTERVAL_SECONDS = 24 * 60 * 60


class StateManager:
    """
    Manages Langfuse push state files for incremental collection.

    Each session has its own state file containing:
    - session_id: Session identifier
    - trace_id: Langfuse trace ID (same for all pushes in session)
    - last_pushed_line: Last line number successfully pushed to Langfuse

    Implements:
    - AC3: Per-session state tracking
    - AC3: Atomic writes (temp file + rename)
    - AC3: Stale file cleanup (>7 days old)
    """

    def __init__(self, state_dir: str):
        """
        Initialize StateManager.

        Args:
            state_dir: Directory for state files (created if not exists)
        """
        self.state_dir = state_dir

        # Create state directory if not exists
        Path(state_dir).mkdir(parents=True, exist_ok=True)

    def read(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Read state for a session.

        Args:
            session_id: Session identifier

        Returns:
            State dict with session_id, trace_id, last_pushed_line, or None if not found
        """
        state_file = Path(self.state_dir) / f"{session_id}.json"

        if not state_file.exists():
            return None

        try:
            with open(state_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            log_warning("state", f"Failed to read state for {session_id}", e)
            return None

    def create_or_update(
        self,
        session_id: str,
        trace_id: str,
        last_pushed_line: int,
        metadata: Optional[Dict[str, Any]] = None,
        pending_trace: Optional[List[Dict[str, Any]]] = None,
        pending_intel: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Create or update state for a session.

        Uses atomic write (temp file + rename) to prevent corruption.

        Args:
            session_id: Session identifier
            trace_id: Langfuse trace ID
            last_pushed_line: Last line pushed to Langfuse
            metadata: Optional trace metadata (tool calls, accumulated tokens)
            pending_trace: Optional pending trace batch (for secrets sanitization)
            pending_intel: Optional prompt intelligence metadata (frustration, specificity, etc)

        Returns:
            True if successful, False if failed
        """
        state_file = Path(self.state_dir) / f"{session_id}.json"
        temp_file = Path(self.state_dir) / f"{session_id}.json.tmp.{os.getpid()}"

        state_data = {
            "session_id": session_id,
            "trace_id": trace_id,
            "last_pushed_line": last_pushed_line,
        }

        # Add metadata if provided
        if metadata is not None:
            state_data["metadata"] = metadata

        # Add pending_trace if provided
        if pending_trace is not None:
            state_data["pending_trace"] = pending_trace

        # Add pending_intel if provided
        if pending_intel is not None:
            state_data["pending_intel"] = pending_intel

        try:
            # Write to temp file first (atomic operation)
            with open(temp_file, "w") as f:
                json.dump(state_data, f)

            # Atomic rename (POSIX guarantees atomicity)
            temp_file.rename(state_file)

            log_debug("state", f"Saved state for {session_id}: line={last_pushed_line}")
            return True

        except (IOError, OSError) as e:
            log_warning("state", f"Failed to save state for {session_id}", e)

            # Clean up temp file if it exists
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    pass

            return False

    def cleanup_stale_files(
        self,
        max_age_days: float = 7,
        subagent_max_age_days: Optional[float] = None,
    ):
        """
        Delete state files older than max_age_days.

        AC3: Stale state files (>7 days old) are cleaned up

        Args:
            max_age_days: Maximum age in days (default: 7)
            subagent_max_age_days: Optional shorter TTL for ``subagent-*.json``
                files (bug #160: a subagent's state is dead once it stops).
                None (default) applies max_age_days to every file.
        """
        now = time.time()
        cutoff_time = now - (max_age_days * 24 * 60 * 60)
        subagent_cutoff_time = (
            cutoff_time
            if subagent_max_age_days is None
            else now - (subagent_max_age_days * 24 * 60 * 60)
        )

        try:
            state_path = Path(self.state_dir)

            # Only process .json files (not .tmp or other files)
            for state_file in state_path.glob("*.json"):
                try:
                    mtime = state_file.stat().st_mtime
                    file_cutoff = (
                        subagent_cutoff_time
                        if state_file.name.startswith(SUBAGENT_STATE_PREFIX)
                        else cutoff_time
                    )

                    if mtime < file_cutoff:
                        state_file.unlink()
                        log_debug(
                            "state", f"Deleted stale state file: {state_file.name}"
                        )

                except (OSError, IOError) as e:
                    log_warning("state", f"Failed to delete {state_file.name}", e)

        except Exception as e:
            log_warning("state", "State cleanup failed", e)

    def maybe_cleanup_stale_files(
        self,
        max_age_days: float = 7,
        subagent_max_age_days: float = SUBAGENT_STATE_MAX_AGE_DAYS,
        min_interval_seconds: float = CLEANUP_MIN_INTERVAL_SECONDS,
    ) -> bool:
        """
        Run cleanup_stale_files at most once per min_interval_seconds.

        Bug #160: cheap enough to call from every SessionStart - when the stamp
        file (CLEANUP_STAMP_FILENAME, mtime = last run) is younger than the
        interval this is one stat() and nothing else. Never raises.

        Returns:
            True if cleanup ran (and the stamp was refreshed), False if it was
            throttled or could not complete.
        """
        stamp = Path(self.state_dir) / CLEANUP_STAMP_FILENAME
        try:
            if time.time() - stamp.stat().st_mtime < min_interval_seconds:
                return False
        except FileNotFoundError:
            pass  # never ran: fall through
        except OSError as e:
            log_warning("state", "Could not stat cleanup stamp", e)
            return False

        self.cleanup_stale_files(
            max_age_days=max_age_days, subagent_max_age_days=subagent_max_age_days
        )
        try:
            stamp.touch()
        except OSError as e:
            log_warning("state", "Could not write cleanup stamp", e)
            return False
        return True
