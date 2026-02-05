#!/usr/bin/env python3
"""
Langfuse subagent trace hierarchy support.

Implements Story #31: Subagent Trace Hierarchy
- Detects subagent transcripts by filename pattern and isSidechain marker
- Manages independent state per subagent
- Creates child spans linked to parent trace
- Handles incremental collection for subagents
- Finalizes subagent traces on completion
"""

import json
from pathlib import Path
from typing import Optional, Dict, Any

from ..logger import log_warning, log_debug


def is_subagent_transcript(transcript_path: str) -> bool:
    """
    Detect if transcript is a subagent by filename pattern.

    AC1: Subagent transcripts follow agent-{uuid}.jsonl naming convention

    Args:
        transcript_path: Path to transcript file

    Returns:
        True if filename matches agent-*.jsonl pattern, False otherwise
    """
    filename = Path(transcript_path).name

    # Check for agent-*.jsonl pattern
    return filename.startswith("agent-") and filename.endswith(".jsonl")


def verify_sidechain_marker(transcript_path: str) -> bool:
    """
    Verify transcript has isSidechain: true marker.

    AC1: Subagent transcripts contain isSidechain: true in session_start entry

    Args:
        transcript_path: Path to transcript file

    Returns:
        True if isSidechain: true found, False otherwise
    """
    try:
        with open(transcript_path, "r") as f:
            # Read first few lines to find session_start
            for _ in range(10):  # Check first 10 lines
                line = f.readline()
                if not line:
                    break

                try:
                    entry = json.loads(line)

                    # Look for session_start entry
                    if entry.get("type") == "session_start":
                        return entry.get("isSidechain", False)

                except json.JSONDecodeError:
                    continue

        # No session_start found or isSidechain not present
        return False

    except (FileNotFoundError, IOError) as e:
        log_warning(
            "subagent", f"Failed to verify sidechain marker: {transcript_path}", e
        )
        return False


# ============================================================================
# AC2: Independent State Tracking Per Subagent
# ============================================================================


def create_subagent_state(
    state_dir: str,
    session_id: str,
    trace_id: str,
    parent_observation_id: Optional[str],
    last_pushed_line: int,
) -> bool:
    """
    Create or update state for a subagent session.

    AC2: Each session (main or subagent) has independent state file
    State schema includes: session_id, trace_id, parent_observation_id, last_pushed_line

    Args:
        state_dir: Directory for state files
        session_id: Session identifier (main or subagent)
        trace_id: Langfuse trace ID
        parent_observation_id: Parent observation ID (None for main session)
        last_pushed_line: Last line pushed to Langfuse

    Returns:
        True if successful, False if failed
    """
    # Ensure state directory exists
    Path(state_dir).mkdir(parents=True, exist_ok=True)

    state_file = Path(state_dir) / f"{session_id}.json"
    temp_file = Path(state_dir) / f"{session_id}.json.tmp"

    state_data = {
        "session_id": session_id,
        "trace_id": trace_id,
        "parent_observation_id": parent_observation_id,
        "last_pushed_line": last_pushed_line,
    }

    try:
        # Atomic write: temp file + rename
        with open(temp_file, "w") as f:
            json.dump(state_data, f)

        temp_file.rename(state_file)
        log_debug(
            "subagent", f"Created state for {session_id}: line={last_pushed_line}"
        )
        return True

    except (IOError, OSError) as e:
        log_warning("subagent", f"Failed to create state for {session_id}", e)

        # Clean up temp file
        if temp_file.exists():
            try:
                temp_file.unlink()
            except OSError:
                pass  # Cleanup failure is non-critical

        return False


def read_subagent_state(state_dir: str, session_id: str) -> Optional[Dict[str, Any]]:
    """
    Read state for a session (main or subagent).

    Args:
        state_dir: Directory for state files
        session_id: Session identifier

    Returns:
        State dict with session_id, trace_id, parent_observation_id, last_pushed_line
        or None if not found
    """
    state_file = Path(state_dir) / f"{session_id}.json"

    if not state_file.exists():
        return None

    try:
        with open(state_file, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log_warning("subagent", f"Failed to read state for {session_id}", e)
        return None


def update_subagent_state(
    state_dir: str, session_id: str, last_pushed_line: int
) -> bool:
    """
    Update last_pushed_line for existing state.

    Preserves other fields (trace_id, parent_observation_id).

    Args:
        state_dir: Directory for state files
        session_id: Session identifier
        last_pushed_line: New last_pushed_line value

    Returns:
        True if successful, False if failed
    """
    # Read existing state
    state = read_subagent_state(state_dir, session_id)

    if state is None:
        log_warning(
            "subagent", f"Cannot update - no state found for {session_id}", None
        )
        return False

    # Update last_pushed_line
    state["last_pushed_line"] = last_pushed_line

    # Write back using atomic write
    return create_subagent_state(
        state_dir=state_dir,
        session_id=state["session_id"],
        trace_id=state["trace_id"],
        parent_observation_id=state.get("parent_observation_id"),
        last_pushed_line=last_pushed_line,
    )


# ============================================================================
# AC3: Child Span Creation with Parent Linking
# ============================================================================


def create_child_span(
    client: Any,
    parent_trace_id: str,
    parent_observation_id: str,
    subagent_session_id: str,
    subagent_name: str,
) -> Optional[str]:
    """
    Create a child span for subagent with parent linking.

    AC3: Child span links to parent trace via parent_observation_id,
    enabling hierarchical visualization in Langfuse UI.

    Args:
        client: Langfuse client instance
        parent_trace_id: Parent trace ID (main session)
        parent_observation_id: Parent observation ID (Task tool span)
        subagent_session_id: Subagent session identifier
        subagent_name: Subagent name (e.g., "code-reviewer")

    Returns:
        Child observation ID for state tracking, or None if failed
    """
    try:
        # Create child span with parent linking
        child_observation_id = client.create_span(
            trace_id=parent_trace_id,
            parent_observation_id=parent_observation_id,
            name=f"subagent:{subagent_name}",
            metadata={"session_id": subagent_session_id},
        )

        log_debug(
            "subagent",
            f"Created child span for {subagent_name}: "
            f"parent_trace={parent_trace_id}, "
            f"parent_obs={parent_observation_id}",
        )

        return child_observation_id

    except Exception as e:
        log_warning("subagent", f"Failed to create child span for {subagent_name}", e)
        return None


def handle_subagent_start(
    client: Any,
    state_dir: str,
    subagent_session_id: str,
    subagent_transcript_path: str,
    parent_session_id: str,
    parent_observation_id: str,
    subagent_name: str,
) -> Optional[str]:
    """
    Handle SubagentStart hook: create child span and initialize subagent state.

    AC3: Orchestrates the complete SubagentStart flow:
    1. Verify this is a subagent transcript (agent-*.jsonl pattern)
    2. Read parent session's Langfuse state to get trace_id
    3. Create child span linked to parent trace
    4. Initialize subagent's own state file with child span info

    Args:
        client: Langfuse client instance
        state_dir: Directory for state files
        subagent_session_id: Subagent session identifier
        subagent_transcript_path: Path to subagent transcript file
        parent_session_id: Parent session identifier
        parent_observation_id: Parent observation ID (Task tool span)
        subagent_name: Subagent name (e.g., "code-reviewer")

    Returns:
        Child observation ID if successful, None if failed or not a subagent
    """
    # Step 1: Verify this is a subagent transcript
    if not is_subagent_transcript(subagent_transcript_path):
        log_debug(
            "subagent", f"Skipping non-subagent transcript: {subagent_transcript_path}"
        )
        return None

    # Step 2: Read parent state to get trace_id
    parent_state = read_subagent_state(state_dir, parent_session_id)
    if parent_state is None:
        log_warning(
            "subagent",
            f"Cannot create child span - parent state not found: {parent_session_id}",
            None,
        )
        return None

    parent_trace_id = parent_state.get("trace_id")
    if not parent_trace_id:
        log_warning(
            "subagent", f"Parent state missing trace_id: {parent_session_id}", None
        )
        return None

    # Step 3: Create child span with parent linking
    child_span_id = create_child_span(
        client=client,
        parent_trace_id=parent_trace_id,
        parent_observation_id=parent_observation_id,
        subagent_session_id=subagent_session_id,
        subagent_name=subagent_name,
    )

    if child_span_id is None:
        return None

    # Step 4: Initialize subagent state with child span info
    success = create_subagent_state(
        state_dir=state_dir,
        session_id=subagent_session_id,
        trace_id=parent_trace_id,  # Same trace as parent
        parent_observation_id=child_span_id,  # Link to our child span
        last_pushed_line=0,  # Start from beginning
    )

    if not success:
        log_warning(
            "subagent", f"Failed to create subagent state: {subagent_session_id}", None
        )
        return None

    log_debug(
        "subagent",
        f"SubagentStart complete: {subagent_name} linked to parent trace {parent_trace_id}",
    )

    return child_span_id
