#!/usr/bin/env python3
"""
Langfuse incremental push orchestrator.

Coordinates incremental pushes from hooks:
- Checks configuration and credentials
- Manages state (read/write last_pushed_line)
- Parses incremental lines from transcript
- Creates or updates trace
- Pushes to Langfuse API with timeout
- Updates state on success
"""

from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import uuid
import json
import re

from ..logger import log_info, log_warning, log_debug
from . import state, incremental, push
from .trace import create_trace_for_turn, finalize_trace_with_output
from .span import create_span, create_text_span
from .project_context import get_project_context
from ..telemetry import jsonl_parser
from .metrics import increment_metric
from ..constants import DEFAULT_DB_PATH, DEFAULT_STATE_PATH
from ..secrets.sanitizer import sanitize_trace


# Timeout for incremental push (increased from 2s to 10s to prevent premature timeouts)
# When timeout occurs, data may have been sent to Langfuse (server just didn't respond in time)
# 10 seconds provides adequate time for push while still being non-blocking
INCREMENTAL_PUSH_TIMEOUT_SECONDS = 10


def extract_task_tool_prompt(
    transcript_path: str, parent_observation_id: Optional[str] = None
) -> Optional[str]:
    """
    Extract Task tool prompt from parent transcript.

    If parent_observation_id is provided, searches for a specific tool_use block.
    Otherwise, finds the most recent Task tool call in the transcript.

    Args:
        transcript_path: Path to parent session transcript JSONL file
        parent_observation_id: Optional Task tool span ID to search for

    Returns:
        Task tool prompt string, or None if not found
    """
    try:
        last_task_prompt = None

        with open(transcript_path, "r") as f:
            for line in f:
                try:
                    entry = json.loads(line)

                    # Only process assistant messages
                    if entry.get("type") != "assistant":
                        continue

                    message = entry.get("message", {})
                    if not isinstance(message, dict):
                        continue

                    # Search content array for tool_use block
                    content = message.get("content", [])
                    if not isinstance(content, list):
                        continue

                    for content_item in content:
                        if not isinstance(content_item, dict):
                            continue

                        # Check if this is a Task tool call
                        if (
                            content_item.get("type") == "tool_use"
                            and content_item.get("name") == "Task"
                        ):
                            # Extract prompt from tool input
                            tool_input = content_item.get("input", {})
                            prompt = tool_input.get("prompt")

                            # If looking for specific observation_id, check and return immediately
                            if parent_observation_id:
                                if content_item.get("id") == parent_observation_id:
                                    return prompt if prompt else None
                            else:
                                # No specific ID - track the last Task tool prompt found
                                if prompt:
                                    last_task_prompt = prompt

                except json.JSONDecodeError:
                    # Skip malformed lines
                    continue

        # Return last found prompt (or None if not found)
        return last_task_prompt

    except FileNotFoundError:
        log_warning(
            "orchestrator", f"Parent transcript not found: {transcript_path}", None
        )
        return None
    except IOError as e:
        log_warning(
            "orchestrator", f"Failed to read parent transcript: {transcript_path}", e
        )
        return None


def _build_tool_id_mapping(transcript_path: str) -> Dict[str, str]:
    """
    Build mapping of tool_use_id to tool_name from transcript.

    Args:
        transcript_path: Path to transcript JSONL file

    Returns:
        Dict mapping tool_use_id to tool_name
    """
    tool_id_to_name = {}

    with open(transcript_path, "r") as f:
        for line in f:
            try:
                entry = json.loads(line)

                # Only process assistant messages
                if entry.get("type") != "assistant":
                    continue

                message = entry.get("message", {})
                if not isinstance(message, dict):
                    continue

                # Search for tool_use blocks
                content = message.get("content", [])
                if not isinstance(content, list):
                    continue

                for content_item in content:
                    if not isinstance(content_item, dict):
                        continue

                    if content_item.get("type") == "tool_use":
                        tool_id = content_item.get("id")
                        tool_name = content_item.get("name")
                        if tool_id and tool_name:
                            tool_id_to_name[tool_id] = tool_name

            except json.JSONDecodeError:
                # Skip malformed lines
                continue

    return tool_id_to_name


def _normalize_tool_result_content(raw_content: Any) -> Optional[str]:
    """
    Normalize tool result content to string format.

    Handles both string content and array content with dicts like
    [{"type": "text", "text": "..."}].

    Args:
        raw_content: Raw content from tool_result (string or array)

    Returns:
        Normalized string content, or None if empty
    """
    if isinstance(raw_content, list):
        # Array may contain dicts like {"type": "text", "text": "..."}
        # or plain strings
        text_parts = []
        for item in raw_content:
            if isinstance(item, dict):
                # Extract text from dict (e.g., {"type": "text", "text": "..."})
                text_parts.append(item.get("text", str(item)))
            else:
                text_parts.append(str(item))
        return "".join(text_parts) if text_parts else None
    elif raw_content:
        return str(raw_content)
    else:
        return None


def _find_task_results(
    transcript_path: str,
    tool_id_to_name: Dict[str, str],
    agent_id: Optional[str] = None,
) -> Optional[str]:
    """
    Find Task tool results from transcript, optionally filtered by agent_id.

    Args:
        transcript_path: Path to transcript JSONL file
        tool_id_to_name: Mapping of tool_use_id to tool_name
        agent_id: Optional agent ID to filter (matches "agentId: XXX" pattern)

    Returns:
        Last matching Task result content, or None if not found
    """
    # Compile regex pattern for agent_id filtering if provided
    agent_id_pattern = None
    if agent_id is not None:
        # Match "agentId: {agent_id}" at end of content with optional whitespace
        agent_id_pattern = re.compile(
            rf"agentId:\s*{re.escape(agent_id)}\s*$", re.MULTILINE
        )

    last_task_result = None
    last_matching_result = None

    with open(transcript_path, "r") as f:
        for line in f:
            try:
                entry = json.loads(line)

                # Only process user messages (tool_result comes from user)
                if entry.get("type") != "user":
                    continue

                message = entry.get("message", {})
                if not isinstance(message, dict):
                    continue

                # Search for tool_result blocks
                content = message.get("content", [])
                if not isinstance(content, list):
                    continue

                for content_item in content:
                    if not isinstance(content_item, dict):
                        continue

                    if content_item.get("type") == "tool_result":
                        tool_use_id = content_item.get("tool_use_id")

                        # Check if this result is for a Task tool
                        if tool_use_id and tool_id_to_name.get(tool_use_id) == "Task":
                            result_content = _normalize_tool_result_content(
                                content_item.get("content")
                            )

                            if result_content:
                                # Update last_task_result (for backward compat when agent_id=None)
                                last_task_result = result_content

                                # If agent_id filtering enabled, check for match
                                if agent_id_pattern and agent_id_pattern.search(
                                    result_content
                                ):
                                    last_matching_result = result_content

            except json.JSONDecodeError:
                # Skip malformed lines
                continue

    # Return based on filtering mode
    if agent_id is not None:
        # Agent ID filtering: return matching result or None
        return last_matching_result
    else:
        # Backward compatibility: return most recent Task result
        return last_task_result


def extract_subagent_output(agent_transcript_path: str) -> Optional[str]:
    """
    Extract subagent output from subagent's own transcript.

    Reads the subagent's transcript JSONL file and finds the LAST assistant message.
    Extracts text from message content (handles both string and array formats).

    Args:
        agent_transcript_path: Path to subagent's transcript JSONL file

    Returns:
        Last assistant message text, or None if not found or file doesn't exist
    """
    try:
        last_assistant_text = None

        with open(agent_transcript_path, "r") as f:
            for line in f:
                try:
                    entry = json.loads(line)

                    # Only process assistant messages
                    if entry.get("type") != "assistant":
                        continue

                    message = entry.get("message", {})
                    if not isinstance(message, dict):
                        continue

                    # Check role
                    if message.get("role") != "assistant":
                        continue

                    # Extract text from content
                    content = message.get("content", "")
                    text_parts = []

                    if isinstance(content, list):
                        # Array format: [{"type": "text", "text": "..."}, ...]
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                    elif isinstance(content, str):
                        # String format
                        text_parts.append(content)

                    # Update last assistant message
                    if text_parts:
                        last_assistant_text = "".join(text_parts)

                except json.JSONDecodeError:
                    # Skip malformed lines
                    continue

        return last_assistant_text

    except FileNotFoundError:
        log_warning(
            "orchestrator",
            f"Subagent transcript not found: {agent_transcript_path}",
            None,
        )
        return None
    except IOError as e:
        log_warning(
            "orchestrator",
            f"Failed to read subagent transcript: {agent_transcript_path}",
            e,
        )
        return None


def extract_task_tool_result(
    transcript_path: str, agent_id: Optional[str] = None
) -> Optional[str]:
    """
    Extract Task tool result from transcript, optionally filtered by agent_id.

    Searches transcript for tool_result blocks, maps them to their corresponding
    tool_use blocks via tool_use_id. When agent_id is provided, returns the last
    Task result containing "agentId: {agent_id}" pattern. When agent_id is None,
    returns the most recent Task result (backward compatibility).

    Args:
        transcript_path: Path to parent session transcript JSONL file
        agent_id: Optional agent ID to filter results (matches "agentId: XXX" in content)

    Returns:
        Task tool result content string, or None if not found (or no match for agent_id)
    """
    try:
        # First pass: build mapping of tool_use_id -> tool_name
        tool_id_to_name = _build_tool_id_mapping(transcript_path)

        # Second pass: find Task tool_result (filtered by agent_id if provided)
        return _find_task_results(transcript_path, tool_id_to_name, agent_id)

    except FileNotFoundError:
        log_warning("orchestrator", f"Transcript not found: {transcript_path}", None)
        return None
    except IOError as e:
        log_warning("orchestrator", f"Failed to read transcript: {transcript_path}", e)
        return None


def should_run_langfuse_push(config: Dict[str, Any]) -> bool:
    """
    Determine if Langfuse push should run.

    Checks:
    - langfuse_enabled flag
    - Required credentials present (base_url, public_key, secret_key)

    Args:
        config: Configuration dictionary

    Returns:
        True if should run, False otherwise
    """
    # Check enabled flag
    if not config.get("langfuse_enabled", False):
        return False

    # Check required credentials
    base_url = config.get("langfuse_base_url")
    public_key = config.get("langfuse_public_key")
    secret_key = config.get("langfuse_secret_key")

    if not base_url or not public_key or not secret_key:
        return False

    return True


def flush_pending_trace(
    config: Dict[str, Any],
    session_id: str,
    state_manager: state.StateManager,
    existing_state: Optional[Dict[str, Any]],
    caller: str = "unknown",
) -> bool:
    """
    Flush a pending trace from state: sanitize, push to Langfuse, clear from state.

    This is the shared "check -> sanitize -> push -> clear" pattern used by:
    - handle_post_tool_use() (existing behavior)
    - handle_stop_finalize() (Bug #3 fix)
    - handle_user_prompt_submit() (Bug #4 fix - flush old before new)
    - SubagentStop flow (Bug #1 completion)

    Args:
        config: Configuration dict with Langfuse credentials
        session_id: Session identifier (parent session)
        state_manager: StateManager instance for reading/writing state
        existing_state: Current state dict (must contain pending_trace to flush)
        caller: Name of the calling function (for logging)

    Returns:
        True if a pending_trace was found and processed (even if push failed),
        False if no pending_trace found or Langfuse disabled
    """
    # Guard: no state or no pending_trace
    if not existing_state:
        return False

    pending_trace = existing_state.get("pending_trace")
    if not pending_trace:
        return False

    # Guard: Langfuse must be enabled
    if not should_run_langfuse_push(config):
        return False

    # Extract credentials
    base_url = config["langfuse_base_url"]
    public_key = config["langfuse_public_key"]
    secret_key = config["langfuse_secret_key"]
    db_path = config.get("db_path", DEFAULT_DB_PATH)

    log_debug(
        "orchestrator",
        f"Flushing pending trace for {session_id} (caller={caller})",
    )

    # Sanitize the trace batch (mask all secrets)
    sanitized_batch = sanitize_trace(pending_trace, db_path)

    # Push sanitized trace
    success, _ = push.push_batch_events(
        base_url,
        public_key,
        secret_key,
        sanitized_batch,
        timeout=INCREMENTAL_PUSH_TIMEOUT_SECONDS,
    )

    if success:
        log_info(
            "orchestrator",
            f"Successfully pushed pending trace for {session_id} (caller={caller})",
        )

        # Increment metrics on successful push
        metadata = existing_state.get("metadata", {})
        is_first_trace = metadata.get("is_first_trace_in_session", False)
        if is_first_trace:
            try:
                increment_metric("sessions", db_path)
            except Exception as e:
                log_warning("orchestrator", "Failed to increment sessions metric", e)

        try:
            increment_metric("traces", db_path)
        except Exception as e:
            log_warning("orchestrator", "Failed to increment traces metric", e)
    else:
        log_warning(
            "orchestrator",
            f"Failed to push pending trace for {session_id} (caller={caller})",
            None,
        )

    # ALWAYS clear pending_trace from state (even on failure, to prevent retry loops)
    state_manager.create_or_update(
        session_id=session_id,
        trace_id=existing_state["trace_id"],
        last_pushed_line=existing_state.get("last_pushed_line", 0),
        metadata=existing_state.get("metadata", {}),
        # pending_trace intentionally omitted - clears it from state
    )

    return True


def run_incremental_push(
    config: Dict[str, Any],
    session_id: str,
    transcript_path: str,
    state_dir: str,
    hook_type: str,
) -> bool:
    """
    Run incremental Langfuse push from hook.

    AC1: Incremental push on UserPromptSubmit
    AC2: Incremental push on PostToolUse
    AC5: Timeout and non-blocking behavior

    Workflow:
    1. Check if Langfuse enabled and configured
    2. Load state (last_pushed_line, trace_id)
    3. Parse incremental lines from transcript
    4. Create or update trace
    5. Push to Langfuse API (with timeout)
    6. Update state on success

    Args:
        config: Configuration dict with Langfuse credentials
        session_id: Session identifier
        transcript_path: Path to transcript JSONL file
        state_dir: Directory for state files
        hook_type: Hook that triggered push ('user_prompt_submit' or 'post_tool_use')

    Returns:
        True if successful or disabled, False if failed
    """
    try:
        # Check if should run
        if not should_run_langfuse_push(config):
            log_debug(
                "orchestrator", "Langfuse push skipped (disabled or misconfigured)"
            )
            return True  # Not an error, just disabled

        # Extract credentials
        base_url = config["langfuse_base_url"]
        public_key = config["langfuse_public_key"]
        secret_key = config["langfuse_secret_key"]

        # Initialize state manager
        state_manager = state.StateManager(state_dir)

        # Load existing state (if any)
        existing_state = state_manager.read(session_id)

        if existing_state:
            # Incremental push - use existing trace_id and last_pushed_line
            trace_id = existing_state["trace_id"]
            last_pushed_line = existing_state["last_pushed_line"]
            log_debug(
                "orchestrator",
                f"Incremental push for {session_id}: starting from line {last_pushed_line}",
            )
        else:
            # First push - new trace
            trace_id = (
                session_id  # Use session_id as trace_id (AC4: single trace per session)
            )
            last_pushed_line = 0
            log_debug(
                "orchestrator", f"First push for {session_id}: creating new trace"
            )

        # Parse incremental lines
        incremental_data = incremental.parse_incremental_lines(
            transcript_path, last_pushed_line
        )

        # Skip if no new lines
        if incremental_data["lines_parsed"] == 0:
            log_debug("orchestrator", f"No new lines to push for {session_id}")
            return True

        log_debug(
            "orchestrator",
            f"Parsed {incremental_data['lines_parsed']} new lines for {session_id}",
        )

        # Extract metadata for trace
        metadata = jsonl_parser.parse_session_metadata(transcript_path)
        user_id = jsonl_parser.extract_user_id(transcript_path)

        # Build existing trace from state if available
        existing_trace = None
        if existing_state:
            # Reconstruct existing trace structure from state
            # State tracks accumulated token usage in metadata
            existing_trace = {
                "id": session_id,
                "sessionId": session_id,
                "metadata": existing_state.get(
                    "metadata",
                    {
                        "tool_calls": [],
                        "tool_count": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read_tokens": 0,
                    },
                ),
            }

        # Create batch event (trace + generation)
        batch = incremental.create_batch_event(
            session_id=session_id,
            model=metadata["model"],
            user_id=user_id,
            incremental_data=incremental_data,
            existing_trace=existing_trace,
        )

        # Get db_path for sanitization and metrics
        db_path = config.get("db_path", DEFAULT_DB_PATH)

        # SECURITY: Sanitize batch before pushing to Langfuse (mask all secrets)
        sanitized_batch = sanitize_trace(batch, db_path)

        # Push sanitized batch to Langfuse API with timeout
        success, _ = push.push_batch_events(
            base_url,
            public_key,
            secret_key,
            sanitized_batch,
            timeout=INCREMENTAL_PUSH_TIMEOUT_SECONDS,
        )

        # CRITICAL FIX: Update state EVEN on timeout to prevent duplicate spans
        # When timeout occurs, the data was likely sent to Langfuse (server just
        # didn't respond in time). We must update last_pushed_line to prevent
        # re-processing the same lines on next hook call.
        #
        # This fixes the duplicate spans issue where timeout → no state update →
        # next hook re-reads from old last_pushed_line → duplicate observations
        new_last_pushed_line = incremental_data["last_line"]

        # Extract trace from batch for metadata storage
        trace = batch[0]["body"]  # First event in batch is always the trace

        state_manager.create_or_update(
            session_id=session_id,
            trace_id=trace_id,
            last_pushed_line=new_last_pushed_line,
            metadata=trace.get("metadata", {}),
        )

        if not success:
            log_warning(
                "orchestrator",
                f"Failed to push incremental data for {session_id} "
                f"(state updated to line {new_last_pushed_line} to prevent duplicates)",
                None,
            )
            return False

        # Increment metrics on successful push (Story #34)
        # First push: increment sessions counter (new session created)
        if existing_state is None:
            try:
                increment_metric("sessions", db_path)
            except Exception as e:
                log_warning("orchestrator", "Failed to increment sessions metric", e)

        # All pushes: increment traces counter
        try:
            increment_metric("traces", db_path)
        except Exception as e:
            log_warning("orchestrator", "Failed to increment traces metric", e)

        log_info(
            "orchestrator",
            f"Successfully pushed {incremental_data['lines_parsed']} lines for {session_id} "
            f"(now at line {new_last_pushed_line})",
        )

        return True

    except Exception as e:
        # AC5: Graceful failure - log error but don't crash hook
        log_warning("orchestrator", f"Incremental push error for {session_id}", e)
        return False


def handle_user_prompt_submit(
    config: Dict[str, Any],
    session_id: str,
    transcript_path: str,
    state_dir: str,
    user_message: str,
) -> bool:
    """
    Handle UserPromptSubmit hook - create new trace but DEFER push.

    REFACTORED ARCHITECTURE (Secrets Sanitization):
    - Creates trace but STORES it as pending_trace in state
    - Does NOT push immediately
    - Actual push happens in post_tool_use after sanitization

    Workflow:
    1. Check if Langfuse enabled
    2. Generate unique trace_id for this turn
    3. Extract user_id from transcript
    4. Create trace via trace module
    5. STORE trace as pending_trace in state (NOT pushed yet)
    6. Update state with current_trace_id and pending_trace

    NOTE: Intel attachment removed - intel is now pushed immediately to current trace
    in handle_post_tool_use when parsed, not stored for next trace.

    Args:
        config: Configuration dict with Langfuse credentials
        session_id: Session identifier (Langfuse sessionId)
        transcript_path: Path to transcript JSONL file
        state_dir: Directory for state files
        user_message: User's prompt text

    Returns:
        True if successful or disabled, False if failed
    """
    try:
        # Check if should run
        if not should_run_langfuse_push(config):
            log_debug("orchestrator", "Langfuse push skipped (disabled)")
            return True

        # Initialize state manager
        state_manager = state.StateManager(state_dir)

        # Load existing state to get last_pushed_line
        existing_state = state_manager.read(session_id)
        last_pushed_line = existing_state["last_pushed_line"] if existing_state else 0

        # BUG #4 FIX: Flush old pending_trace before storing new one
        # If a previous pending_trace exists (from an earlier UserPromptSubmit that
        # was never consumed by PostToolUse), flush it now to prevent data loss.
        if existing_state and existing_state.get("pending_trace"):
            flush_pending_trace(
                config=config,
                session_id=session_id,
                state_manager=state_manager,
                existing_state=existing_state,
                caller="handle_user_prompt_submit",
            )
            # Re-read state after flush (pending_trace cleared, other fields preserved)
            existing_state = state_manager.read(session_id)
            last_pushed_line = (
                existing_state["last_pushed_line"] if existing_state else 0
            )

        # Generate unique trace_id for this turn
        trace_id = f"{session_id}-turn-{str(uuid.uuid4())[:8]}"

        # Extract user_id and model from transcript
        user_id = jsonl_parser.extract_user_id(transcript_path)
        metadata = jsonl_parser.parse_session_metadata(transcript_path)
        model = metadata.get("model")

        # Get project context for metadata
        project_context = get_project_context()

        # Create trace for this turn
        trace = create_trace_for_turn(
            session_id=session_id,
            trace_id=trace_id,
            user_message=user_message,
            user_id=user_id,
            model=model,
            project_context=project_context,
        )

        # Build batch event with trace
        now = datetime.now(timezone.utc).isoformat()
        batch = [
            {
                "id": trace["id"],
                "timestamp": now,
                "type": "trace-create",
                "body": trace,
            }
        ]

        # CRITICAL CHANGE: Do NOT push immediately
        # Store batch as pending_trace in state for later sanitization + push
        state_metadata = {
            "current_trace_id": trace_id,
            "trace_start_line": last_pushed_line,
            "is_first_trace_in_session": existing_state is None,  # Track for metrics
        }

        state_manager.create_or_update(
            session_id=session_id,
            trace_id=trace_id,
            last_pushed_line=last_pushed_line,  # Don't change last_pushed_line yet
            metadata=state_metadata,
            pending_trace=batch,  # Store for later push in post_tool_use
        )

        log_info(
            "orchestrator",
            f"Stored pending trace {trace_id} for user prompt (will push after sanitization)",
        )
        return True

    except Exception as e:
        log_warning(
            "orchestrator", f"UserPromptSubmit handler error for {session_id}", e
        )
        return False


def _create_spans_from_blocks(
    content_blocks: List[Dict[str, Any]], trace_id: str, timestamp: datetime
) -> List[Dict[str, Any]]:
    """
    Create span batch events from content blocks.

    Args:
        content_blocks: List of content blocks from extract_content_blocks()
        trace_id: Trace ID to link spans to
        timestamp: Timestamp for span events

    Returns:
        List of batch event dicts ready for push_batch_events()
    """
    batch = []

    for block in content_blocks:
        content_type = block["content_type"]
        line_number = block["line_number"]

        if content_type == "text":
            span = create_text_span(
                trace_id=trace_id,
                text=block["text"],
                start_time=timestamp,
                end_time=timestamp,
                line_number=line_number,
            )
        elif content_type == "tool_use":
            span = create_span(
                trace_id=trace_id,
                tool_name=block["tool_name"],
                tool_input=block["tool_input"],
                tool_output=block.get(
                    "tool_output", ""
                ),  # Use extracted output from block
                start_time=timestamp,
                end_time=timestamp,
            )
        else:
            continue  # Skip unknown content types

        batch.append(
            {
                "id": span["id"],
                "timestamp": timestamp.isoformat(),
                "type": "span-create",
                "body": span,
            }
        )

    return batch


def handle_post_tool_use(
    config: Dict[str, Any],
    session_id: str,
    transcript_path: str,
    state_dir: str,
    tool_response: Optional[str] = None,
    tool_name: Optional[str] = None,
    tool_input: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Handle PostToolUse hook - sanitize and push pending trace, then create spans.

    REFACTORED ARCHITECTURE (Secrets Sanitization):
    1. Check if pending_trace exists in state
    2. If yes: sanitize pending_trace and push it (this is the trace from user_prompt_submit)
    3. Parse transcript incrementally to create spans for ALL content:
       - Text blocks → text spans (create_text_span)
       - Tool use blocks → tool spans (create_span)

    This captures the full conversation flow in Langfuse, not just tool calls.

    SUBAGENT CONTEXT DETECTION:
    Checks pacemaker state to detect if running in subagent context.
    If in_subagent=True and current_subagent_trace_id exists, uses subagent's
    trace_id for spans instead of parent's trace_id.

    TOOL OUTPUT CAPTURE:
    If tool_response is provided (from PostToolUse hook), creates a span for
    the current tool execution with the actual output. Tool metadata (name and input)
    are also included if provided, giving full visibility into tool execution.

    Args:
        config: Configuration dict with Langfuse credentials
        session_id: Session identifier
        transcript_path: Path to transcript JSONL file
        state_dir: Directory for state files
        tool_response: Optional tool output from PostToolUse hook
        tool_name: Optional tool name from PostToolUse hook
        tool_input: Optional tool input parameters from PostToolUse hook

    Returns:
        True if successful or disabled, False if failed
    """
    try:
        # STEP 0: Parse intel from recent assistant responses (runs on EVERY post_tool_use)
        # This captures prompt intelligence metadata for trace filtering and analytics
        # NEW: Intel is now pushed immediately to current trace, not stored for next trace
        parsed_intel = None
        try:
            from ..intel.parser import parse_intel_line
            from ..transcript_reader import get_last_n_assistant_messages

            # Get last 3 assistant messages to check for intel lines
            recent_messages = get_last_n_assistant_messages(transcript_path, n=3)
            for msg in recent_messages:
                if msg and "§" in msg:
                    # Parse intel line
                    intel = parse_intel_line(msg)
                    if intel:
                        parsed_intel = intel
                        log_debug("orchestrator", f"Parsed intel: {intel}")
                        break  # Use the most recent intel line found
        except Exception as e:
            log_warning("orchestrator", "Failed to parse intel from transcript", e)

        if not should_run_langfuse_push(config):
            log_debug("orchestrator", "Langfuse push skipped (disabled)")
            return True

        base_url = config["langfuse_base_url"]
        public_key = config["langfuse_public_key"]
        secret_key = config["langfuse_secret_key"]
        db_path = config.get("db_path", DEFAULT_DB_PATH)

        state_manager = state.StateManager(state_dir)

        # Initially read parent's state
        existing_state = state_manager.read(session_id)

        if not existing_state or "metadata" not in existing_state:
            log_warning("orchestrator", f"No state for {session_id}", None)
            return False

        metadata = existing_state.get("metadata", {})
        current_trace_id = metadata.get("current_trace_id")
        last_pushed_line = existing_state.get("last_pushed_line", 0)

        # PRESERVE parent state before subagent context detection may overwrite it
        # The parent's pending_trace must survive the subagent state override
        parent_state = existing_state

        # CRITICAL FIX (Bug #1): Check subagent context BEFORE early return on missing current_trace_id
        # This ensures subagent spans are created even when parent has no current_trace_id
        # The subagent context detection will override current_trace_id with subagent's trace_id
        effective_session_id = session_id  # Default to parent's session_id
        try:
            with open(DEFAULT_STATE_PATH, "r") as f:
                pacemaker_state = json.load(f)

                in_subagent = pacemaker_state.get("in_subagent", False)
                subagent_trace_id = pacemaker_state.get("current_subagent_trace_id")
                subagent_agent_id = pacemaker_state.get("current_subagent_agent_id")

                # If in subagent and we have subagent trace_id, use it instead of parent's
                if in_subagent and subagent_trace_id and subagent_agent_id:
                    current_trace_id = subagent_trace_id

                    # CRITICAL FIX: Derive subagent_session_id and re-read state
                    effective_session_id = f"subagent-{subagent_agent_id}"

                    # Re-read state for subagent's session_id to get correct last_pushed_line
                    subagent_state = state_manager.read(effective_session_id)
                    if subagent_state:
                        last_pushed_line = subagent_state.get("last_pushed_line", 0)
                        metadata = subagent_state.get("metadata", metadata)
                        existing_state = (
                            subagent_state  # Use subagent's state for trace_id
                        )
                        log_debug(
                            "orchestrator",
                            f"Using subagent state: session_id={effective_session_id}, "
                            f"last_pushed_line={last_pushed_line}",
                        )
                    else:
                        log_debug(
                            "orchestrator",
                            f"No existing state for subagent {effective_session_id}, "
                            f"starting from line 0",
                        )
                        last_pushed_line = 0

                    log_debug(
                        "orchestrator", f"Using subagent trace_id: {subagent_trace_id}"
                    )
        except (FileNotFoundError, json.JSONDecodeError, IOError) as e:
            # Graceful fallback: use parent trace_id if can't read pacemaker state
            log_debug(
                "orchestrator",
                f"Could not read pacemaker state, using parent trace_id: {e}",
            )

        # NOW check current_trace_id after subagent override
        if not current_trace_id:
            log_warning("orchestrator", f"No current_trace_id for {session_id}", None)
            return False

        # STEP 0.5: Push intel to CURRENT trace immediately (if parsed)
        # NEW ARCHITECTURE: Intel describes current prompt, so attach to current trace NOW
        if parsed_intel:
            intel_metadata = {}
            if "frustration" in parsed_intel:
                intel_metadata["intel_frustration"] = parsed_intel["frustration"]
            if "specificity" in parsed_intel:
                intel_metadata["intel_specificity"] = parsed_intel["specificity"]
            if "task_type" in parsed_intel:
                intel_metadata["intel_task_type"] = parsed_intel["task_type"]
            if "quality" in parsed_intel:
                intel_metadata["intel_quality"] = parsed_intel["quality"]
            if "iteration" in parsed_intel:
                intel_metadata["intel_iteration"] = parsed_intel["iteration"]

            # Create trace-update event to add intel to current trace
            intel_batch = [
                {
                    "id": f"intel-{current_trace_id}-{str(uuid.uuid4())[:8]}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "type": "trace-create",  # Upsert semantics
                    "body": {
                        "id": current_trace_id,
                        "metadata": intel_metadata,
                    },
                }
            ]

            # Push intel immediately to Langfuse
            intel_success, _ = push.push_batch_events(
                base_url, public_key, secret_key, intel_batch, timeout=5
            )

            if intel_success:
                log_debug(
                    "orchestrator",
                    f"Pushed intel to current trace {current_trace_id}: {parsed_intel}",
                )
            else:
                log_warning(
                    "orchestrator",
                    f"Failed to push intel to current trace {current_trace_id}",
                    None,
                )

        # STEP 1: Parse secrets from recent assistant responses (runs on EVERY post_tool_use)
        # This ensures secrets are stored in database and available for masking in any future traces
        try:
            from ..secrets.parser import parse_assistant_response
            from ..transcript_reader import get_last_n_assistant_messages

            # Get last 5 assistant messages to check for secret declarations
            recent_messages = get_last_n_assistant_messages(transcript_path, n=5)
            for msg in recent_messages:
                if msg and "🔐" in msg:
                    # Parse and store secrets
                    parse_assistant_response(msg, db_path)
        except Exception as e:
            log_warning("orchestrator", "Failed to parse secrets from transcript", e)

        # STEP 2: Check for pending_trace and push it (sanitized)
        # CRITICAL: Use parent_state (preserved before subagent override) to find pending_trace
        # If we're in subagent context, existing_state was overwritten with subagent's state
        # which has NO pending_trace - the parent's pending_trace would be lost
        if parent_state.get("pending_trace"):
            flush_pending_trace(
                config=config,
                session_id=session_id,
                state_manager=state_manager,
                existing_state=parent_state,
                caller="handle_post_tool_use",
            )
            # Re-read parent state to get updated version without pending_trace
            parent_state = state_manager.read(session_id)

        # STEP 3: Create span for current tool if tool_response provided
        # This handles the case where PostToolUse hook fires BEFORE the tool
        # output is written to transcript. The hook provides tool_response which
        # is the ACTUAL output from the tool that just executed.
        batch = []
        content_blocks = []  # Track for state update

        if tool_response is not None:
            # Create a span for the current tool execution using hook data
            now = datetime.now(timezone.utc)

            # Create a tool span with full metadata (name, input, output)
            span_id = f"{current_trace_id}-tool-current-{str(uuid.uuid4())[:8]}"
            span = {
                "id": span_id,
                "traceId": current_trace_id,
                "name": f"Tool - {tool_name}" if tool_name else "Tool Execution",
                "startTime": now.isoformat(),
                "endTime": now.isoformat(),
                "input": tool_input,
                "output": tool_response,
                "metadata": {
                    "source": "post_tool_use_hook",
                    "tool": tool_name,
                },
            }

            batch.append(
                {
                    "id": span["id"],
                    "timestamp": now.isoformat(),
                    "type": "span-create",
                    "body": span,
                }
            )

            log_debug(
                "orchestrator", "Created span for current tool with output from hook"
            )
        else:
            # STEP 4: Parse transcript for new content and create spans
            content_blocks = incremental.extract_content_blocks(
                transcript_path=transcript_path, start_line=last_pushed_line
            )

            if not content_blocks:
                log_debug("orchestrator", f"No new content for {session_id}")
                return True

            # Create spans from content blocks
            now = datetime.now(timezone.utc)
            batch = _create_spans_from_blocks(content_blocks, current_trace_id, now)

        # SECURITY: Sanitize spans batch before pushing to Langfuse (mask all secrets)
        sanitized_batch = sanitize_trace(batch, db_path)

        # Push sanitized batch to Langfuse
        success, success_count = push.push_batch_events(
            base_url,
            public_key,
            secret_key,
            sanitized_batch,
            timeout=INCREMENTAL_PUSH_TIMEOUT_SECONDS,
        )

        # CRITICAL FIX: Update state EVEN on timeout to prevent duplicate spans
        # When timeout occurs, the data was likely sent to Langfuse (server just
        # didn't respond in time). We must update last_pushed_line to prevent
        # re-processing the same content blocks on next hook call.
        #
        # This applies the same optimistic state update pattern used in run_incremental_push().

        # Determine new last_pushed_line
        if tool_response is not None:
            # When using tool_response from hook, we didn't parse transcript
            # so last_pushed_line stays the same
            new_last_pushed_line = last_pushed_line
        else:
            # When parsing transcript, update to max line from content blocks
            max_line = max(b["line_number"] for b in content_blocks)
            new_last_pushed_line = max_line

        if existing_state:
            state_manager.create_or_update(
                session_id=effective_session_id,
                trace_id=existing_state["trace_id"],
                last_pushed_line=new_last_pushed_line,
                metadata=metadata,
                # pending_intel not passed - intel is now pushed immediately, not stored
            )

        if not success:
            log_warning(
                "orchestrator",
                f"Failed to push spans for {session_id} "
                f"(state updated to line {new_last_pushed_line} to prevent duplicates)",
                None,
            )
            return False

        # Increment spans metric for each span ACTUALLY pushed (Story #34)
        # CRITICAL BUG FIX: Use success_count (actual successes) not len(batch)
        # ONLY increment on confirmed success (not on timeout)
        for _ in range(success_count):
            try:
                increment_metric("spans", db_path)
            except Exception as e:
                log_warning("orchestrator", "Failed to increment spans metric", e)

        log_debug("orchestrator", f"Created {len(batch)} spans for {current_trace_id}")
        return True

    except Exception as e:
        log_warning("orchestrator", f"PostToolUse handler error for {session_id}", e)
        return False


def handle_stop_finalize(
    config: Dict[str, Any],
    session_id: str,
    transcript_path: str,
    state_dir: str,
) -> bool:
    """
    Finalize current trace with Claude's output in Stop hook.

    Workflow:
    1. Check if Langfuse enabled and configured
    2. Read current_trace_id and trace_start_line from state
    3. Extract Claude's output from transcript
    4. Create trace-update event
    5. Push to Langfuse API
    6. Return success/failure

    Args:
        config: Configuration dict with Langfuse credentials
        session_id: Session identifier
        transcript_path: Path to transcript JSONL file
        state_dir: Directory for state files

    Returns:
        True if successful or disabled, False if failed
    """
    try:
        log_info("orchestrator", f"handle_stop_finalize called: session={session_id}")

        # Check if should run
        if not should_run_langfuse_push(config):
            log_debug("orchestrator", "Langfuse finalize skipped (disabled)")
            return True

        # Extract credentials
        base_url = config["langfuse_base_url"]
        public_key = config["langfuse_public_key"]
        secret_key = config["langfuse_secret_key"]

        # Initialize state manager
        state_manager = state.StateManager(state_dir)

        # Load state to get current trace info
        existing_state = state_manager.read(session_id)
        if not existing_state or "metadata" not in existing_state:
            log_warning(
                "orchestrator",
                f"No state for {session_id}, cannot finalize trace",
                None,
            )
            return False

        metadata = existing_state.get("metadata", {})
        current_trace_id = metadata.get("current_trace_id")
        trace_start_line = metadata.get("trace_start_line", 0)

        if not current_trace_id:
            log_warning(
                "orchestrator",
                f"No current_trace_id for {session_id}, cannot finalize",
                None,
            )
            return False

        # BUG #3 FIX: Flush pending_trace BEFORE finalize_trace_with_output
        # If pending_trace exists, it means UserPromptSubmit stored a trace but no
        # PostToolUse consumed it. We must push it now before finalizing.
        flush_pending_trace(
            config=config,
            session_id=session_id,
            state_manager=state_manager,
            existing_state=existing_state,
            caller="handle_stop_finalize",
        )

        # STEP 0: Parse secrets from recent assistant responses (runs on EVERY stop_finalize)
        # This ensures secrets are stored in database and available for masking in final output
        db_path = config.get("db_path", DEFAULT_DB_PATH)
        try:
            from ..secrets.parser import parse_assistant_response
            from ..transcript_reader import get_last_n_assistant_messages

            # Get last 5 assistant messages to check for secret declarations
            recent_messages = get_last_n_assistant_messages(transcript_path, n=5)
            for msg in recent_messages:
                if msg and "🔐" in msg:
                    # Parse and store secrets
                    parse_assistant_response(msg, db_path)
        except Exception as e:
            log_warning("orchestrator", "Failed to parse secrets from transcript", e)

        # Finalize trace with output from transcript
        trace_update = finalize_trace_with_output(
            trace_id=current_trace_id,
            transcript_path=transcript_path,
            trace_start_line=trace_start_line,
        )

        # Debug: log output details
        output_content = trace_update.get("output", "")
        output_len = len(output_content)
        output_preview = (
            output_content[:200].replace("\n", " ") if output_content else "(empty)"
        )
        log_info(
            "orchestrator",
            f"Finalize trace {current_trace_id}: start_line={trace_start_line}, output_len={output_len}",
        )
        log_info("orchestrator", f"Output preview: {output_preview}")

        # Build batch event with trace-update
        # Note: event "id" must be unique per event (UUID), trace_id goes in body
        # Using trace-create for upsert semantics (trace-update may not work reliably)
        now = datetime.now(timezone.utc).isoformat()
        event_id = f"finalize-{current_trace_id}-{str(uuid.uuid4())[:8]}"
        batch = [
            {
                "id": event_id,
                "timestamp": now,
                "type": "trace-create",
                "body": trace_update,
            }
        ]

        # Create generation observation with accumulated token usage
        # Langfuse computes totalCost from generation observations (not traces/spans)
        # Parse token usage from FULL transcript (state doesn't accumulate tokens)
        token_data = incremental.parse_incremental_lines(transcript_path, 0)
        token_usage = token_data.get("token_usage", {})
        accumulated_input = token_usage.get("input_tokens", 0)
        accumulated_output = token_usage.get("output_tokens", 0)
        accumulated_cache = token_usage.get("cache_read_tokens", 0)

        if accumulated_input > 0 or accumulated_output > 0:
            gen_usage: Dict[str, int] = {
                "input": accumulated_input,
                "output": accumulated_output,
                "total": accumulated_input + accumulated_output,
            }
            if accumulated_cache > 0:
                gen_usage["cache_read"] = accumulated_cache

            model_name = jsonl_parser.parse_session_metadata(transcript_path).get(
                "model", "claude-opus-4-6"
            )
            gen_id = f"{current_trace_id}-gen-{str(uuid.uuid4())[:8]}"
            generation = {
                "id": gen_id,
                "traceId": current_trace_id,
                "name": "claude-code-generation",
                "model": model_name,
                "usage": gen_usage,
                "startTime": now,
            }

            batch.append(
                {
                    "id": gen_id,
                    "timestamp": now,
                    "type": "generation-create",
                    "body": generation,
                }
            )

            log_info(
                "orchestrator",
                f"Created generation for trace {current_trace_id}: "
                f"in={accumulated_input}, out={accumulated_output}, "
                f"cache={accumulated_cache}",
            )

        # SECURITY: Sanitize batch before pushing (Claude's output may contain secrets)
        sanitized_batch = sanitize_trace(batch, db_path)

        # Push sanitized batch to Langfuse
        success, _ = push.push_batch_events(
            base_url,
            public_key,
            secret_key,
            sanitized_batch,
            timeout=INCREMENTAL_PUSH_TIMEOUT_SECONDS,
        )

        if not success:
            log_warning(
                "orchestrator", f"Failed to finalize trace for {session_id}", None
            )
            return False

        log_info("orchestrator", f"Finalized trace {current_trace_id} with output")
        return True

    except Exception as e:
        log_warning("orchestrator", f"Stop finalize handler error for {session_id}", e)
        return False


def handle_subagent_stop(
    config: Dict[str, Any],
    subagent_trace_id: str,
    parent_transcript_path: Optional[str],
    agent_id: Optional[str] = None,
    agent_transcript_path: Optional[str] = None,
) -> bool:
    """
    Finalize subagent trace with output when SubagentStop fires.

    NEW BEHAVIOR: If agent_transcript_path is provided, reads subagent output
    from subagent's own transcript (solves timing issue where SubagentStop fires
    BEFORE Task result is written to parent transcript).

    FALLBACK: If agent_transcript_path is None, falls back to extracting Task
    tool result from parent transcript (backward compatibility).

    Args:
        config: Configuration dict with Langfuse credentials
        subagent_trace_id: Subagent trace ID to finalize
        parent_transcript_path: Path to parent transcript (used for fallback)
        agent_id: Optional agent ID to filter Task results (for fallback)
        agent_transcript_path: Optional path to subagent's own transcript (NEW)

    Returns:
        True on success, False on failure or disabled
    """
    try:
        # Check if should run
        if not should_run_langfuse_push(config):
            log_debug(
                "orchestrator", "Langfuse subagent finalization skipped (disabled)"
            )
            return True  # Not an error, just disabled (consistent with other handlers)

        # Extract credentials
        base_url = config["langfuse_base_url"]
        public_key = config["langfuse_public_key"]
        secret_key = config["langfuse_secret_key"]

        # Extract subagent output - NEW LOGIC
        subagent_output = ""

        if agent_transcript_path:
            # NEW: Read from subagent's own transcript
            result = extract_subagent_output(agent_transcript_path)
            if result:
                subagent_output = result
        elif parent_transcript_path:
            # FALLBACK: Read from parent transcript (backward compatibility)
            result = extract_task_tool_result(parent_transcript_path, agent_id=agent_id)
            if result:
                subagent_output = result

        # Create trace update with output
        # Using trace-create for upsert semantics (updates existing trace)
        now = datetime.now(timezone.utc)
        trace_update = {
            "id": subagent_trace_id,
            "output": subagent_output,
            "endTime": now.isoformat(),  # BUG FIX #2: Add explicit endTime for latency calculation
        }

        # Build batch event
        event_id = f"finalize-{subagent_trace_id}-{str(uuid.uuid4())[:8]}"
        batch = [
            {
                "id": event_id,
                "timestamp": now.isoformat(),
                "type": "trace-create",  # Upsert semantics
                "body": trace_update,
            }
        ]

        # SECURITY: Sanitize batch before pushing (subagent output may contain secrets)
        db_path = config.get("db_path", DEFAULT_DB_PATH)
        sanitized_batch = sanitize_trace(batch, db_path)

        # Push sanitized batch to Langfuse with timeout
        success, _ = push.push_batch_events(
            base_url,
            public_key,
            secret_key,
            sanitized_batch,
            timeout=INCREMENTAL_PUSH_TIMEOUT_SECONDS,
        )

        if not success:
            log_warning(
                "orchestrator",
                f"Failed to finalize subagent trace {subagent_trace_id}",
                None,
            )
            return False

        log_info(
            "orchestrator", f"Finalized subagent trace {subagent_trace_id} with output"
        )
        return True

    except Exception as e:
        log_warning(
            "orchestrator", f"Subagent stop handler error for {subagent_trace_id}", e
        )
        return False


def handle_subagent_start(
    config: Dict[str, Any],
    parent_session_id: str,
    subagent_session_id: str,
    subagent_name: str,
    parent_transcript_path: str,
    state_dir: str,
) -> Optional[str]:
    """
    Handle SubagentStart hook - create TRACE for subagent (not span).

    CHANGED: Now creates a real Langfuse trace for subagents instead of a span.
    This gives subagents their own trace in Langfuse UI, linked to parent via sessionId.

    Workflow:
    1. Check if Langfuse enabled
    2. Extract Task tool prompt from parent transcript (most recent Task call)
    3. Create NEW trace for subagent with sessionId=parent_session_id
    4. Push trace to Langfuse API
    5. Initialize subagent state with NEW subagent trace_id
    6. Return subagent trace ID

    Args:
        config: Configuration dict with Langfuse credentials
        parent_session_id: Parent session identifier
        subagent_session_id: Subagent session identifier
        subagent_name: Subagent name (e.g., "code-reviewer")
        parent_transcript_path: Path to parent transcript for extracting prompt
        state_dir: Directory for state files

    Returns:
        Subagent trace ID if successful, None if failed or disabled
    """
    try:
        # Check if should run
        if not should_run_langfuse_push(config):
            log_debug("orchestrator", "Subagent trace creation skipped (disabled)")
            return None  # Disabled, not an error

        # Extract credentials
        base_url = config["langfuse_base_url"]
        public_key = config["langfuse_public_key"]
        secret_key = config["langfuse_secret_key"]

        # Initialize state manager
        state_manager = state.StateManager(state_dir)

        # Extract Task tool prompt from parent transcript (most recent Task call)
        subagent_prompt = extract_task_tool_prompt(
            transcript_path=parent_transcript_path
        )

        # Use empty string if prompt not found (graceful failure)
        if subagent_prompt is None:
            log_debug(
                "orchestrator",
                f"Could not extract prompt for subagent {subagent_name}, using empty string",
            )
            subagent_prompt = ""

        # Generate unique trace_id for subagent
        # Format: parent-session-id-subagent-name-uuid
        now = datetime.now(timezone.utc)
        subagent_trace_id = (
            f"{parent_session_id}-subagent-{subagent_name}-{str(uuid.uuid4())[:8]}"
        )

        # Create trace for subagent (NOT a span)
        # Use sessionId to link to parent session (this is how Langfuse links related sessions)
        trace = {
            "id": subagent_trace_id,
            "name": f"subagent:{subagent_name}",
            "sessionId": parent_session_id,  # Links to parent session
            "input": subagent_prompt,  # The Task tool prompt
            "timestamp": now.isoformat(),
            "startTime": now.isoformat(),  # BUG FIX #2: Add explicit startTime for latency calculation
            "metadata": {
                "subagent_session_id": subagent_session_id,
                "subagent_name": subagent_name,
            },
        }

        # Build batch event with trace-create (NOT span-create)
        batch = [
            {
                "id": subagent_trace_id,
                "timestamp": now.isoformat(),
                "type": "trace-create",  # CHANGED from span-create
                "body": trace,
            }
        ]

        # SECURITY: Sanitize batch before pushing (Task prompt may contain secrets)
        db_path = config.get("db_path", DEFAULT_DB_PATH)
        sanitized_batch = sanitize_trace(batch, db_path)

        # Push sanitized batch to Langfuse
        success, _ = push.push_batch_events(
            base_url,
            public_key,
            secret_key,
            sanitized_batch,
            timeout=INCREMENTAL_PUSH_TIMEOUT_SECONDS,
        )

        if not success:
            log_warning(
                "orchestrator",
                f"Failed to push subagent trace for {subagent_name}",
                None,
            )
            return None

        # Increment traces metric for subagent invocation (Story #34)
        db_path = config.get("db_path", DEFAULT_DB_PATH)
        try:
            increment_metric("traces", db_path)
        except Exception as e:
            log_warning("orchestrator", "Failed to increment traces metric", e)

        # Initialize subagent state with NEW subagent trace_id
        # CHANGED: Use subagent's own trace_id, not parent's
        state_manager.create_or_update(
            session_id=subagent_session_id,
            trace_id=subagent_trace_id,  # NEW subagent trace_id
            last_pushed_line=0,  # Start from beginning
            metadata={
                "current_trace_id": subagent_trace_id,  # For incremental pushes
                "trace_start_line": 0,
            },
        )

        log_info(
            "orchestrator",
            f"Created subagent trace {subagent_trace_id} for {subagent_name} "
            f"(linked to parent_session={parent_session_id})",
        )

        return subagent_trace_id

    except Exception as e:
        log_warning(
            "orchestrator", f"Subagent start handler error for {subagent_name}", e
        )
        return None
