#!/usr/bin/env python3
"""
Post-tool-use hook entry point for Credit-Aware Adaptive Throttling.

This module is called by the Claude Code post-tool-use hook.
It runs pacing checks and applies adaptive throttling.
"""

import os
import sys
import time
from pathlib import Path
from datetime import datetime
import json
from typing import Optional, Dict, Any

from . import pacing_engine, database, user_commands
from .database import record_blockage
from .constants import (
    DEFAULT_CONFIG,
    DEFAULT_DB_PATH,
    DEFAULT_CONFIG_PATH,
    DEFAULT_STATE_PATH,
    DEFAULT_EXTENSION_REGISTRY_PATH,
    MAX_DELAY_SECONDS,
)
from .transcript_reader import (
    get_last_n_messages_for_validation,
)
from .logger import log_warning, log_debug, log_info


def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Load configuration from file."""
    try:
        if os.path.exists(config_path):
            with open(config_path) as f:
                return json.load(f)
    except Exception as e:
        log_warning("hook", "Failed to load config, using defaults", e)

    return DEFAULT_CONFIG.copy()


def load_state(state_path: str = DEFAULT_STATE_PATH) -> dict:
    """Load hook state (last poll time, last cleanup time, session ID)."""
    # Default state
    default_state = {
        "session_id": f"session-{int(time.time())}",
        "last_poll_time": None,
        "last_cleanup_time": None,
        "in_subagent": False,
        "subagent_counter": 0,
        "tool_execution_count": 0,
    }

    try:
        if os.path.exists(state_path):
            with open(state_path) as f:
                data = json.load(f)

                # Convert timestamp strings back to datetime
                if data.get("last_poll_time"):
                    data["last_poll_time"] = datetime.fromisoformat(
                        data["last_poll_time"]
                    )
                if data.get("last_cleanup_time"):
                    data["last_cleanup_time"] = datetime.fromisoformat(
                        data["last_cleanup_time"]
                    )
                if data.get("last_user_interaction_time"):
                    data["last_user_interaction_time"] = datetime.fromisoformat(
                        data["last_user_interaction_time"]
                    )

                # Merge with defaults to ensure all required fields exist
                # Loaded data takes precedence, defaults fill in missing fields
                return {**default_state, **data}
    except Exception as e:
        log_warning("hook", "Failed to load state, using defaults", e)

    # File doesn't exist or failed to load - return defaults
    return default_state


def save_state(state: dict, state_path: str = DEFAULT_STATE_PATH):
    """Save hook state for next invocation."""
    try:
        # Ensure directory exists
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)

        # Convert datetime to string for JSON serialization
        state_copy = state.copy()
        if isinstance(state_copy.get("last_poll_time"), datetime):
            state_copy["last_poll_time"] = state_copy["last_poll_time"].isoformat()
        if isinstance(state_copy.get("last_cleanup_time"), datetime):
            state_copy["last_cleanup_time"] = state_copy[
                "last_cleanup_time"
            ].isoformat()
        if isinstance(state_copy.get("last_user_interaction_time"), datetime):
            state_copy["last_user_interaction_time"] = state_copy[
                "last_user_interaction_time"
            ].isoformat()

        with open(state_path, "w") as f:
            json.dump(state_copy, f)
    except Exception as e:
        log_warning("hook", "Failed to save state", e)


def execute_delay(delay_seconds: int):
    """Execute direct delay (sleep)."""
    if delay_seconds > 0:
        # Cap at MAX_DELAY_SECONDS (360s timeout - 10s safety margin)
        actual_delay = min(delay_seconds, MAX_DELAY_SECONDS)
        time.sleep(actual_delay)


def safe_print(message: str, file=None, flush: bool = True, end: str = "\n"):
    """
    Print to file with BrokenPipeError protection (Bug #2, #10).

    When Claude Code closes the pipe before the hook finishes writing,
    a BrokenPipeError is raised. This function catches it silently to
    prevent the entire hook from crashing.

    Args:
        message: Text to print
        file: File object to write to (defaults to sys.stdout)
        flush: Whether to flush after writing (default True)
        end: String appended after message (default newline)
    """
    if file is None:
        file = sys.stdout
    try:
        print(message, file=file, flush=flush, end=end)
    except BrokenPipeError:
        pass


def inject_prompt_delay(prompt: str):
    """Inject prompt for Claude to wait."""
    # Print to stdout so Claude sees it
    safe_print(prompt, file=sys.stdout, flush=True)


def display_intent_validation_guidance() -> str:
    """
    Load intent validation guidance from external file.

    Shared helper used by both SessionStart and SubagentStart hooks.
    Shows requirements for intent declaration and TDD enforcement.

    Returns:
        String containing the guidance text loaded from external file
    """
    from .prompt_loader import PromptLoader

    loader = PromptLoader()
    return loader.load_prompt(
        "intent_validation_guidance.md", subfolder="session_start"
    )


def get_model_preference_nudge(
    config: dict, include_usage: bool = True
) -> Optional[str]:
    """
    Generate model preference nudge message based on config.

    Used at session start and in subagent reminders to guide model selection
    for quota balancing.

    Args:
        config: Configuration dictionary
        include_usage: If True, include current usage stats (for session start)

    Returns:
        Nudge message string, or None if no preference set (auto mode)
    """
    import requests

    preferred_model = config.get("preferred_subagent_model", "auto")

    # No nudge if auto mode
    if preferred_model == "auto":
        return None

    # Build nudge message - ASSERTIVE language for quota balancing
    lines = []
    lines.append(f"⚠️  MANDATORY MODEL PREFERENCE: {preferred_model.upper()}")
    lines.append("")
    lines.append(
        f'   You MUST use model: "{preferred_model}" for ALL Task tool subagent calls.'
    )
    lines.append("")
    lines.append("   WHY: This is for QUOTA BALANCING, not capability.")
    lines.append(
        "   The user needs to balance token consumption across models to maximize"
    )
    lines.append(
        "   their usage window. Even if the default model 'works fine', using the"
    )
    lines.append(
        f"   preferred model ({preferred_model}) helps prevent hitting rate limits."
    )

    if include_usage:
        # Try to get current usage for context
        try:
            from . import api_client

            access_token = api_client.load_access_token()
            if access_token:
                usage_data = api_client.fetch_usage(access_token, timeout=5)
                if usage_data:
                    five_hour = usage_data.get("five_hour_util", 0)
                    seven_day = usage_data.get("seven_day_util", 0)
                    lines.append("")
                    lines.append(
                        f"   Current quotas: 5-hour {five_hour:.1f}%, 7-day {seven_day:.1f}%"
                    )
        except (requests.RequestException, KeyError, TypeError) as e:
            log_debug("hook", f"Could not fetch usage stats for model nudge: {e}")

    lines.append("")
    lines.append("   REQUIRED FORMAT:")
    lines.append(
        f"   Task(subagent_type='...', model='{preferred_model}', prompt='...')"
    )

    if include_usage:
        lines.append("")
        lines.append(
            f"   To change main session model, restart with: claude --model {preferred_model}"
        )

    return "\n".join(lines)


def get_secrets_nudge(subfolder: str) -> Optional[str]:
    """
    Load secrets management nudge from prompts directory.

    Args:
        subfolder: Prompt subfolder (session_start, pre_tool_use, post_tool_use)

    Returns:
        Nudge message string, or None if file not found
    """
    from .prompt_loader import PromptLoader

    try:
        loader = PromptLoader()
        message = loader.load_prompt("secrets_nudge.md", subfolder=subfolder)
        return message.strip()
    except FileNotFoundError:
        # Graceful degradation - no nudge if file missing
        return None


def run_session_start_hook():
    """
    Handle SessionStart hook - beginning of new session.

    Resets state based on session source:
    - source='startup': Full reset (new session)
    - source='resume': Update session_id but preserve counters
    - source='clear'/'compact': Reset counters but keep session_id
    - Missing source: Default to 'startup' behavior

    This prevents state corruption from cancelled subagents and stale data.

    SessionStart hook receives JSON via stdin:
    {
        "session_id": "abc123",
        "transcript_path": "/path/to/transcript.jsonl",
        "cwd": "/current/working/directory",
        "permission_mode": "default",
        "hook_event_name": "SessionStart",
        "source": "startup|resume|clear|compact",
        "model": "claude-sonnet-4-5-20250929"
    }

    Also displays intent validation mandate if feature is enabled.
    """
    # Load config to check master switch
    config = load_config(DEFAULT_CONFIG_PATH)

    # Master switch - all features disabled
    if not config.get("enabled", True):
        return

    # Read hook data from stdin
    hook_data = None
    session_id = None
    source = "startup"  # Default to startup behavior

    try:
        raw_input = sys.stdin.read()
        if raw_input:
            hook_data = json.loads(raw_input)
            session_id = hook_data.get("session_id")
            source = hook_data.get("source", "startup")
    except (json.JSONDecodeError, Exception) as e:
        # Graceful degradation - log warning and continue with defaults
        log_warning("hook", "Failed to parse SessionStart stdin data", e)

    # Load state
    state = load_state(DEFAULT_STATE_PATH)

    # Reset subagent tracking (always reset regardless of source)
    state["subagent_counter"] = 0
    state["in_subagent"] = False

    # Conditional reset based on source
    if source == "startup":
        # NEW SESSION - Full reset
        if session_id:
            state["session_id"] = session_id
        state["last_user_interaction_time"] = None
        state["tool_execution_count"] = 0
        state["last_poll_time"] = None
    elif source == "resume":
        # RESUME existing session - Update session_id but preserve counters
        if session_id:
            state["session_id"] = session_id
        # Keep: last_user_interaction_time, tool_execution_count, last_poll_time
    elif source in ("clear", "compact"):
        # CLEAR/COMPACT - Reset counters but keep session_id
        # (session_id from stdin should match existing session_id)
        state["last_user_interaction_time"] = None
        state["tool_execution_count"] = 0
        state["last_poll_time"] = None
        # Keep: session_id (same session continues)

    # Save state
    save_state(state, DEFAULT_STATE_PATH)

    # AC3: Cleanup stale Langfuse state files (>7 days old)
    try:
        from .langfuse import state as langfuse_state

        langfuse_state_dir = os.path.expanduser("~/.claude-pace-maker/langfuse_state")
        state_manager = langfuse_state.StateManager(langfuse_state_dir)
        state_manager.cleanup_stale_files(max_age_days=7)
    except Exception as e:
        # Log error but don't break session start
        log_warning("hook", "Failed to cleanup stale Langfuse state files", e)

    # Display intent validation mandate if enabled
    try:
        if config.get("intent_validation_enabled", False):
            guidance = display_intent_validation_guidance()
            safe_print(guidance, file=sys.stdout)
    except Exception as e:
        # Log error but don't break session start
        print(
            f"[PACE-MAKER WARNING] Failed to display intent guidance: {e}",
            file=sys.stderr,
        )

    # Display model preference nudge if configured
    try:
        model_nudge = get_model_preference_nudge(config, include_usage=True)
        if model_nudge:
            safe_print(model_nudge, file=sys.stdout)
    except Exception as e:
        # Log error but don't break session start
        log_warning("hook", "Failed to display model preference nudge", e)

    # Display secrets management nudge
    try:
        secrets_nudge = get_secrets_nudge("session_start")
        if secrets_nudge:
            safe_print(secrets_nudge, file=sys.stdout)
    except Exception as e:
        # Log error but don't break session start
        log_warning("hook", "Failed to display secrets nudge", e)

    # Display intel guidance for Prompt Intelligence Telemetry
    try:
        from .prompt_loader import PromptLoader

        loader = PromptLoader()
        intel_guidance = loader.load_prompt(
            "intel_guidance.md", subfolder="session_start"
        )
        safe_print(intel_guidance, file=sys.stdout)
    except FileNotFoundError:
        # Graceful degradation - intel guidance is optional
        log_debug("hook", "Intel guidance prompt not found - skipping")
    except Exception as e:
        # Log error but don't break session start
        log_warning("hook", "Failed to display intel guidance", e)


def run_subagent_start_hook():
    """
    Handle SubagentStart hook - entering subagent context.

    AC3: Creates child Langfuse span when Langfuse enabled
    Increments subagent_counter and sets in_subagent flag based on counter.
    Does NOT reset tool_execution_count (global counter persists).

    Also displays intent validation mandate if feature is enabled.
    """
    # Load config
    config = load_config(DEFAULT_CONFIG_PATH)

    # Read hook data from stdin for Langfuse integration
    hook_data = None
    try:
        raw_input = sys.stdin.read()
        # Debug: Log what we received
        log_debug(
            "hook",
            f"SubagentStart raw_input length: {len(raw_input) if raw_input else 0}",
        )
        log_debug(
            "hook",
            f"SubagentStart raw_input: {raw_input[:500] if raw_input else 'EMPTY'}",
        )
        if raw_input:
            hook_data = json.loads(raw_input)
    except (json.JSONDecodeError, Exception) as e:
        log_warning("hook", "Failed to parse SubagentStart stdin data", e)

    # Load state
    state = load_state(DEFAULT_STATE_PATH)

    # Increment counter
    state["subagent_counter"] = state.get("subagent_counter", 0) + 1

    # Set flag based on counter
    state["in_subagent"] = state["subagent_counter"] > 0

    # Save state
    save_state(state, DEFAULT_STATE_PATH)

    # AC3: Create Langfuse trace for subagent if hook data available
    if hook_data:
        log_debug("hook", f"SubagentStart hook_data keys: {list(hook_data.keys())}")
        log_debug("hook", f"SubagentStart hook_data: {hook_data}")
        subagent_trace_id = _handle_langfuse_subagent_start(hook_data, config)

        # Store subagent trace info in pacemaker state for:
        # 1. PostToolUse to link spans to subagent trace
        # 2. SubagentStop to finalize trace with output
        # Use dict keyed by agent_id to support concurrent subagents
        if subagent_trace_id:
            agent_id = hook_data.get("agent_id")
            parent_transcript_path = hook_data.get("transcript_path", "")

            # Store in dict keyed by agent_id (supports concurrent subagents)
            if "subagent_traces" not in state:
                state["subagent_traces"] = {}
            state["subagent_traces"][agent_id] = {
                "trace_id": subagent_trace_id,
                "parent_transcript_path": parent_transcript_path,
            }

            # Keep old keys for backward compatibility (will be deprecated)
            state["current_subagent_trace_id"] = subagent_trace_id
            state["current_subagent_agent_id"] = agent_id
            state["current_subagent_parent_transcript_path"] = parent_transcript_path

            save_state(state, DEFAULT_STATE_PATH)
            log_debug(
                "hook",
                f"SubagentStart: Stored subagent trace_id={subagent_trace_id} for agent_id={agent_id} in dict",
            )
    else:
        log_debug("hook", "SubagentStart: No hook_data received from stdin")

    # Display intent validation mandate if enabled
    try:
        if config.get("intent_validation_enabled", False):
            guidance = display_intent_validation_guidance()
            # Output JSON with additionalContext for subagent injection
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "SubagentStart",
                    "additionalContext": guidance,
                }
            }
            safe_print(json.dumps(output), file=sys.stdout)
    except Exception as e:
        # Log error but don't break subagent start
        print(
            f"[PACE-MAKER WARNING] Failed to display intent guidance: {e}",
            file=sys.stderr,
        )


def run_subagent_stop_hook():
    """
    Handle SubagentStop hook - exiting subagent context.

    AC5: Finalizes subagent span by flushing remaining transcript lines
    Decrements subagent_counter and sets in_subagent flag based on counter.
    Does NOT reset tool_execution_count (global counter persists).
    """
    # Load config
    config = load_config(DEFAULT_CONFIG_PATH)

    # Read hook data from stdin for Langfuse finalization
    hook_data = None
    try:
        raw_input = sys.stdin.read()
        if raw_input:
            hook_data = json.loads(raw_input)
            log_debug("hook", f"SubagentStop hook_data keys: {list(hook_data.keys())}")
            log_debug("hook", f"SubagentStop hook_data: {hook_data}")
    except (json.JSONDecodeError, Exception) as e:
        log_warning("hook", "Failed to parse SubagentStop stdin data", e)

    # Load state
    state = load_state(DEFAULT_STATE_PATH)

    # Decrement counter (never go below 0)
    state["subagent_counter"] = max(0, state.get("subagent_counter", 0) - 1)

    # Set flag based on counter
    state["in_subagent"] = state["subagent_counter"] > 0

    # Save state
    save_state(state, DEFAULT_STATE_PATH)

    # AC5: Subagent trace finalization
    # Get agent_id from hook_data to lookup correct trace in dict
    hook_agent_id = hook_data.get("agent_id") if hook_data else None

    # Lookup trace info from dict (supports concurrent subagents)
    trace_info = None
    if hook_agent_id:
        subagent_traces = state.get("subagent_traces", {})
        trace_info = subagent_traces.get(hook_agent_id)

    # Backward compatibility: fallback to old single-value keys if dict lookup fails
    if not trace_info:
        old_trace_id = state.get("current_subagent_trace_id")
        old_agent_id = state.get("current_subagent_agent_id")
        old_parent_path = state.get("current_subagent_parent_transcript_path")
        if old_trace_id:
            trace_info = {
                "trace_id": old_trace_id,
                "parent_transcript_path": old_parent_path,
            }
            hook_agent_id = old_agent_id

    if trace_info and config.get("langfuse_enabled", False):
        subagent_trace_id = trace_info.get("trace_id")
        parent_transcript_path = trace_info.get("parent_transcript_path")

        try:
            from .langfuse import orchestrator

            # Get parent transcript path for extracting subagent output
            # Try to get transcript path from hook_data first, then use stored path
            parent_session_id = hook_data.get("session_id") if hook_data else None
            if parent_session_id:
                transcript_from_session = get_transcript_path(parent_session_id)
                if transcript_from_session:
                    parent_transcript_path = transcript_from_session

            # Extract agent_transcript_path from hook_data (NEW)
            # This is the subagent's own transcript where output already exists
            agent_transcript_path = (
                hook_data.get("agent_transcript_path") if hook_data else None
            )

            # Extract last_assistant_message from hook_data (fallback for output)
            last_assistant_message = (
                hook_data.get("last_assistant_message") if hook_data else None
            )

            # Finalize subagent trace with output
            # Pass agent_transcript_path to read from subagent's own transcript
            # Pass agent_id to correctly correlate output when multiple subagents run (fallback)
            orchestrator.handle_subagent_stop(
                config=config,
                subagent_trace_id=subagent_trace_id,
                parent_transcript_path=parent_transcript_path,
                agent_id=hook_agent_id,
                agent_transcript_path=agent_transcript_path,
                last_assistant_message=last_assistant_message,
            )
            log_info(
                "hook",
                f"SubagentStop: Finalized subagent trace {subagent_trace_id} for agent_id={hook_agent_id}",
            )

            # BUG #1 FIX: Flush parent session's pending_trace
            # The typical flow is: UserPromptSubmit -> SubagentStart -> SubagentStop -> Stop
            # Without this, pending_trace from UserPromptSubmit is never consumed
            # because PostToolUse never fires in the parent session during subagent execution.
            try:
                from .langfuse import state as langfuse_state

                if parent_session_id:
                    langfuse_state_dir = os.path.expanduser(
                        "~/.claude-pace-maker/langfuse_state"
                    )
                    state_mgr = langfuse_state.StateManager(langfuse_state_dir)
                    parent_state = state_mgr.read(parent_session_id)

                    if parent_state and parent_state.get("pending_trace"):
                        orchestrator.flush_pending_trace(
                            config=config,
                            session_id=parent_session_id,
                            state_manager=state_mgr,
                            existing_state=parent_state,
                            caller="run_subagent_stop_hook",
                        )
                        log_info(
                            "hook",
                            f"SubagentStop: Flushed parent pending trace for {parent_session_id}",
                        )
            except Exception as e:
                log_warning(
                    "hook",
                    "SubagentStop: Failed to flush parent pending trace",
                    e,
                )
        except Exception as e:
            # Graceful failure - log but don't break hook
            log_warning(
                "hook", f"SubagentStop: Failed to finalize trace {subagent_trace_id}", e
            )

        # Clear this agent's trace info from dict
        if hook_agent_id and "subagent_traces" in state:
            state["subagent_traces"].pop(hook_agent_id, None)

        # Clear old backward-compat keys
        state.pop("current_subagent_trace_id", None)
        state.pop("current_subagent_agent_id", None)
        state.pop("current_subagent_parent_transcript_path", None)
        save_state(state, DEFAULT_STATE_PATH)
    elif not trace_info:
        log_debug("hook", "SubagentStop: No subagent trace_id to finalize")


def should_inject_reminder(
    state: dict, config: dict, tool_name: Optional[str] = None
) -> bool:
    """
    Determine if we should inject the subagent reminder.

    Conditions:
    - NOT in subagent (in_subagent == false)
    - Feature enabled in config
    - EITHER:
      a) Write tool used in main context (immediate nudge, bypasses counter)
      b) tool_execution_count is multiple of frequency (every 5 executions)

    Args:
        state: Current session state
        config: Configuration dictionary
        tool_name: Name of the tool that was just executed (optional)

    Returns:
        True if should inject reminder, False otherwise
    """
    # Skip if in subagent
    if state.get("in_subagent", False):
        return False

    # Skip if disabled
    if not config.get("subagent_reminder_enabled", True):
        return False

    # IMMEDIATE NUDGE: Write or Edit tool used in main context
    if tool_name in ("Write", "Edit"):
        return True

    # COUNTER-BASED NUDGE: Check frequency (every 5 executions by default)
    count = state.get("tool_execution_count", 0)
    frequency = config.get("subagent_reminder_frequency", 5)

    # Only inject on multiples of frequency (and not on count 0)
    return count > 0 and count % frequency == 0


def inject_subagent_reminder(config: dict) -> Optional[str]:
    """
    Get subagent reminder message.

    Returns the reminder message that should be shown to Claude.
    Does NOT print to stdout - caller is responsible for output.
    Includes model preference nudge if configured.

    Args:
        config: Configuration dictionary

    Returns:
        Reminder message string, or None if not applicable
    """
    from .prompt_loader import PromptLoader

    # Try loading from external prompt file first
    try:
        loader = PromptLoader()
        message = loader.load_prompt("subagent_reminder.md", subfolder="post_tool_use")
        message = message.strip()
    except FileNotFoundError:
        # Fallback to config or hardcoded message
        message = config.get(
            "subagent_reminder_message",
            "💡 Consider using the Task tool to delegate work to specialized subagents (per your guidelines)",
        )

    # Append model preference nudge if configured (without usage stats for brevity)
    model_nudge = get_model_preference_nudge(config, include_usage=False)
    if model_nudge:
        message = f"{message}\n\n{model_nudge}"

    return message


def run_hook():
    """
    Main hook execution with pacing AND incremental Langfuse push.

    AC2: Trigger incremental Langfuse push on PostToolUse

    Returns:
        bool: True if code review feedback was provided, False otherwise
    """

    # Track pending message (code review takes priority over subagent nudge)
    pending_message = None

    # Track if feedback was provided (for exit code decision)
    feedback_provided = False

    # Load configuration
    config = load_config(DEFAULT_CONFIG_PATH)

    # Check if enabled
    if not config.get("enabled", True):
        return feedback_provided  # Disabled - do nothing

    # Read hook data from stdin to get tool_name and tool_response
    tool_name = None
    tool_response = None
    hook_data = None
    session_id = None
    transcript_path = None
    try:
        raw_input = sys.stdin.read()
        if raw_input:
            hook_data = json.loads(raw_input)
            tool_name = hook_data.get("tool_name")
            tool_input = hook_data.get("tool_input", {})
            tool_response = hook_data.get("tool_response")
            session_id = hook_data.get("session_id")
            transcript_path = hook_data.get("transcript_path")
    except (json.JSONDecodeError, Exception) as e:
        log_warning("hook", "Failed to parse hook data from stdin", e)

    # Load state
    state = load_state(DEFAULT_STATE_PATH)

    # Increment global tool execution counter
    state["tool_execution_count"] = state.get("tool_execution_count", 0) + 1

    # Ensure database is initialized
    db_path = DEFAULT_DB_PATH
    database.initialize_database(db_path)

    # Pacing section - wrapped to prevent BrokenPipeError from blocking Langfuse
    try:
        # Run pacing check
        result = pacing_engine.run_pacing_check(
            db_path=db_path,
            session_id=state["session_id"],
            last_poll_time=state.get("last_poll_time"),
            poll_interval=config.get("poll_interval", 60),
            last_cleanup_time=state.get("last_cleanup_time"),
            safety_buffer_pct=config.get("safety_buffer_pct", 95.0),
            preload_hours=config.get("preload_hours", 12.0),
            api_timeout_seconds=config.get("api_timeout_seconds", 10),
            cleanup_interval_hours=config.get("cleanup_interval_hours", 24),
            retention_days=config.get("retention_days", 60),
            weekly_limit_enabled=config.get("weekly_limit_enabled", True),
            five_hour_limit_enabled=config.get("five_hour_limit_enabled", True),
        )

        # Update state if polled or cleaned up
        state_changed = False
        if result.get("polled"):
            state["last_poll_time"] = result.get("poll_time")
            state_changed = True
        if result.get("cleanup_time"):
            state["last_cleanup_time"] = result.get("cleanup_time")
            state_changed = True

        if state_changed:
            save_state(state)

        # Apply throttling if needed
        decision = result.get("decision", {})

        # Show usage status if we polled
        if result.get("polled") and decision:
            five_hour = decision.get("five_hour", {})
            constrained = decision.get("constrained_window")

            if five_hour and constrained:
                util = five_hour.get("utilization", 0)
                target = five_hour.get("target", 0)
                overage = util - target

                print(
                    f"[PACING] 5-hour usage: {util}% (target: {target:.1f}%, over by: {overage:.1f}%)",
                    file=sys.stderr,
                    flush=True,
                )

        if decision.get("should_throttle"):
            # Handle both cached decisions (direct delay_seconds) and fresh decisions (strategy dict)
            strategy = decision.get("strategy", {})

            # Check if this is a cached decision (has delay_seconds directly)
            if "delay_seconds" in decision and not strategy:
                delay = decision.get("delay_seconds", 0)
            else:
                # Fresh decision with strategy
                delay = strategy.get("delay_seconds", 0)

            # Always execute delay if delay > 0
            if delay > 0:
                # AC5: Record blockage for pacing throttle
                record_blockage(
                    db_path=db_path,
                    category="pacing_quota",
                    reason=f"Throttle delay {delay}s applied due to quota protection",
                    hook_type="post_tool_use",
                    session_id=state.get("session_id", "unknown"),
                    details={"delay_seconds": delay},
                )
                execute_delay(delay)

        # Capture subagent reminder if conditions met (don't print yet)
        if should_inject_reminder(state, config, tool_name):
            pending_message = inject_subagent_reminder(config)

        # Add secrets nudge to pending message
        try:
            secrets_nudge = get_secrets_nudge("post_tool_use")
            if secrets_nudge:
                if pending_message:
                    pending_message = f"{pending_message}\n\n{secrets_nudge}"
                else:
                    pending_message = secrets_nudge
        except Exception as e:
            log_warning("hook", "Failed to load secrets nudge for post_tool_use", e)

        # Save state (always save to persist counter)
        state_changed = True
        if state_changed:
            save_state(state, DEFAULT_STATE_PATH)

    except BrokenPipeError:
        log_warning(
            "hook",
            "BrokenPipeError during pacing section, continuing to Langfuse",
            None,
        )
    except Exception as e:
        log_warning("hook", f"Error during pacing section: {e}", e)

    # DIAGNOSTIC: Log whether we'll enter Langfuse section
    log_debug(
        "hook",
        f"PostToolUse: session_id={session_id}, hook_data_present={hook_data is not None}, tool={tool_name}",
    )

    # AC2: Create span for tool call (trace-per-turn)
    if session_id and hook_data:
        try:
            from .langfuse import orchestrator

            langfuse_state_dir = os.path.expanduser(
                "~/.claude-pace-maker/langfuse_state"
            )

            log_debug(
                "hook",
                f"PostToolUse: Calling handle_post_tool_use for session={session_id}, tool={tool_name}",
            )

            # Create spans from transcript (graceful failure per AC5)
            # Passes tool_response, tool_name, and tool_input from hook to capture current tool's full metadata
            result = orchestrator.handle_post_tool_use(
                config=config,
                session_id=session_id,
                transcript_path=transcript_path,
                state_dir=langfuse_state_dir,
                tool_response=tool_response,
                tool_name=tool_name,
                tool_input=tool_input,
            )

            log_debug(
                "hook",
                f"PostToolUse: handle_post_tool_use returned {result} for session={session_id}",
            )

        except Exception as e:
            # AC5: Graceful failure - log error but don't crash hook
            log_warning("hook", "Langfuse span creation failed on PostToolUse", e)

    # Print final message if any (code review takes priority over subagent nudge)
    if pending_message:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": pending_message,
            }
        }
        safe_print(json.dumps(output), file=sys.stdout)
        return False  # Not blocking feedback, just injecting context

    # Return whether feedback was provided
    return feedback_provided


def parse_user_prompt_input(raw_input: str) -> dict:
    """
    Parse user prompt input from Claude Code.

    Handles both JSON format and plain text fallback.

    Args:
        raw_input: Raw stdin input from Claude Code

    Returns:
        Dict with session_id and prompt keys
    """
    try:
        # Try to parse as JSON first
        hook_data = json.loads(raw_input)
        session_id = hook_data.get("session_id", f"sess-{int(time.time())}")
        prompt = hook_data.get("prompt", "")
        return {"session_id": session_id, "prompt": prompt}
    except json.JSONDecodeError:
        # Fallback to plain text - generate session ID
        return {
            "session_id": f"sess-{int(time.time())}",
            "prompt": raw_input.strip(),
        }


def get_transcript_path(session_id: str) -> Optional[str]:
    """
    Derive transcript path from session_id and current working directory.

    Claude Code stores transcripts at:
    ~/.claude/projects/<cwd-with-slashes-replaced-by-dashes>/<session_id>.jsonl

    Args:
        session_id: Session UUID from hook data

    Returns:
        Path to transcript file, or None if not found
    """
    # Get current working directory
    cwd = os.getcwd()

    # Convert to project directory name (replace / with -)
    # /home/jsbattig/Dev/code-indexer -> -home-jsbattig-Dev-code-indexer
    project_dir_name = cwd.replace("/", "-")

    # Build transcript path
    transcript_path = os.path.expanduser(
        f"~/.claude/projects/{project_dir_name}/{session_id}.jsonl"
    )

    # Return only if file exists
    if os.path.exists(transcript_path):
        return transcript_path
    return None


def run_user_prompt_submit():
    """
    Handle user prompt submit hook.

    Responsibilities:
    - Intercept pace-maker commands
    - Update state (interaction time, subagent tracking)
    - AC1: Trigger incremental Langfuse push
    """
    try:
        # Read user input from stdin first
        raw_input = sys.stdin.read().strip()

        # Parse input (JSON or plain text)
        parsed_data = parse_user_prompt_input(raw_input)
        user_input = parsed_data["prompt"]
        session_id = parsed_data["session_id"]

        # Check if this is a pace-maker command BEFORE updating state
        result = user_commands.handle_user_prompt(
            user_input, DEFAULT_CONFIG_PATH, DEFAULT_DB_PATH
        )

        # Update state - but only track interaction time for non-pace-maker commands
        state = load_state(DEFAULT_STATE_PATH)
        state["subagent_counter"] = 0
        state["in_subagent"] = False
        state["silent_tool_nudge_count"] = 0

        # Only update last_user_interaction_time for actual prompts to Claude
        # NOT for pace-maker commands (which are just checking status/settings)
        if not result["intercepted"]:
            state["last_user_interaction_time"] = datetime.now()

        save_state(state, DEFAULT_STATE_PATH)

        # AC1: Trigger trace creation for user prompt (trace-per-turn)
        # Only for non-intercepted prompts (actual Claude interactions)
        if not result["intercepted"]:
            try:
                from .langfuse import orchestrator

                # Derive transcript path from session_id (Claude Code doesn't send it)
                transcript_path = get_transcript_path(session_id)

                if transcript_path:
                    config = load_config(DEFAULT_CONFIG_PATH)
                    langfuse_state_dir = os.path.expanduser(
                        "~/.claude-pace-maker/langfuse_state"
                    )

                    # Create trace for user prompt (graceful failure per AC5)
                    orchestrator.handle_user_prompt_submit(
                        config=config,
                        session_id=session_id,
                        transcript_path=transcript_path,
                        state_dir=langfuse_state_dir,
                        user_message=user_input,
                    )
                    # Note: We don't check return value - failures are logged but don't block hook

            except Exception as e:
                # AC5: Graceful failure - log error but don't crash hook
                log_warning(
                    "hook", "Langfuse trace creation failed on UserPromptSubmit", e
                )

        if result["intercepted"]:
            # Command was intercepted - output JSON to block and display output
            response = {"decision": "block", "reason": result["output"]}
            safe_print(json.dumps(response), file=sys.stdout)
            sys.exit(0)

        # Output with intel nudge reminder
        intel_nudge = (
            "§ intel: Start your FIRST response to this user prompt with § intel line. "
            "Emit ONCE only — do NOT repeat in subsequent tool-use messages within this turn. "
            "EXACT format: § △0.0-1.0 ◎surg|const|outc|expl ■bug|feat|refac|research|test|docs|debug|conf|other ◇0.0-1.0 ↻1-9 "
            "(△◇ = decimals NOT words, ◎■ = codes NOT synonyms). "
            "NEVER emit § for background task completions, subagent results, or system notifications — ONLY for human-typed prompts."
        )
        output = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": intel_nudge,
            }
        }
        safe_print(json.dumps(output), file=sys.stdout)
        sys.exit(0)

    except Exception as e:
        # Graceful degradation - log error and pass through
        print(f"[PACE-MAKER ERROR] {e}", file=sys.stderr)
        # Re-print original input on error
        try:
            sys.stdin.seek(0)
            safe_print(sys.stdin.read(), file=sys.stdout)
        except Exception:
            pass
        sys.exit(0)


def get_last_assistant_message(transcript_path: str) -> str:
    """
    Read JSONL transcript and extract ONLY the last assistant message.

    Args:
        transcript_path: Path to the JSONL transcript file

    Returns:
        Text from the last assistant message only
    """
    try:
        last_assistant_text = ""

        with open(transcript_path, "r") as f:
            for line in f:
                entry = json.loads(line)

                # Check if this is an assistant message
                message = entry.get("message", {})
                role = message.get("role")

                if role == "assistant":
                    # This is an assistant message - extract its text
                    content = message.get("content", [])
                    text_parts = []

                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                    elif isinstance(content, str):
                        text_parts.append(content)

                    # Store this as the last assistant message (will be overwritten by next one)
                    if text_parts:
                        last_assistant_text = "\n".join(text_parts)

        return last_assistant_text

    except Exception as e:
        log_warning("hook", "Failed to read last assistant message", e)
        return ""


# Backwards compatibility alias for tests
read_conversation_from_transcript = get_last_assistant_message


def get_last_n_messages(transcript_path: str, n: int = 5) -> list:
    """
    Read JSONL transcript and extract the last N messages (user + assistant).

    Args:
        transcript_path: Path to the JSONL transcript file
        n: Number of messages to extract (default: 5)

    Returns:
        List of message texts (most recent last)
    """
    try:
        all_messages = []

        with open(transcript_path, "r") as f:
            for line in f:
                entry = json.loads(line)

                # Extract message content
                message = entry.get("message", {})
                role = message.get("role")
                content = message.get("content", [])

                text_parts = []
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                elif isinstance(content, str):
                    text_parts.append(content)

                if text_parts:
                    message_text = "\n".join(text_parts)
                    # Prefix with role for context
                    all_messages.append(f"[{role.upper()}]\n{message_text}")

        # Return last N messages
        return all_messages[-n:] if len(all_messages) >= n else all_messages

    except Exception as e:
        log_warning("hook", "Failed to read messages from transcript", e)
        return []


def should_run_tempo(config: dict, state: dict) -> bool:
    """
    Determine if tempo tracking should run based on global and session settings.

    Precedence logic:
    1. Check if session override exists (tempo_session_enabled in state)
    2. If session override exists, use that value
    3. Otherwise, use tempo_mode setting (auto/on/off)
    4. For auto mode, check last_user_interaction_time against threshold

    Supports backward compatibility with tempo_enabled boolean.

    Args:
        config: Configuration dictionary with tempo_mode (or legacy tempo_enabled)
        state: State dictionary with tempo_session_enabled and last_user_interaction_time (optional)

    Returns:
        True if tempo should run, False otherwise
    """
    # Check for session override
    tempo_session_enabled = state.get("tempo_session_enabled")

    # If session override exists, use it
    if tempo_session_enabled is not None:
        return tempo_session_enabled

    # Get tempo_mode from config (with backward compatibility)
    tempo_mode = config.get("tempo_mode")

    # Backward compatibility: check for old tempo_enabled boolean
    if tempo_mode is None:
        tempo_enabled = config.get("tempo_enabled")
        if tempo_enabled is not None:
            # Map old boolean to new mode
            tempo_mode = "on" if tempo_enabled else "off"
        else:
            # Default to auto if nothing specified
            tempo_mode = "auto"

    # Handle tempo_mode
    if tempo_mode == "off":
        return False

    if tempo_mode == "on":
        return True

    if tempo_mode == "auto":
        # Check user activity
        last_interaction = state.get("last_user_interaction_time")

        # No interaction recorded, assume unattended
        if last_interaction is None:
            return True

        # Check if elapsed time exceeds threshold
        threshold_minutes = config.get("auto_tempo_threshold_minutes", 10)
        elapsed_seconds = (datetime.now() - last_interaction).total_seconds()
        elapsed_minutes = elapsed_seconds / 60

        return elapsed_minutes >= threshold_minutes

    # Unknown mode, default to enabled for safety
    return True


def format_elapsed_time(last_interaction_time) -> str:
    """
    Format elapsed time since last interaction in human-readable format.

    Args:
        last_interaction_time: datetime object or None

    Returns:
        Human-readable string like "5 minutes ago", "2.5 hours ago", or "never"
    """
    if last_interaction_time is None:
        return "never"

    elapsed_seconds = (datetime.now() - last_interaction_time).total_seconds()

    if elapsed_seconds < 60:
        return f"{int(elapsed_seconds)} seconds ago"
    elif elapsed_seconds < 3600:
        minutes = int(elapsed_seconds / 60)
        return f"{minutes} minutes ago"
    else:
        hours = elapsed_seconds / 3600
        return f"{hours:.1f} hours ago"


# Langfuse push timeout for AC4 (<2s constraint)
LANGFUSE_PUSH_TIMEOUT_SECONDS = 2


def _handle_langfuse_subagent_start(hook_data: dict, config: dict) -> Optional[str]:
    """
    Handle Langfuse trace creation for SubagentStart hook.

    AC3: Creates trace for subagent linked to parent session when subagent starts.

    Uses orchestrator.handle_subagent_start() for real API integration.

    Args:
        hook_data: Hook data from stdin with subagent metadata
        config: Configuration dict

    Returns:
        Subagent trace ID if successful, None if skipped or failed
    """
    # Check if Langfuse enabled
    if not config.get("langfuse_enabled", False):
        return None

    try:
        from .langfuse import orchestrator

        # Extract subagent metadata from Claude Code's actual hook data format:
        # - session_id: This is the PARENT's session ID (Claude Code's naming)
        # - agent_id: The subagent's identifier
        # - agent_type: The subagent type (e.g., "Explore", "tdd-engineer")
        # - transcript_path: Parent's transcript path
        parent_session_id = hook_data.get("session_id")
        agent_id = hook_data.get("agent_id")
        subagent_name = hook_data.get("agent_type", "subagent")
        parent_transcript_path = hook_data.get("transcript_path", "")

        # Validate required fields
        if not parent_session_id or not agent_id:
            log_debug(
                "hook",
                "SubagentStart: Missing required fields (session_id or agent_id)",
            )
            return None

        # Create a subagent session ID from the agent_id
        subagent_session_id = f"subagent-{agent_id}"

        # State directory
        langfuse_state_dir = os.path.expanduser("~/.claude-pace-maker/langfuse_state")

        # Call orchestrator handler - creates TRACE for subagent (not span)
        subagent_trace_id = orchestrator.handle_subagent_start(
            config=config,
            parent_session_id=parent_session_id,
            subagent_session_id=subagent_session_id,
            subagent_name=subagent_name,
            parent_transcript_path=parent_transcript_path,
            state_dir=langfuse_state_dir,
        )

        if subagent_trace_id:
            log_info(
                "hook",
                f"SubagentStart: Created subagent trace {subagent_trace_id} for {subagent_name}",
            )

        return subagent_trace_id

    except Exception as e:
        # AC5: Graceful failure - log error but don't crash hook
        log_warning(
            "hook", "Failed to create Langfuse subagent trace on SubagentStart", e
        )
        return None


def run_langfuse_push(config: dict, session_id: str, transcript_path: str) -> bool:
    """
    DEPRECATED: Legacy generation event push - replaced by span-based architecture.

    This function created "claude-code-generation" events with token tracking.
    Now replaced by:
    - handle_user_prompt_submit() - creates traces
    - handle_post_tool_use() - creates text/tool spans
    - handle_stop_finalize() - finalizes traces

    Kept for backward compatibility but does nothing.

    Args:
        config: Configuration dict (ignored)
        session_id: Session identifier (ignored)
        transcript_path: Path to transcript JSONL file (ignored)

    Returns:
        True (always succeeds as no-op)
    """
    log_debug(
        "hook",
        "run_langfuse_push called but deprecated - using span-based architecture",
    )
    return True


def is_context_exhaustion_detected(transcript_path: str) -> bool:
    """
    Detect if conversation is approaching or has reached context exhaustion.

    Detects TWO scenarios based on actual code-indexer conversation pattern:

    SCENARIO 1 - Early Warning (Context Low):
    - Last compact_boundary has preTokens > 180000 (approaching 200K limit)
    - This is when Claude Code shows "Context low · Run /compact to compact & continue"
    - Allows graceful exit BEFORE hitting infinite loop

    SCENARIO 2 - Terminal Exhaustion:
    - Last message is "Prompt is too long" API error
    - Context window completely exhausted
    - Compaction failed
    - No recovery possible

    Real pattern from code-indexer conversation (f9185385):
    - Line 1121: compact_boundary with preTokens=185279 (danger zone)
    - Line 1135: First "Prompt is too long" error (~14 min later)
    - Lines 1135-1570: Infinite loop of error + stop hook + error...

    This prevents the race condition where:
    - API returns "Prompt is too long" error
    - Stop hook blocks exit demanding response
    - Claude cannot respond (context full)
    - Loop repeats 40+ times

    Args:
        transcript_path: Path to JSONL transcript file

    Returns:
        True if context exhaustion detected (early or terminal), False otherwise
    """
    try:
        # Read last 50KB to capture recent entries including compact_boundary
        with open(transcript_path, "rb") as f:
            f.seek(0, 2)  # End of file
            file_size = f.tell()

            if file_size == 0:
                return False

            # Read last 50KB (enough for multiple JSONL entries)
            read_size = min(50000, file_size)
            f.seek(file_size - read_size)

            # Decode and split into lines
            content = f.read().decode("utf-8", errors="ignore")
            lines = [line.strip() for line in content.split("\n") if line.strip()]

            if not lines:
                return False

            # Parse last entry
            last_entry = json.loads(lines[-1])

            # SCENARIO 2: Check for terminal "Prompt is too long" error
            error = last_entry.get("error")
            if error == "invalid_request":
                message = last_entry.get("message", {})
                role = message.get("role")

                if role == "assistant":
                    # Extract text from content blocks
                    content_blocks = message.get("content", [])
                    text_parts = []

                    if isinstance(content_blocks, list):
                        for block in content_blocks:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text_parts.append(block.get("text", "").strip())

                    text = " ".join(text_parts).strip()

                    if text == "Prompt is too long":
                        log_debug(
                            "hook", "=== TERMINAL CONTEXT EXHAUSTION DETECTED ==="
                        )
                        log_debug(
                            "hook", "Last message: 'Prompt is too long' API error"
                        )
                        log_debug(
                            "hook", "Allowing graceful exit - conversation is dead"
                        )
                        return True

            # SCENARIO 1: Check for high preTokens in recent compact_boundary
            # Walk backwards through last ~20 entries looking for compact_boundary
            for line in reversed(lines[-20:]):
                try:
                    entry = json.loads(line)

                    if entry.get("subtype") == "compact_boundary":
                        compact_metadata = entry.get("compactMetadata", {})
                        pre_tokens = compact_metadata.get("preTokens", 0)

                        # Danger threshold: 180K tokens (90% of 200K Sonnet limit)
                        if pre_tokens > 180000:
                            log_debug(
                                "hook", "=== EARLY WARNING: CONTEXT LOW DETECTED ==="
                            )
                            log_debug(
                                "hook", f"Last compact_boundary preTokens: {pre_tokens}"
                            )
                            log_debug(
                                "hook",
                                "Context approaching exhaustion - allowing graceful exit",
                            )
                            log_debug(
                                "hook",
                                "User should run /compact or start fresh conversation",
                            )
                            return True

                        # Found compact_boundary but preTokens OK - stop searching
                        break

                except json.JSONDecodeError:
                    continue

            return False

    except Exception as e:
        log_warning("hook", "Failed to check context exhaustion", e)
        return False


def run_stop_hook():
    """
    Handle Stop hook using intent-based validation.

    Refactored behavior:
    - Extracts first N user messages from transcript (original mission)
    - Extracts last N user messages from transcript (recent context)
    - Extracts last assistant message from transcript (what Claude just said)
    - Calls SDK to validate if Claude completed the user's original request
    - SDK acts as user proxy to judge completion
    - Returns APPROVED (allow exit) or BLOCKED with feedback

    Returns:
        Dictionary with Claude Code Stop hook schema:
        - {"continue": True} - Allow exit
        - {"decision": "block", "reason": "feedback"} - Block with feedback
    """

    try:
        # === STOP HOOK ENTRY POINT ===
        log_info("hook", "=" * 70)
        log_info("hook", "STOP HOOK FIRED")
        log_info("hook", f"Timestamp: {datetime.now().isoformat()}")
        log_info("hook", "=" * 70)

        # Load config and state to check if tempo should run
        config = load_config(DEFAULT_CONFIG_PATH)

        # Master switch - all features disabled
        if not config.get("enabled", True):
            log_info("hook", "Pace maker disabled - allowing exit")
            return {"continue": True}

        state = load_state(DEFAULT_STATE_PATH)

        # Log auto tempo status
        tempo_mode = config.get("tempo_mode", "auto")
        tempo_session_override = state.get("tempo_session_override")
        last_user_interaction = state.get("last_user_interaction_time")
        if last_user_interaction:
            elapsed = (datetime.now() - last_user_interaction).total_seconds()
            log_info(
                "hook",
                f"Auto Tempo Status: mode={tempo_mode}, "
                f"session_override={tempo_session_override}, "
                f"last_interaction={elapsed:.1f}s ago",
            )
        else:
            log_info(
                "hook", f"Auto Tempo Status: mode={tempo_mode}, no interaction tracked"
            )

        # Read hook data from stdin FIRST (needed for Langfuse regardless of tempo)
        raw_input = sys.stdin.read()
        if not raw_input:
            log_debug("hook", "No raw input from stdin")
            return {"continue": True}

        hook_data = json.loads(raw_input)
        session_id = hook_data.get("session_id")

        # Derive transcript path from session_id (Claude Code doesn't send it)
        transcript_path = get_transcript_path(session_id) if session_id else None

        # Finalize current trace with Claude's output (ALWAYS runs, regardless of tempo)
        # This adds the output field to the trace before final push
        try:
            from .langfuse import orchestrator

            langfuse_state_dir = os.path.expanduser(
                "~/.claude-pace-maker/langfuse_state"
            )
            orchestrator.handle_stop_finalize(
                config=config,
                session_id=session_id,
                transcript_path=transcript_path,
                state_dir=langfuse_state_dir,
            )
        except Exception as e:
            log_warning("hook", "Failed to finalize Langfuse trace", e)

        # AC4: Legacy generation event push removed
        # NOTE: Trace finalization now handled by handle_stop_finalize above
        # No need for separate run_langfuse_push - span-based architecture handles everything

        # Check transcript exists — needed for both silent-stop and intent validation
        log_debug("hook", f"Session ID: {session_id}")
        log_debug("hook", f"Transcript path: {transcript_path}")

        if not transcript_path or not os.path.exists(transcript_path):
            log_debug("hook", "No transcript - allow exit")
            return {"continue": True}

        # CRITICAL: Check for context exhaustion BEFORE any blocking logic.
        # If conversation hit "Prompt is too long" error, compaction failed
        # and Claude cannot respond - must allow graceful exit.
        if is_context_exhaustion_detected(transcript_path):
            log_debug("hook", "Allowing graceful exit due to context exhaustion")
            return {"continue": True}

        # Silent tool stop detection — runs independently of tempo gate.
        # When Claude stops immediately after a tool use without producing text,
        # nudge it to continue rather than letting the session stall.
        from .transcript_reader import detect_silent_tool_stop

        if detect_silent_tool_stop(transcript_path):
            nudge_count = state.get("silent_tool_nudge_count", 0)
            max_nudges = config.get("max_silent_tool_nudges", 3)
            log_debug(
                "hook",
                f"Silent tool stop detected. nudge_count={nudge_count}, max_nudges={max_nudges}",
            )

            if nudge_count < max_nudges:
                # Load continuation nudge prompt
                try:
                    from .prompt_loader import PromptLoader

                    loader = PromptLoader()
                    nudge_message = loader.load_prompt(
                        "continuation_nudge.md", subfolder="stop"
                    )
                except Exception as e:
                    log_warning("hook", "Failed to load continuation nudge prompt", e)
                    nudge_message = (
                        "You stopped after a tool use without providing text output. "
                        "Please continue your work."
                    )

                # Increment counter and save state
                state["silent_tool_nudge_count"] = nudge_count + 1
                save_state(state, DEFAULT_STATE_PATH)

                log_debug(
                    "hook",
                    f"Blocking silent stop (nudge {nudge_count + 1}/{max_nudges})",
                )
                return {"decision": "block", "reason": nudge_message}
            else:
                # Max nudges reached — reset counter and allow exit
                state["silent_tool_nudge_count"] = 0
                save_state(state, DEFAULT_STATE_PATH)
                log_debug("hook", "Max silent tool nudges reached - allowing exit")
                return {"continue": True}

        # NOW check tempo - if disabled, allow exit after silent-stop check
        if not should_run_tempo(config, state):
            log_debug("hook", "Tempo disabled - allow exit")
            return {"continue": True}  # Tempo disabled - allow exit

        # Debug log
        log_debug("hook", "=== INTENT VALIDATION (Refactored) ===")

        # Use intent validator to check if work is complete
        from . import intent_validator

        conversation_context_size = config.get("conversation_context_size", 5)

        log_debug(
            "hook",
            f"Calling intent validator (context_size={conversation_context_size})...",
        )

        result = intent_validator.validate_intent(
            session_id=session_id,
            transcript_path=transcript_path,
            conversation_context_size=conversation_context_size,
        )

        log_debug("hook", f"Intent validation result: {result}")

        # AC5: Record blockage for tempo validation failure
        if result.get("decision") == "block":
            record_blockage(
                db_path=DEFAULT_DB_PATH,
                category="pacing_tempo",
                reason=result.get("reason", "Work appears incomplete"),
                hook_type="stop",
                session_id=session_id or "unknown",
                details=None,
            )

        # Return validation result
        return result

    except Exception as e:
        # Graceful degradation - log error and allow exit
        print(f"[PACE-MAKER ERROR] Stop hook: {e}", file=sys.stderr)
        return {"continue": True}


def run_pre_tool_hook() -> Dict[str, Any]:
    """
    Pre-tool hook: Unified validation (intent + code review).

    Validates BEFORE tool execution:
    1. Intent was declared in last 2 messages
    2. Proposed code matches declared intent exactly
    3. No clean code violations

    Debug note: Comprehensive logging captures all hook_data fields and CLAUDE_* environment variables.

    Returns:
        {"continue": True} to allow, or
        {"decision": "block", "reason": "..."} to block
    """
    try:
        # 1. Read hook data from stdin
        raw_input = sys.stdin.read()
        if not raw_input:
            return {"continue": True}

        hook_data = json.loads(raw_input)

        # DEBUG: Log all available hook_data fields
        log_debug("hook", f"Pre-tool hook_data keys: {list(hook_data.keys())}")

        # Log ALL hook_data values
        for key in hook_data.keys():
            if key == "tool_input":
                # Log tool_input keys only (not full content)
                tool_input = hook_data[key]
                if isinstance(tool_input, dict):
                    log_debug(
                        "hook",
                        f"hook_data['tool_input'] keys: {list(tool_input.keys())}",
                    )
                    # Log non-content fields
                    for k, v in tool_input.items():
                        if k not in [
                            "content",
                            "old_string",
                            "new_string",
                        ]:  # Skip large code fields
                            log_debug("hook", f"  tool_input['{k}']: {v}")
            else:
                value_str = str(hook_data[key])
                if len(value_str) > 300:
                    value_str = value_str[:300] + "..."
                log_debug("hook", f"hook_data['{key}']: {value_str}")

        # 2. Extract fields
        tool_name = hook_data.get("tool_name")
        tool_input = hook_data.get("tool_input", {})
        file_path = tool_input.get("file_path")
        session_id = hook_data.get("session_id")

        # Derive transcript path from session_id (Claude Code doesn't send it)
        transcript_path = get_transcript_path(session_id) if session_id else None

        log_debug("hook", f"session_id: {session_id}")
        log_debug("hook", f"transcript_path: {transcript_path}")

        # Check environment variables
        import os as os_module

        log_debug("hook", f"CWD: {os_module.getcwd()}")
        log_debug(
            "hook", f"CLAUDE_AGENT_ID: {os_module.environ.get('CLAUDE_AGENT_ID')}"
        )
        log_debug(
            "hook",
            f"All CLAUDE_ env vars: {[k for k in os_module.environ.keys() if 'CLAUDE' in k.upper()]}",
        )

        # Check if we're in a subagent context by matching tool_use_id
        tool_use_id = hook_data.get("tool_use_id")
        if transcript_path and "/agent-" not in transcript_path and tool_use_id:
            # Main context transcript - search for agent transcript with this tool_use_id
            import glob

            projects_dir = os.path.dirname(transcript_path)
            agent_transcripts = glob.glob(os.path.join(projects_dir, "agent-*.jsonl"))
            # Also search new Claude Code 2.1.39+ nested structure: <session_id>/subagents/agent-*.jsonl
            if session_id:
                agent_transcripts += glob.glob(
                    os.path.join(projects_dir, session_id, "subagents", "agent-*.jsonl")
                )

            # Filter to only recently modified agent transcripts (last 30 seconds)
            recent_agents = [
                f for f in agent_transcripts if time.time() - os.path.getmtime(f) < 30
            ]

            log_debug(
                "hook",
                f"Searching {len(recent_agents)} recent agent transcripts for tool_use_id: {tool_use_id}",
            )

            # Search for the agent transcript containing this tool_use_id
            for agent_path in recent_agents:
                try:
                    with open(agent_path, "r") as f:
                        # Search from end of file (most recent entries)
                        for line in f:
                            if tool_use_id in line:
                                log_debug(
                                    "hook",
                                    f"Found tool_use_id in subagent transcript: {agent_path}",
                                )
                                transcript_path = agent_path
                                break
                    if transcript_path == agent_path:
                        break  # Found it, stop searching
                except Exception as e:
                    log_debug("hook", f"Error searching {agent_path}: {e}")

        # 2a. Only validate Write/Edit tools
        if tool_name not in ["Write", "Edit"]:
            return {"continue": True}

        # Skip if no file_path (e.g., some edge cases)
        if not file_path:
            return {"continue": True}

        # 3. Load config
        config = load_config(DEFAULT_CONFIG_PATH)

        # Master switch - all features disabled
        if not config.get("enabled", True):
            return {"continue": True}

        # Check if feature enabled
        if not config.get("intent_validation_enabled", False):
            return {"continue": True}

        # 4. Check if source code file
        from . import extension_registry

        extensions = extension_registry.load_extensions(DEFAULT_EXTENSION_REGISTRY_PATH)
        is_source = extension_registry.is_source_code_file(file_path, extensions)

        if not is_source:
            return {"continue": True}  # Bypass non-source files

        # 5. Extract proposed code from tool_input
        if tool_name == "Write":
            proposed_code = tool_input.get("content", "")
        elif tool_name == "Edit":
            # For Edit, use new_string as the proposed code
            proposed_code = tool_input.get("new_string", "")
        else:
            return {"continue": True}

        # 6. Read last 2 messages for validation (text + tool_use are separate entries)
        messages = get_last_n_messages_for_validation(transcript_path, n=2)

        # 7. Call unified validation via SDK
        from . import intent_validator

        result = intent_validator.validate_intent_and_code(
            messages=messages,
            code=proposed_code,
            file_path=file_path,
            tool_name=tool_name,
        )

        # 8. Return result
        if result.get("approved", False):
            return {"continue": True}
        else:
            # AC4: Record blockage for intent validation failure
            # Determine category based on failure type
            if result.get("tdd_failure", False):
                category = "intent_validation_tdd"
            elif result.get("clean_code_failure", False):
                category = "intent_validation_cleancode"
            else:
                category = "intent_validation"

            record_blockage(
                db_path=DEFAULT_DB_PATH,
                category=category,
                reason=result.get("feedback", "Validation failed"),
                hook_type="pre_tool_use",
                session_id=session_id or "unknown",
                details={"tool": tool_name, "file_path": file_path},
            )

            return {
                "decision": "block",
                "reason": result.get("feedback", "Validation failed"),
            }

    except Exception as e:
        # Graceful degradation - log error and allow
        print(f"[PACE-MAKER ERROR] Pre-tool hook: {e}", file=sys.stderr)
        return {"continue": True}


def main():
    """Entry point for hook script."""
    # Check if this is pre_tool_use hook
    if len(sys.argv) > 1 and sys.argv[1] == "pre_tool_use":
        result = run_pre_tool_hook()
        safe_print(json.dumps(result), file=sys.stdout)

        if result.get("decision") == "block":
            sys.exit(2)  # Block tool use
        else:
            sys.exit(0)  # Allow tool use

    # Check if this is stop hook
    if len(sys.argv) > 1 and sys.argv[1] == "stop":
        result = run_stop_hook()
        # Output JSON response
        safe_print(json.dumps(result), file=sys.stdout)

        # ========================================================================
        # CRITICAL: Exit code determines Claude Code behavior
        # ========================================================================
        # Exit code 2: Signals blocking error - Claude Code will show the
        #              "reason" message to Claude and force it to continue
        #              responding. Used with {"decision": "block", "reason": "..."}
        #
        # Exit code 0: Signals success - Claude Code allows normal exit.
        #              Used with {"continue": true}
        #
        # DO NOT change these exit codes or the hook will not work correctly.
        # ========================================================================
        if result.get("decision") == "block":
            sys.exit(2)  # Force continuation by signaling blocking error
        else:
            sys.exit(0)  # Allow normal exit

    # Check if this is user-prompt-submit hook
    if len(sys.argv) > 1 and sys.argv[1] == "user_prompt_submit":
        run_user_prompt_submit()
        return

    # Check if this is session-start hook
    if len(sys.argv) > 1 and sys.argv[1] == "session_start":
        try:
            run_session_start_hook()
        except Exception as e:
            print(f"[PACE-MAKER ERROR] SessionStart: {e}", file=sys.stderr)
        return

    # Check if this is subagent-start hook
    if len(sys.argv) > 1 and sys.argv[1] == "subagent_start":
        try:
            run_subagent_start_hook()
        except Exception as e:
            print(f"[PACE-MAKER ERROR] SubagentStart: {e}", file=sys.stderr)
        return

    # Check if this is subagent-stop hook
    if len(sys.argv) > 1 and sys.argv[1] == "subagent_stop":
        try:
            run_subagent_stop_hook()
        except Exception as e:
            print(f"[PACE-MAKER ERROR] SubagentStop: {e}", file=sys.stderr)
        return

    # Check if this is post-tool-use hook (explicit handling for clarity)
    if len(sys.argv) > 1 and sys.argv[1] == "post_tool_use":
        try:
            feedback_provided = run_hook()
            if feedback_provided:
                sys.exit(2)  # Show feedback to Claude
        except Exception as e:
            # Graceful degradation - log error but don't crash
            print(f"[PACE-MAKER ERROR] {e}", file=sys.stderr)
            # Continue execution without throttling
        return

    # Default fallback: treat as post-tool-use hook
    try:
        run_hook()
    except Exception as e:
        # Graceful degradation - log error but don't crash
        print(f"[PACE-MAKER ERROR] {e}", file=sys.stderr)
        # Continue execution without throttling


if __name__ == "__main__":
    main()
