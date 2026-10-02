#!/usr/bin/env python3
"""
Post-tool-use hook entry point for Credit-Aware Adaptive Throttling.

This module is called by the Claude Code post-tool-use hook.
It runs pacing checks and applies adaptive throttling.
"""

import os
import sys
import time
import traceback
from datetime import datetime, timezone
import json
from typing import List, Optional, Dict, Any

from . import pacing_engine, database, user_commands
from .database import (
    record_blockage,
    record_activity_event,
    cleanup_old_activity,
    record_governance_event,
    cleanup_old_governance_events,
)
from .constants import (
    DEFAULT_CONFIG,
    DEFAULT_DB_PATH,
    DEFAULT_CONFIG_PATH,
    DEFAULT_STATE_PATH,
    DEFAULT_EXTENSION_REGISTRY_PATH,
    DEFAULT_DANGER_RULES_PATH,
    MAX_DELAY_SECONDS,
    PRE_TOOL_ANCHOR_CAP_SECONDS,
    PRE_TOOL_HOOK_TIMEOUT_SECONDS,
    PRE_TOOL_SAFETY_MARGIN_SECONDS,
)
from .transcript_reader import (
    get_last_n_messages_for_validation,
    get_current_turn_message_for_validation,
)
from .atomic_file import atomic_write_text
from .bounded_call import run_with_deadline
from .intent_declarations import gate as declaration_gate
from .intent_declarations import subagent_guidance
from .logger import log_warning, log_debug, log_info, log_error
from .prompt_provenance import format_tag, format_reviewer_relay

# Guards one-time schema migration for codex_usage table in SubagentStop handler.
_codex_migration_done: bool = False

# Issue #93: the danger-bash gate's tool-matched-anchor wait ceiling,
# DELIBERATELY much smaller than the Write/Edit gate's unmodified 30s
# default (transcript_reader.get_current_turn_message_for_validation's
# _max_wait_seconds). Live evidence (116 race-path danger-bash blocks
# recorded in usage.db) showed every observed failure burned the FULL 30s
# ceiling and never recovered within it -- the true current turn was never
# flushed anywhere inside the hook's entire execution window, so the long
# wait bought nothing but latency before the inevitable block. Combined
# with the gate now ACCEPTING a stale byte-identical re-issue's own INTENT
# (see the "found"/"stale"/"not_found" branch below) instead of discarding
# the anchor, the effective recovery path is no longer "wait longer" but
# "block fast, let the agent re-issue, accept the re-issue's own turn (or
# its now-stale predecessor) on the next attempt" -- so a short ceiling is
# strictly better than a long one here. 3.0s keeps a handful of real
# exponential-backoff retries (0.25, 0.5, 1.0, 1.25(clamped) ~= 5 attempts
# with the default 0.25s/2x/2.0s-cap schedule) in case the turn flushes
# within a couple seconds, while capping the worst-case latency per attempt
# far below the old 30s. The Write/Edit gate's not_found ceiling is a
# SEPARATE concern (still the function's 30s default, clamped further by
# PRE_TOOL_ANCHOR_CAP_SECONDS/the gate deadline -- issue #108) -- it now
# also consumes a "stale" outcome (issue #139), just via its own
# _WRITE_EDIT_STALE_GRACE_SECONDS below rather than this constant.
_DANGER_BASH_MAX_WAIT_SECONDS = 3.0

# Issue #139 finding #4: a "found" result still wins over "stale" at any
# point before this grace window elapses -- the retry loop returns
# immediately the moment ANY attempt classifies as "found" (see the loop in
# transcript_reader.get_current_turn_message_for_validation), regardless of
# an earlier "stale" hit. Only once a "stale" classification has PERSISTED
# for at least this many real seconds -- i.e. no "found" superseded it in
# that window -- does the gate stop waiting and accept the stale match
# early (see the "stale" branch a few hundred lines below, which then
# accepts it unconditionally). There is no correctness reason to keep
# paying the full not_found ceiling once that grace has passed -- this only
# shortens the LATENCY of the eventual accept, exactly like
# _DANGER_BASH_MAX_WAIT_SECONDS shortens the not_found path above, but
# scoped to "stale" only (does NOT lower the not_found ceiling itself).
_WRITE_EDIT_STALE_GRACE_SECONDS = 3.0

# Bug #157 (fix 4, defence in depth): how long SubagentStart / SubagentStop may
# spend on their Langfuse step, measured from the start of the hook function.
# Both hooks run under a 10 s harness timeout, and a cancelled SubagentStart
# delivers NOTHING to the subagent. The Langfuse step does network I/O whose own
# timeouts (10 s trace push, 3 s OAuth profile) exceed the budget by themselves,
# so it runs through bounded_call.run_with_deadline. 5 s leaves ~5 s for what the
# budget does not cover: the shell wrapper's find_python (~0.7 s, imports
# claude_agent_sdk), interpreter + module imports (~0.5 s), state/CSA work and
# the output itself. After the fix the whole hook measures ~1 s without load.
SUBAGENT_HOOK_LANGFUSE_BUDGET_SECONDS = 5.0
# Floor so a slow earlier step never reduces the Langfuse wait to "none at all".
_SUBAGENT_HOOK_LANGFUSE_MIN_WAIT_SECONDS = 0.25


def _langfuse_wait_budget(hook_started: float) -> float:
    """Seconds left for the bounded Langfuse step of a subagent hook."""
    remaining = SUBAGENT_HOOK_LANGFUSE_BUDGET_SECONDS - (
        time.monotonic() - hook_started
    )
    return max(_SUBAGENT_HOOK_LANGFUSE_MIN_WAIT_SECONDS, remaining)


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
                if data.get("last_cleanup_time"):
                    data["last_cleanup_time"] = datetime.fromisoformat(
                        data["last_cleanup_time"]
                    ).replace(tzinfo=timezone.utc)
                if data.get("last_user_interaction_time"):
                    data["last_user_interaction_time"] = datetime.fromisoformat(
                        data["last_user_interaction_time"]
                    ).replace(tzinfo=timezone.utc)

                # Merge with defaults to ensure all required fields exist
                # Loaded data takes precedence, defaults fill in missing fields
                state = {**default_state, **data}
                state.pop(
                    "last_poll_time", None
                )  # Migrated to global_poll_state SQLite (Story #43)
                return state
    except Exception as e:
        log_warning("hook", "Failed to load state, using defaults", e)

    # File doesn't exist or failed to load - return defaults
    return default_state


def save_state(state: dict, state_path: str = DEFAULT_STATE_PATH):
    """Save hook state for next invocation.

    Bug #161: state.json is shared by every concurrent hook process, so the
    write is atomic (temp file + os.replace) -- a reader never sees an empty or
    half-written file. The state is serialized BEFORE the disk is touched, so a
    non-serializable value leaves the previous file intact.
    """
    try:
        # Convert datetime to string for JSON serialization
        state_copy = state.copy()
        if isinstance(state_copy.get("last_cleanup_time"), datetime):
            state_copy["last_cleanup_time"] = state_copy[
                "last_cleanup_time"
            ].isoformat()
        if isinstance(state_copy.get("last_user_interaction_time"), datetime):
            state_copy["last_user_interaction_time"] = state_copy[
                "last_user_interaction_time"
            ].isoformat()

        atomic_write_text(state_path, json.dumps(state_copy))
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


def display_intent_validation_guidance(config: Optional[dict] = None) -> str:
    """
    Load intent validation guidance from external file.

    Shared helper used by both SessionStart and SubagentStart hooks.
    Shows requirements for intent declaration and TDD enforcement.

    Story #155: when the declare_intent tool path is enabled, the guidance
    LEADS with the pilot-validated "call the declare_intent tool first"
    paragraph (prompts/session_start/declare_intent_guidance.md); the #150
    "C text" and every visible-INTENT: instruction follow immediately as the
    fallback, unchanged. With the kill switch off the output is
    byte-identical to pre-#155.

    Args:
        config: the caller's already-loaded config. ``None`` means the
            shipped defaults (tool path enabled) -- deliberately NOT a
            hidden read of the user's real config file.

    Returns:
        String containing the guidance text loaded from external file
    """
    from .intent_declarations.gate import intent_declaration_tool_enabled
    from .prompt_loader import PromptLoader

    loader = PromptLoader()
    guidance = loader.load_prompt(
        "intent_validation_guidance.md", subfolder="session_start"
    )
    if intent_declaration_tool_enabled(config or {}):
        declare_paragraph = loader.load_prompt(
            "declare_intent_guidance.md", subfolder="session_start"
        )
        guidance = f"{declare_paragraph.strip()}\n\n{guidance}"
    # format_tag is imported at module top: `from .prompt_provenance import
    # format_tag, format_reviewer_relay` (already used elsewhere in this
    # file, e.g. _fail_closed_message() above).
    return format_tag(guidance, "intent_validation_guidance")


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
        f'   You MUST use model: "{preferred_model}" for ALL Agent tool subagent calls.'
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
        return format_tag(message.strip(), "secrets_nudge")
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
        state.setdefault("last_user_interaction_time_by_session", {}).pop(
            session_id or "", None
        )
        state["tool_execution_count"] = 0
    elif source == "resume":
        # RESUME existing session - Update session_id but preserve counters
        if session_id:
            state["session_id"] = session_id
        # Keep: last_user_interaction_time, tool_execution_count
    elif source in ("clear", "compact"):
        # CLEAR/COMPACT - Reset counters but keep session_id
        # (session_id from stdin should match existing session_id)
        state["last_user_interaction_time"] = None
        state.setdefault("last_user_interaction_time_by_session", {}).pop(
            session_id or "", None
        )
        state["tool_execution_count"] = 0
        # Keep: session_id (same session continues)

    # Save state
    save_state(state, DEFAULT_STATE_PATH)

    # Minimum Claude Code version check (Story #66 / issue #96). Must run
    # after the first save_state (session_id/counter resets already on
    # disk) and before Cross-Session Awareness, per the documented ordering.
    # perform_session_start_version_check() already fails open internally on
    # any probe/parse failure; this try/except is defense-in-depth only,
    # mirroring the CSA block immediately below.
    try:
        from .version_check import perform_session_start_version_check

        perform_session_start_version_check(state, config, stderr=sys.stderr)
        save_state(state, DEFAULT_STATE_PATH)
    except Exception as e:
        log_warning("hook", f"Version check failed: {e}")

    # Version-block user-visible signal (issue #100). SessionStart cannot be
    # blocked via a non-zero exit code (Claude Code hooks reference: exit
    # code 2 is not a documented blocking case for SessionStart), and a
    # hook that exits 0 has its stderr surfaced only in transcript/debug
    # mode — so plain stdout (treated as additionalContext for
    # SessionStart, same as the intent-validation guidance printed further
    # below in this function) is the only channel that reliably reaches
    # Claude/the user. Purely informational: if version_block_active is
    # unset/False, or the message is missing (fail-open path already
    # cleared it to None), nothing is printed and nothing can block.
    try:
        if state.get("version_block_active") and state.get("version_block_message"):
            from .prompt_provenance import format_tag

            safe_print(
                format_tag(state["version_block_message"], "version_block_notice"),
                file=sys.stdout,
            )
    except Exception as e:
        log_warning("hook", f"Failed to display version block notice: {e}")

    # Cross-Session Awareness: register session and emit sibling banner if any.
    # csa.on_session_start() mutates state to cache workspace_root, so we save
    # state again after the call. init_schema is idempotent and ensures the
    # registry DB file exists before the first CSA operation.
    # os.getcwd() and os.getpid() are recomputed here because hook_data does
    # not expose cwd directly and no pre-existing local variable holds them.
    #
    # Issue #105: init_schema() (and everything else in this block) must be
    # gated behind `cross_session_awareness_enabled`, not just the master
    # `enabled` switch checked earlier in this function. Without this check,
    # init_schema() previously ran unconditionally and created an empty
    # session_registry.db file/schema even when a user explicitly disabled
    # CSA. csa_on_session_start() itself already checks _is_enabled(config)
    # internally and no-ops when disabled — this check mirrors that same
    # canonical flag semantic so the DB file is never touched in the first
    # place, rather than being created and then immediately unused.
    try:
        from .session_registry._csa import (
            _is_enabled as _csa_is_enabled,
            on_session_start as csa_on_session_start,
        )
        from .session_registry.db import resolve_db_path, init_schema

        if _csa_is_enabled(config):
            csa_db_path = resolve_db_path()
            init_schema(csa_db_path)
            csa_banner = csa_on_session_start(
                session_id=session_id or "",
                source=source,
                cwd=os.getcwd(),
                pid=os.getpid(),
                db_path=csa_db_path,
                state=state,
                config=config,
            )
            # Persist state mutation from csa.on_session_start (workspace_root cache)
            save_state(state, DEFAULT_STATE_PATH)
            if csa_banner:
                safe_print(csa_banner, file=sys.stdout)
    except Exception as e:
        log_warning("hook", f"CSA session_start failed: {e}")

    # Memory localization (story #65) — auto-link central memory folder to
    # repo-local .claude-memory/ when present. Safe no-op for non-git repos or
    # repos without .claude-memory/.
    if source in ("startup", "resume"):
        try:
            from .memory_localization.core import link_if_local_exists

            ml_cwd = hook_data.get("cwd") if hook_data else None
            ml_transcript = hook_data.get("transcript_path") if hook_data else None
            if ml_cwd and ml_transcript:
                ml_status, ml_target = link_if_local_exists(
                    ml_cwd, ml_transcript, config
                )
                if ml_status in (
                    "linked_fresh",
                    "replaced_with_symlink",
                    "relinked",
                    "already_linked",
                    "raced_but_ok",
                ):
                    ml_nudge = (
                        f"**Memory localization active**\n"
                        f"Your project memory is stored in {ml_target} "
                        f"(git-tracked), symlinked from the central Claude memory folder. "
                        f"Edits to memory files will appear in `git status` and can be "
                        f"committed to share across clones."
                    )
                    _session_start_additional_context = {
                        "hookSpecificOutput": {
                            "hookEventName": "SessionStart",
                            "additionalContext": ml_nudge,
                        }
                    }
                    safe_print(
                        json.dumps(_session_start_additional_context),
                        file=sys.stdout,
                    )
        except Exception as e:
            log_warning("memory_localization", f"SessionStart link failed: {e}")

    # Activity event: SE (session started)
    try:
        record_activity_event(
            DEFAULT_DB_PATH, "SE", "green", state.get("session_id", "unknown")
        )
    except Exception:
        pass  # Activity recording must never break session start

    # Cleanup old activity events to prevent unbounded table growth
    try:
        cleanup_old_activity(DEFAULT_DB_PATH, max_age_seconds=60)
    except Exception:
        pass  # Cleanup must never break session start

    # Cleanup old governance events (24h retention)
    try:
        cleanup_old_governance_events(DEFAULT_DB_PATH, max_age_seconds=86400)
    except Exception:
        pass  # Cleanup must never break session start

    # AC3: Cleanup stale Langfuse state files (>7 days old)
    try:
        from .langfuse import state as langfuse_state

        langfuse_state_dir = os.path.expanduser("~/.claude-pace-maker/langfuse_state")
        state_manager = langfuse_state.StateManager(langfuse_state_dir)
        # Bug #160: throttled to once a day; subagent-*.json get a short TTL
        state_manager.maybe_cleanup_stale_files(max_age_days=7)
    except Exception as e:
        # Log error but don't break session start
        log_warning("hook", "Failed to cleanup stale Langfuse state files", e)

    # Display intent validation mandate if enabled
    try:
        if config.get("intent_validation_enabled", False):
            guidance = display_intent_validation_guidance(config)
            safe_print(guidance, file=sys.stdout)
    except Exception as e:
        # Log error but don't break session start
        print(
            f"[PACE-MAKER WARNING] Failed to display intent guidance: {e}",
            file=sys.stderr,
        )

    # Story #101: SessionStart provenance manifest — declares the closed
    # channel enumeration + never-list so Claude can check any tagged
    # pace-maker text (this session or any other) against a declared
    # contract. Always emitted (independent of intent_validation_enabled),
    # since the manifest covers every pace-maker channel, not just intent
    # validation.
    try:
        from .prompt_provenance import session_start_manifest

        safe_print(session_start_manifest(), file=sys.stdout)
    except Exception as e:
        # Log error but don't break session start
        log_warning("hook", "Failed to display session_start_manifest", e)

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
    # Bug #157: the hook's own clock, for the bounded Langfuse step below.
    hook_started = time.monotonic()

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

    # Early CSA agent registration — must run BEFORE Langfuse (which can timeout).
    # Issue #99: the gate is enforced INSIDE on_subagent_start_register() via
    # _is_enabled(config) (checks BOTH `enabled` and
    # `cross_session_awareness_enabled`) — do NOT re-add an inline
    # `cross_session_awareness_enabled`-only check here; that duplication
    # diverging from _is_enabled() is exactly what caused this bug.
    try:
        if hook_data:
            from .session_registry._csa import (
                on_subagent_start_register as _csa_on_subagent_start_register,
            )
            from .session_registry.db import resolve_db_path as _early_resolve_db

            _session_id = hook_data.get("session_id", "")
            _agent_id = hook_data.get("agent_id", "")
            _csa_state = load_state(DEFAULT_STATE_PATH)
            _csa_ns = _csa_state.get("cross_session_awareness", {})
            _cs = _csa_ns.get(_session_id, {})
            _ws = _cs.get("workspace_root", "")
            if _session_id and _agent_id and _ws:
                _csa_on_subagent_start_register(
                    session_id=_session_id,
                    agent_id=_agent_id,
                    workspace_root=_ws,
                    db_path=_early_resolve_db(),
                    config=config,
                    subagent_type=hook_data.get("agent_type"),
                )
    except Exception as e:
        log_warning("hook", f"Early CSA agent registration failed: {e}")

    # AC3: Create Langfuse trace for subagent if hook data available
    if hook_data:
        log_debug("hook", f"SubagentStart hook_data keys: {list(hook_data.keys())}")
        log_debug("hook", f"SubagentStart hook_data: {hook_data}")
        # Bug #157: bounded -- a slow/unreachable Langfuse must never cost the
        # subagent its guidance (the output below does not depend on this step).
        _lf_finished, subagent_trace_id = run_with_deadline(
            _handle_langfuse_subagent_start,
            _langfuse_wait_budget(hook_started),
            hook_data,
            config,
        )
        if not _lf_finished:
            log_warning(
                "hook",
                "SubagentStart: Langfuse step exceeded its "
                f"{SUBAGENT_HOOK_LANGFUSE_BUDGET_SECONDS}s budget -- continuing "
                "without a subagent trace (guidance is still delivered)",
            )

        # Bug #161: PostToolUse (#158) and SubagentStop both resolve the
        # subagent's trace from its OWN langfuse_state/subagent-<agent_id>.json,
        # written by the Langfuse step above. The shared state.json no longer
        # carries a per-agent trace map (any concurrent hook could clobber it).
        if subagent_trace_id:
            agent_id = hook_data.get("agent_id")
            parent_transcript_path = hook_data.get("transcript_path", "")

            # `state` was loaded before the (up to several seconds) Langfuse
            # step: re-load it so changes other hooks made meanwhile survive.
            state = load_state(DEFAULT_STATE_PATH)

            # Legacy single slot: a SubagentStop fallback for an agent with no
            # state file of its own. It is global across sessions, so SubagentStop
            # only claims it for a payload whose session owns the trace.
            # (in_subagent is derived from subagent_counter, not from this slot.)
            state["current_subagent_trace_id"] = subagent_trace_id
            state["current_subagent_agent_id"] = agent_id
            state["current_subagent_parent_transcript_path"] = parent_transcript_path

            save_state(state, DEFAULT_STATE_PATH)
            log_debug(
                "hook",
                f"SubagentStart: Stored subagent trace_id={subagent_trace_id} for "
                f"agent_id={agent_id} in the legacy state.json slot "
                "(per-agent state file holds the authoritative copy)",
            )
    else:
        log_debug("hook", "SubagentStart: No hook_data received from stdin")

    # Activity event: SA (subagent started)
    try:
        _session_id = hook_data.get("session_id", "unknown") if hook_data else "unknown"
        record_activity_event(DEFAULT_DB_PATH, "SA", "green", _session_id)
    except Exception:
        pass  # Activity recording must never break subagent start

    def _emit_subagent_additional_context(text: str) -> None:
        """Emit hookSpecificOutput.additionalContext JSON for SubagentStart."""
        output = {
            "hookSpecificOutput": {
                "hookEventName": "SubagentStart",
                "additionalContext": text,
            }
        }
        safe_print(json.dumps(output), file=sys.stdout)

    # Collect context parts from intent validation and CSA, then emit exactly once.
    # The manifest + intent guidance come from the SAME builder PostToolUse's
    # late delivery uses (bug #157), so both channels deliver identical text.
    _additional_context_parts: list = _subagent_guidance_parts(config)

    # Cross-Session Awareness: inject sibling banner for new subagent if siblings present.
    # State is reloaded here to get workspace_root cached by SessionStart.
    # No save_state after on_subagent_start: seen_agent_ids/counter mutations are
    # acceptable as in-memory-only since SubagentStart fires per subagent launch.
    try:
        if hook_data:
            from .session_registry._csa import (
                on_subagent_start as csa_on_subagent_start,
            )
            from .session_registry.db import resolve_db_path

            csa_state = load_state(DEFAULT_STATE_PATH)
            csa_banner = csa_on_subagent_start(
                session_id=hook_data.get("session_id", ""),
                agent_id=hook_data.get("agent_id", ""),
                pid=os.getpid(),
                db_path=resolve_db_path(),
                state=csa_state,
                config=config,
                subagent_type=hook_data.get("agent_type"),
            )
            if csa_banner:
                _additional_context_parts.append(csa_banner)
    except Exception as e:
        log_warning("hook", f"CSA subagent_start failed: {e}")

    # Emit exactly one hookSpecificOutput if any context was collected.
    if _additional_context_parts:
        _emit_subagent_additional_context("\n\n".join(_additional_context_parts))

    # Bug #157 -- VERY LAST step, after the output above was written: record
    # that SubagentStart completed for (session_id, agent_id). A subagent whose
    # SubagentStart was cancelled never reaches this line, so its first
    # PostToolUse delivers the guidance late (see run_hook). Never raises.
    try:
        if hook_data:
            subagent_guidance.record_start_completed(hook_data)
    except Exception as e:
        log_warning("hook", f"Could not record SubagentStart completion: {e}")


def _subagent_guidance_parts(config: dict) -> list:
    """The pace-maker context a subagent must receive: the abbreviated
    provenance manifest (ALWAYS -- subagents have zero session history, so
    independent of intent_validation_enabled; Story #101) and, when
    ``intent_validation_enabled``, the intent-validation guidance. Shared by
    SubagentStart and PostToolUse's late delivery (bug #157) so both deliver
    identical text. Each part fails independently and never raises."""
    parts: list = []
    try:
        from .prompt_provenance import subagent_start_manifest

        parts.append(subagent_start_manifest())
    except Exception as e:
        log_warning("hook", f"Failed to build subagent_start_manifest: {e}")

    # Display intent validation mandate if enabled
    try:
        if config.get("intent_validation_enabled", False):
            parts.append(display_intent_validation_guidance(config))
    except Exception as e:
        # Log error but don't break subagent start
        print(
            f"[PACE-MAKER WARNING] Failed to display intent guidance: {e}",
            file=sys.stderr,
        )
    return parts


def _prepare_late_subagent_guidance(hook_data: dict, config: dict) -> Optional[str]:
    """Bug #157: the guidance text a subagent's PostToolUse MAY have to inject,
    or None. Cheap on the common path (every subagent tool call after the
    first): ``agent_id`` is checked first, then a read-only "already completed
    or delivered?" check, and only when delivery is actually still needed is
    the text built. Nothing is claimed here -- the atomic claim happens
    IMMEDIATELY BEFORE the output is printed (see ``run_hook``), so a pacing
    delay or a slow Langfuse push between the two can never spend the one
    delivery without the output going out."""
    if not hook_data.get("agent_id"):
        return None
    try:
        if not subagent_guidance.needs_late_guidance(hook_data):
            return None
        parts = _subagent_guidance_parts(config)
        return "\n\n".join(parts) if parts else None
    except Exception as e:
        log_warning("hook", f"Late subagent guidance preparation failed: {e}")
        return None


def run_subagent_stop_hook():
    """
    Handle SubagentStop hook - exiting subagent context.

    AC5: Finalizes subagent span by flushing remaining transcript lines
    Decrements subagent_counter and sets in_subagent flag based on counter.
    Does NOT reset tool_execution_count (global counter persists).
    """
    # Bug #157: the hook's own clock, for the bounded Langfuse step below.
    hook_started = time.monotonic()

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

    # Cross-Session Awareness: heartbeat on subagent stop to keep session visible to siblings.
    # Called unconditionally inside try/except; CSA validates inputs and fails-open.
    try:
        from .session_registry._csa import on_heartbeat as csa_on_heartbeat
        from .session_registry._csa import on_subagent_stop as csa_on_subagent_stop
        from .session_registry.db import resolve_db_path

        _csa_substop_state = load_state(DEFAULT_STATE_PATH)
        _csa_substop_db_path = resolve_db_path()
        csa_on_heartbeat(
            session_id=(hook_data or {}).get("session_id", ""),
            pid=os.getpid(),
            db_path=_csa_substop_db_path,
            state=_csa_substop_state,
            config=config,
        )
        csa_on_subagent_stop(
            session_id=(hook_data or {}).get("session_id", ""),
            agent_id=(hook_data or {}).get("agent_id", ""),
            db_path=_csa_substop_db_path,
            state=_csa_substop_state,
            config=config,
        )
    except Exception as e:
        log_warning("hook", f"CSA subagent_stop heartbeat failed: {e}")

    # Activity event: SA (subagent stopped)
    try:
        _sa_session_id = (
            hook_data.get("session_id", "unknown") if hook_data else "unknown"
        )
        record_activity_event(DEFAULT_DB_PATH, "SA", "blue", _sa_session_id)
    except Exception:
        pass  # Activity recording must never break subagent stop

    # AC5: Subagent trace finalization
    # The payload's identity (agent_id + session_id) decides which trace this is
    hook_agent_id = hook_data.get("agent_id") if hook_data else None
    payload_session_id = hook_data.get("session_id") if hook_data else None

    # Bug #161: the trace comes from THIS agent's own state file, keyed by the
    # payload's agent_id -- never from the shared state.json, which any
    # concurrent hook can clobber. The trace must belong to the payload's session.
    trace_info = None
    if hook_agent_id:
        from .langfuse import state as langfuse_state

        trace_info = langfuse_state.read_subagent_trace_info(
            os.path.expanduser("~/.claude-pace-maker/langfuse_state"),
            hook_agent_id,
            payload_session_id,
        )

    # Backward compatibility: the legacy single `current_subagent_*` slot in
    # state.json, for an agent without a state file of its own
    if not trace_info:
        old_trace_id = state.get("current_subagent_trace_id")
        old_agent_id = state.get("current_subagent_agent_id")
        old_parent_path = state.get("current_subagent_parent_transcript_path")
        # Bug #158/#161: the single slot is global across sessions, so ONE rule
        # decides whether it stands in for this payload: its trace must belong
        # to the payload's OWN session (trace ids start with
        # "<parent_session_id>-subagent-"), and when the payload names an
        # agent, it must be the slot's agent too.
        slot_is_ours = bool(
            payload_session_id
            and isinstance(old_trace_id, str)
            and old_trace_id.startswith(f"{payload_session_id}-subagent-")
            and (not hook_agent_id or old_agent_id == hook_agent_id)
        )
        if slot_is_ours:
            trace_info = {
                "trace_id": old_trace_id,
                "parent_transcript_path": old_parent_path,
            }
            hook_agent_id = old_agent_id

    if trace_info and config.get("langfuse_enabled", False):
        subagent_trace_id = trace_info.get("trace_id")
        parent_transcript_path = trace_info.get("parent_transcript_path")

        def _finalize_langfuse_trace() -> None:
            # Bug #157: this whole Langfuse step (trace finalize + parent
            # pending-trace flush) is network I/O; it runs through
            # run_with_deadline below so the hook always returns in time.
            nonlocal parent_transcript_path
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

        try:
            _lf_finished, _ = run_with_deadline(
                _finalize_langfuse_trace, _langfuse_wait_budget(hook_started)
            )
            if not _lf_finished:
                log_warning(
                    "hook",
                    f"SubagentStop: Langfuse finalize of {subagent_trace_id} "
                    f"exceeded its {SUBAGENT_HOOK_LANGFUSE_BUDGET_SECONDS}s "
                    "budget -- continuing (trace left unfinalized)",
                )
        except Exception as e:
            # Graceful failure - log but don't break hook
            log_warning(
                "hook", f"SubagentStop: Failed to finalize trace {subagent_trace_id}", e
            )

        # Bug #161: `state` was loaded before the (multi-second) Langfuse step
        # above. Re-load it so another hook's changes made in the meantime are
        # not overwritten by that stale copy. `subagent_traces` is the retired
        # pre-#161 map (finalization no longer reads it); drop any leftover.
        state = load_state(DEFAULT_STATE_PATH)
        state.pop("subagent_traces", None)

        # Clear old backward-compat keys
        state.pop("current_subagent_trace_id", None)
        state.pop("current_subagent_agent_id", None)
        state.pop("current_subagent_parent_transcript_path", None)
        save_state(state, DEFAULT_STATE_PATH)
    elif not trace_info:
        log_debug("hook", "SubagentStop: No subagent trace_id to finalize")

    # Codex usage capture: extract rate limits from latest session file.
    # Only runs when hook_model indicates a GPT/Codex model is in use.
    try:
        hook_model = config.get("hook_model", "auto")
        if "gpt" in hook_model.lower() or "codex" in hook_model.lower():
            global _codex_migration_done
            from .codex_usage import (
                get_latest_codex_usage,
                migrate_codex_usage_schema,
                write_codex_usage,
            )

            if not _codex_migration_done:
                migrate_codex_usage_schema(DEFAULT_DB_PATH)
                _codex_migration_done = True
            usage = get_latest_codex_usage()
            if usage:
                write_codex_usage(DEFAULT_DB_PATH, usage)
                log_debug(
                    "hook",
                    f"SubagentStop: Captured codex usage:"
                    f" primary={usage['primary_used_pct']}%,"
                    f" secondary={usage['secondary_used_pct']}%",
                )
    except Exception as e:
        log_warning("hook", "SubagentStop: Failed to capture codex usage", e)


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
            "💡 Consider using the Agent tool to delegate work to specialized subagents (per your guidelines)",
        )

    # Append model preference nudge if configured (without usage stats for brevity)
    model_nudge = get_model_preference_nudge(config, include_usage=False)
    if model_nudge:
        message = f"{message}\n\n{model_nudge}"

    return format_tag(message, "subagent_delegation_reminder")


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

    # Story #155: record a declare_intent MCP tool call (main thread or
    # subagent) so the Write/Edit gate can validate the next edit from it
    # without waiting on the transcript. Done FIRST, before the slow
    # pacing/Langfuse work below, so the declaration is in the store well
    # before the model's next tool call. Never raises for a store failure
    # (the only consequence is the transcript fallback later); a no-op for
    # every other tool and when the kill switch is off.
    if isinstance(hook_data, dict):
        declaration_gate.record_declare_intent(hook_data, config)

    # Bug #157: a subagent's FIRST tool call with no SubagentStart completion
    # record gets the guidance SubagentStart should have delivered (once per
    # agent). The text is PREPARED here (cheap read-only check first) and held
    # aside; it is CLAIMED atomically only at the very end, right before the
    # single output is printed.
    _late_subagent_guidance: Optional[str] = (
        _prepare_late_subagent_guidance(hook_data, config)
        if isinstance(hook_data, dict)
        else None
    )

    # Load state
    state = load_state(DEFAULT_STATE_PATH)

    # Increment global tool execution counter
    state["tool_execution_count"] = state.get("tool_execution_count", 0) + 1

    # Reset stop-block exit valve counter whenever agent uses a tool
    # (agent doing real work breaks the text-only arguing loop)
    if state.get("consecutive_stop_blocks", 0) > 0:
        state["consecutive_stop_blocks"] = 0
        log_debug(
            "hook",
            "PostToolUse: reset consecutive_stop_blocks counter (tool use detected)",
        )

    # Cross-Session Awareness: heartbeat on PostToolUse to keep session visible to siblings.
    try:
        from .session_registry._csa import on_heartbeat as csa_on_heartbeat
        from .session_registry.db import resolve_db_path

        csa_on_heartbeat(
            session_id=session_id or state.get("session_id", ""),
            pid=os.getpid(),
            db_path=resolve_db_path(),
            state=state,
            config=config,
        )
    except Exception as e:
        log_warning("hook", f"CSA post_tool_use heartbeat failed: {e}")

    # Cross-Session Awareness: record tool action for activity trail display.
    # Gate enforcement lives entirely inside on_post_tool_use_record_action()
    # (checks cross_session_awareness_enabled before touching the registry at
    # all) — this call site must never talk to session_registry.registry directly.
    try:
        from .session_registry._csa import (
            on_post_tool_use_record_action as csa_on_post_tool_use_record_action,
        )
        from .session_registry.db import resolve_db_path as _csa_db

        _csa_aid = (
            (hook_data or {}).get("agent_id")
            or session_id
            or state.get("session_id", "")
        )
        csa_on_post_tool_use_record_action(
            agent_id=_csa_aid,
            tool_name=tool_name,
            tool_input=tool_input or {},
            db_path=_csa_db(),
            config=config,
        )
    except Exception as e:
        log_warning("hook", f"CSA record_action failed: {e}")

    # Ensure database is initialized
    db_path = DEFAULT_DB_PATH
    database.initialize_database(db_path)

    # Pacing section - wrapped to prevent BrokenPipeError from blocking Langfuse
    try:
        # Run pacing check
        result = pacing_engine.run_pacing_check(
            db_path=db_path,
            session_id=state["session_id"],
            poll_interval=config.get("poll_interval", 300),
            last_cleanup_time=state.get("last_cleanup_time"),
            safety_buffer_pct=config.get("safety_buffer_pct", 95.0),
            preload_hours=config.get("preload_hours", 12.0),
            api_timeout_seconds=config.get("api_timeout_seconds", 10),
            cleanup_interval_hours=config.get("cleanup_interval_hours", 24),
            retention_days=config.get("retention_days", 60),
            weekly_limit_enabled=config.get("weekly_limit_enabled", True),
            five_hour_limit_enabled=config.get("five_hour_limit_enabled", True),
        )

        # Update state if cleaned up
        state_changed = False
        if result.get("cleanup_time"):
            state["last_cleanup_time"] = result.get("cleanup_time")
            state_changed = True

        if state_changed:
            save_state(state)

        # Apply throttling if needed
        decision = result.get("decision", {})

        # Activity event: PL (API polled) — color reflects result quality
        if result.get("polled"):
            _pl_color = "yellow" if result.get("is_synthetic") else "blue"
            try:
                record_activity_event(
                    DEFAULT_DB_PATH, "PL", _pl_color, state.get("session_id", "unknown")
                )
            except Exception:
                pass  # Activity recording must never break pacing
        elif result.get("error"):
            try:
                record_activity_event(
                    DEFAULT_DB_PATH, "PL", "red", state.get("session_id", "unknown")
                )
            except Exception:
                pass  # Activity recording must never break pacing

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
                # Activity event: PA red (throttle applied)
                try:
                    record_activity_event(
                        DEFAULT_DB_PATH, "PA", "red", state.get("session_id", "unknown")
                    )
                except Exception:
                    pass  # Activity recording must never break pacing
                execute_delay(delay)
        else:
            # Activity event: PA green (pacing ran, no throttle needed)
            try:
                record_activity_event(
                    DEFAULT_DB_PATH, "PA", "green", state.get("session_id", "unknown")
                )
            except Exception:
                pass  # Activity recording must never break pacing

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

        # Accumulate token cost for fallback mode (no-op when not in fallback)
        _accumulate_fallback_cost(
            transcript_path=transcript_path,
            session_id=session_id or "unknown",
        )

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
                # Bug #158: trace selection uses the payload's own identity
                agent_id=hook_data.get("agent_id") or None,
            )

            log_debug(
                "hook",
                f"PostToolUse: handle_post_tool_use returned {result} for session={session_id}",
            )

            # Activity event: LF (Langfuse pushed) — only when enabled AND data was actually pushed
            if config.get("langfuse_enabled", False) and result is True:
                try:
                    record_activity_event(
                        DEFAULT_DB_PATH, "LF", "blue", session_id or "unknown"
                    )
                except Exception:
                    pass  # Activity recording must never break PostToolUse hook

        except Exception as e:
            # AC5: Graceful failure - log error but don't crash hook
            log_warning("hook", "Langfuse span creation failed on PostToolUse", e)

    # Print final message if any (code review takes priority over subagent nudge)
    # Bug #157 (code-review L1): the late-guidance claim is made HERE, just
    # before printing -- never earlier. Losing the race to another PostToolUse of
    # the same agent means that one delivers it; we print no duplicate. A hook
    # killed between the claim and the print could still lose the delivery for
    # a few milliseconds' window (documented, accepted).
    if _late_subagent_guidance and subagent_guidance.claim_late_guidance(hook_data):
        log_info(
            "hook",
            "PostToolUse: delivering subagent guidance late (SubagentStart did "
            f"not complete) agent_id={hook_data.get('agent_id')}",
        )
        pending_message = (
            f"{_late_subagent_guidance}\n\n{pending_message}"
            if pending_message
            else _late_subagent_guidance
        )
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


# Alias for testability: exposes the PostToolUse handler under the name the
# tests expect.  run_hook() IS the PostToolUse handler; this alias lets test
# code do `from pacemaker.hook import handle_post_tool_use` without any
# restructuring of the production function.
handle_post_tool_use = run_hook


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
        transcript_path = hook_data.get("transcript_path")
        return {
            "session_id": session_id,
            "prompt": prompt,
            "transcript_path": transcript_path,
        }
    except json.JSONDecodeError:
        # Fallback to plain text - generate session ID
        return {
            "session_id": f"sess-{int(time.time())}",
            "prompt": raw_input.strip(),
        }


def get_transcript_path(session_id: str) -> Optional[str]:
    """
    Derive transcript path from session_id and project directory.

    Claude Code stores transcripts at:
    ~/.claude/projects/<dir-with-slashes-replaced-by-dashes>/<session_id>.jsonl

    Tries in order:
    1. CLAUDE_PROJECT_DIR env var (most reliable, set by Claude Code)
    2. Current working directory (fallback, may be a subdirectory)

    Args:
        session_id: Session UUID from hook data

    Returns:
        Path to transcript file, or None if not found
    """
    candidates = []

    # Try CLAUDE_PROJECT_DIR first (set by Claude Code, points to project root)
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        dir_name = project_dir.replace("/", "-")
        candidates.append(
            os.path.expanduser(f"~/.claude/projects/{dir_name}/{session_id}.jsonl")
        )

    # Fall back to CWD (may be a subdirectory of the project)
    cwd = os.getcwd()
    cwd_dir_name = cwd.replace("/", "-")
    cwd_path = os.path.expanduser(
        f"~/.claude/projects/{cwd_dir_name}/{session_id}.jsonl"
    )
    if cwd_path not in candidates:
        candidates.append(cwd_path)

    # Return first existing path
    for path in candidates:
        if os.path.exists(path):
            return path
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
            now = datetime.now(timezone.utc)
            state["last_user_interaction_time"] = now
            state.setdefault("last_user_interaction_time_by_session", {})[
                session_id or ""
            ] = now.isoformat()

        save_state(state, DEFAULT_STATE_PATH)

        # Cross-Session Awareness: heartbeat on UserPromptSubmit to keep session visible.
        try:
            from .session_registry._csa import on_heartbeat as csa_on_heartbeat
            from .session_registry.db import resolve_db_path

            _ups_config = load_config(DEFAULT_CONFIG_PATH)
            csa_on_heartbeat(
                session_id=session_id or state.get("session_id", ""),
                pid=os.getpid(),
                db_path=resolve_db_path(),
                state=state,
                config=_ups_config,
            )
        except Exception as e:
            log_warning("hook", f"CSA user_prompt_submit heartbeat failed: {e}")

        # Activity event: UP (user prompt received) — only for real Claude prompts
        if not result["intercepted"]:
            try:
                record_activity_event(
                    DEFAULT_DB_PATH, "UP", "green", session_id or "unknown"
                )
            except Exception:
                pass  # Activity recording must never break user prompt hook

        # AC1: Trigger trace creation for user prompt (trace-per-turn)
        # Only for non-intercepted prompts (actual Claude interactions)
        if not result["intercepted"]:
            try:
                from .langfuse import orchestrator

                # Use transcript_path from hook data, fall back to derived
                transcript_path = parsed_data.get(
                    "transcript_path"
                ) or get_transcript_path(session_id)

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
        intel_nudge = format_tag(
            "§ intel: Start your FIRST response to this user prompt with § intel line. "
            "Emit ONCE only — do NOT repeat in subsequent tool-use messages within this turn. "
            "EXACT format: § △0.0-1.0 ◎surg|const|outc|expl ■bug|feat|refac|research|test|docs|debug|conf|other ◇0.0-1.0 ↻1-9 "
            "(△◇ = decimals NOT words, ◎■ = codes NOT synonyms). "
            "NEVER emit § for background task completions, subagent results, or system notifications — ONLY for human-typed prompts.",
            "intel_nudge",
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


def _get_last_token_usage(transcript_path: Optional[str]) -> Optional[dict]:
    """
    Read the last token usage entry from a JSONL transcript file.

    Uses tail-read (seek to end - 64KB) to avoid loading large files
    into memory. Parses backwards from the end to find the last assistant
    message entry that contains a usage dict.

    Args:
        transcript_path: Path to the JSONL transcript file, or None

    Returns:
        Dict with input_tokens, output_tokens, cache_read_tokens,
        cache_creation_tokens, model_family — or None if not found.
    """
    if not transcript_path:
        return None

    last_usage = None
    last_model = ""

    try:
        import os as _os

        file_size = _os.path.getsize(transcript_path)
        tail_size = 65536
        seek_pos = max(0, file_size - tail_size)

        with open(transcript_path, "rb") as f:
            f.seek(seek_pos)
            raw = f.read()

        # Decode, skip partial first line if we seeked into the middle
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if seek_pos > 0 and lines:
            lines = lines[1:]  # First line may be truncated — skip it

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                msg = obj.get("message", {})
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage")
                if usage and isinstance(usage, dict):
                    last_usage = usage
                    last_model = msg.get("model", "")
            except (json.JSONDecodeError, Exception):
                continue

        if last_usage is None:
            return None

        # Classify model family from model string
        model_lower = last_model.lower()
        if "opus" in model_lower:
            family = "opus"
        elif "haiku" in model_lower:
            family = "haiku"
        else:
            family = "sonnet"

        return {
            "input_tokens": int(last_usage.get("input_tokens", 0)),
            "output_tokens": int(last_usage.get("output_tokens", 0)),
            "cache_read_tokens": int(last_usage.get("cache_read_input_tokens", 0)),
            "cache_creation_tokens": int(
                last_usage.get("cache_creation_input_tokens", 0)
            ),
            "model_family": family,
        }

    except OSError:
        return None
    except Exception as e:
        log_warning("hook", "Failed to read last token usage from transcript", e)
        return None


def _accumulate_fallback_cost(
    transcript_path: Optional[str],
    session_id: str = "unknown",
) -> None:
    """
    Accumulate token cost into fallback state when fallback mode is active.

    Called from run_hook() on each PostToolUse. Uses UsageModel (SQLite)
    for concurrency-safe accumulation. No-op when fallback is not active
    or when transcript has no token usage. Never raises.

    Args:
        transcript_path: Path to the JSONL transcript, or None
        session_id: Session identifier for cost tracking
    """
    try:
        from .usage_model import UsageModel

        model = UsageModel()

        # Fast path: no-op when fallback is not active
        if not model.is_fallback_active():
            return

        token_data = _get_last_token_usage(transcript_path)
        if not token_data:
            return

        model.accumulate_cost(
            input_tokens=token_data["input_tokens"],
            output_tokens=token_data["output_tokens"],
            cache_read_tokens=token_data["cache_read_tokens"],
            cache_creation_tokens=token_data["cache_creation_tokens"],
            model_family=token_data["model_family"],
            session_id=session_id,
        )

    except Exception as e:
        log_warning("hook", "Failed to accumulate fallback cost", e)


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


def should_run_tempo(config: dict, state: dict, session_id: str = "") -> bool:
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
        session_id: Current session ID for per-session timestamp lookup

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
        last_interaction = None
        raw = state.get("last_user_interaction_time_by_session", {}).get(session_id)
        if raw:
            try:
                last_interaction = datetime.fromisoformat(raw)
                if last_interaction.tzinfo is None:
                    last_interaction = last_interaction.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                last_interaction = None

        if last_interaction is None:
            last_interaction = state.get("last_user_interaction_time")

        # No interaction recorded, assume unattended
        if last_interaction is None:
            return True

        # Check if elapsed time exceeds threshold
        threshold_minutes = config.get("auto_tempo_threshold_minutes", 10)
        # State may store naive timestamps — assume UTC
        if hasattr(last_interaction, "tzinfo") and last_interaction.tzinfo is None:
            last_interaction = last_interaction.replace(tzinfo=timezone.utc)
        elapsed_seconds = (
            datetime.now(timezone.utc) - last_interaction
        ).total_seconds()
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

    # State may store naive timestamps — assume UTC
    if (
        hasattr(last_interaction_time, "tzinfo")
        and last_interaction_time.tzinfo is None
    ):
        last_interaction_time = last_interaction_time.replace(tzinfo=timezone.utc)
    elapsed_seconds = (
        datetime.now(timezone.utc) - last_interaction_time
    ).total_seconds()

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

# Stop hook exit valve: number of consecutive blocks before the valve releases on the (N+1)th block.
STOP_EXIT_VALVE_THRESHOLD = (
    4  # Consecutive stop blocks before exit valve releases; 5th block passes through
)


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
        log_info("hook", f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
        log_info("hook", "=" * 70)

        # Load config and state to check if tempo should run
        config = load_config(DEFAULT_CONFIG_PATH)

        # Master switch - all features disabled
        if not config.get("enabled", True):
            log_info("hook", "Pace maker disabled - allowing exit")
            return {"continue": True}

        state = load_state(DEFAULT_STATE_PATH)

        # Minimum Claude Code version check (Story #66 / issue #96): when
        # SessionStart flagged an unsupported Claude Code version, skip this
        # hook's own downstream work (stdin is not even read) and degrade
        # silently rather than validating against a version we don't
        # support. Scope (issue #100): PreToolUse and Stop ONLY — PostToolUse
        # (run_hook(), ~line 1045) has NO version guard at all and continues
        # running normally under a version block (Langfuse pushes, CSA
        # registry writes, usage.db telemetry, pacing all still fire), by
        # design per Story #66.
        if state.get("version_block_active"):
            log_info("hook", "Version block active - allowing exit")
            return {"continue": True}

        # Log auto tempo status
        tempo_mode = config.get("tempo_mode", "auto")
        tempo_session_override = state.get("tempo_session_override")
        last_user_interaction = state.get("last_user_interaction_time")
        if last_user_interaction:
            # State may store naive timestamps — assume UTC
            if (
                hasattr(last_user_interaction, "tzinfo")
                and last_user_interaction.tzinfo is None
            ):
                last_user_interaction = last_user_interaction.replace(
                    tzinfo=timezone.utc
                )
            elapsed = (
                datetime.now(timezone.utc) - last_user_interaction
            ).total_seconds()
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

        # Use transcript_path from hook_data (Claude Code provides it), fall back to derived
        transcript_path = hook_data.get("transcript_path") or (
            get_transcript_path(session_id) if session_id else None
        )

        # Cross-Session Awareness: final heartbeat then unregister session on stop.
        # session_id passed directly; CSA functions validate inputs and fail-open on None.
        try:
            from .session_registry._csa import (
                on_heartbeat as csa_on_heartbeat,
                on_session_end as csa_on_session_end,
            )
            from .session_registry.db import resolve_db_path

            _csa_stop_state = load_state(DEFAULT_STATE_PATH)
            _csa_stop_db = resolve_db_path()
            csa_on_heartbeat(
                session_id=session_id,
                pid=os.getpid(),
                db_path=_csa_stop_db,
                state=_csa_stop_state,
                config=config,
            )
            csa_on_session_end(
                session_id=session_id,
                pid=os.getpid(),
                db_path=_csa_stop_db,
                state=_csa_stop_state,
                config=config,
            )
        except Exception as e:
            log_warning("hook", f"CSA stop cleanup failed: {e}")

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

        # NOTE: SM (secret masked) event is now fired in orchestrator.py after each
        # sanitize_trace() call, where masking actually occurs. It is NOT fired here.

        # CRITICAL: Check for context exhaustion BEFORE any blocking logic.
        # If conversation hit "Prompt is too long" error, compaction failed
        # and Claude cannot respond - must allow graceful exit.
        if is_context_exhaustion_detected(transcript_path):
            log_debug("hook", "Allowing graceful exit due to context exhaustion")
            # Activity event: CX (context exhaustion detected)
            try:
                record_activity_event(
                    DEFAULT_DB_PATH, "CX", "red", session_id or "unknown"
                )
            except Exception:
                pass  # Activity recording must never break stop hook
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
                return {
                    "decision": "block",
                    "reason": format_tag(nudge_message, "stop_continuation_nudge"),
                }
            else:
                # Max nudges reached — reset counter and allow exit
                state["silent_tool_nudge_count"] = 0
                save_state(state, DEFAULT_STATE_PATH)
                log_debug("hook", "Max silent tool nudges reached - allowing exit")
                return {"continue": True}

        # NOW check tempo - if disabled, allow exit after silent-stop check
        if not should_run_tempo(config, state, session_id=session_id or ""):
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
            hook_model=config.get("hook_model", "auto"),
        )

        log_debug("hook", f"Intent validation result: {result}")

        # Story #101: tag the tempo-block reason BEFORE it is used for
        # record_blockage or the final return, so both carry the tag.
        # APPROVED/COMPLETE path is untouched.
        if result.get("decision") == "block":
            result["reason"] = format_tag(
                result.get("reason", "Work appears incomplete"), "stop_tempo_block"
            )

        # Exit valve: prevent infinite block loop when agent only produces text without tool use.
        # After 5 consecutive blocks without intervening tool use, allow exit to avoid deadlock.
        _EXIT_VALVE_THRESHOLD = STOP_EXIT_VALVE_THRESHOLD
        if result.get("decision") == "block":
            _consec = state.get("consecutive_stop_blocks", 0)
            if _consec >= _EXIT_VALVE_THRESHOLD:
                # 5th consecutive block without tool use — activate exit valve
                state["consecutive_stop_blocks"] = 0
                save_state(state, DEFAULT_STATE_PATH)
                log_warning(
                    "hook",
                    f"Stop hook exit valve activated after {_consec + 1} consecutive blocks "
                    "without tool use. Allowing exit to prevent deadlock.",
                )
                try:
                    record_activity_event(
                        DEFAULT_DB_PATH, "EV", "yellow", session_id or "unknown"
                    )
                except Exception as e:
                    log_warning(
                        "hook", "Exit valve: failed to record EV activity event", e
                    )
                return {"continue": True}
            else:
                state["consecutive_stop_blocks"] = _consec + 1
                save_state(state, DEFAULT_STATE_PATH)
                log_debug(
                    "hook", f"Stop hook blocked: consecutive_stop_blocks={_consec + 1}"
                )
        else:
            # APPROVED or COMPLETE: reset exit valve counter
            if state.get("consecutive_stop_blocks", 0) > 0:
                state["consecutive_stop_blocks"] = 0
                save_state(state, DEFAULT_STATE_PATH)
                log_debug(
                    "hook", "Stop hook approved: reset consecutive_stop_blocks counter"
                )

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

        # Activity event: ST (stop hook result)
        try:
            _st_status = "red" if result.get("decision") == "block" else "green"
            record_activity_event(
                DEFAULT_DB_PATH, "ST", _st_status, session_id or "unknown"
            )
        except Exception:
            pass  # Activity recording must never break stop hook

        # Return validation result
        return result

    except Exception as e:
        # Graceful degradation - log error and allow exit
        print(f"[PACE-MAKER ERROR] Stop hook: {e}", file=sys.stderr)
        return {"continue": True}


def _merge_csa_reminder(
    response: Dict[str, Any], csa_result: Dict[str, Any]
) -> Dict[str, Any]:
    """Inject CSA periodic_reminder into response hookSpecificOutput.additionalContext.

    Returns response unchanged when csa_result has no periodic_reminder.
    When a reminder is present, merges it into hookSpecificOutput.additionalContext,
    preserving any existing additionalContext by appending with a blank-line separator.
    """
    reminder = csa_result.get("periodic_reminder", "") if csa_result else ""
    if not reminder:
        return response

    existing = response.get("hookSpecificOutput", {}).get("additionalContext", "")
    merged_context = f"{existing}\n\n{reminder}" if existing else reminder

    result = dict(response)
    result["hookSpecificOutput"] = {
        "hookEventName": "PreToolUse",
        "additionalContext": merged_context,
    }
    return result


def _normalize_reasoning_summary_intent_models(raw: Any) -> List[str]:
    """Issue #151 code review H1: ``reasoning_summary_intent_models`` comes
    from a hand-editable config.json and can be ANYTHING -- ``null``,
    ``false``, a bare string, or a list containing non-string entries.

    Without this guard, ``model in cfg_value`` (the exception-gating
    membership check) has two failure modes:
      1. ``cfg_value`` is ``None``/``False`` -> ``model in None`` raises
         ``TypeError: argument of type 'NoneType' is not iterable``,
         which the pre-tool gate's outer exception handler turns into a
         fail-closed error for EVERY model (not just Opus), and the error
         message tells the agent to ask the user to disable intent
         validation entirely -- the worst possible failure mode for a
         malformed config value.
      2. ``cfg_value`` is a bare STRING -> Python's ``in`` operator does
         SUBSTRING matching on a string, not list membership, so
         ``"claude-opus-5-5" in "claude-opus-5-5-and-more"`` is ``True``
         even though no list ever contained that entry.

    Returns a list of strings only -- never raises. Logs a WARNING and
    returns ``[]`` (exception disabled for every model, the safe default)
    for anything that isn't a list, and silently drops any non-string
    list entries (also logged).
    """
    if not isinstance(raw, list):
        log_warning(
            "hook",
            "reasoning_summary_intent_models must be a list, got "
            f"{type(raw).__name__}: {raw!r} -- treating as an empty list "
            "(exception disabled for every model)",
        )
        return []
    result = [item for item in raw if isinstance(item, str)]
    invalid = [item for item in raw if not isinstance(item, str)]
    if invalid:
        log_warning(
            "hook",
            f"reasoning_summary_intent_models contains non-string entries, "
            f"ignoring them: {invalid!r}",
        )
    return result


def _build_bash_command_preview(command: str, max_chars: int = 60) -> str:
    """Issue #150 code-review follow-up (item 2, extended by the second
    review's item 2): render a single-line, length-capped preview of a
    Bash command for the no-visible-text notice's INTENT: example.

    A raw ``command[:max_chars]`` slice can include embedded newlines
    (e.g. a heredoc), which breaks the rendered example across multiple
    lines -- and a later line in a long/adversarial command could even
    happen to start with a "[pace-maker · ...]"-shaped substring,
    confusable with a real provenance tag. This takes the FIRST
    NON-BLANK line (not just ``lines[0]``, which could itself be blank or
    whitespace-only -- e.g. ``"\\nrm -rf x"`` or a command a Bash
    formatter padded with a leading blank line -- producing an empty, or
    "..."-only, preview), collapses internal whitespace runs to single
    spaces, then truncates to ``max_chars`` -- appending "..." whenever
    OTHER lines exist besides the chosen one (dropped by either the
    line-selection or the length cut), so the agent always knows the
    preview is partial.
    """
    lines = command.splitlines()
    non_blank_index = next((i for i, line in enumerate(lines) if line.strip()), None)
    if non_blank_index is None:
        # No non-blank line at all (empty command, or all-whitespace) --
        # nothing meaningful to show; fall back to the raw command so an
        # empty/whitespace-only command still renders as itself rather
        # than silently vanishing.
        chosen_line = command
        other_lines_exist = False
    else:
        chosen_line = lines[non_blank_index]
        other_lines_exist = len(lines) > 1
    collapsed = " ".join(chosen_line.split())
    truncated = other_lines_exist or len(collapsed) > max_chars
    preview = collapsed[:max_chars]
    if truncated:
        preview += "..."
    return preview


def _fail_closed_message(error: BaseException) -> str:
    """Build the fail-closed block reason for an unexpected pre-tool gate
    exception.

    Shared by both the Write/Edit intent-validation gate and the
    Danger-Bash gate (Messi Anti-Duplication) for the backstop that fires
    when an unexpected exception occurs AFTER the relevant gate has been
    confirmed active for this tool call (see ``_gate_committed`` in
    ``run_pre_tool_hook``).
    """
    return format_tag(
        "⛔ Intent validation hit an unexpected internal error\n\n"
        f"The pre-tool validation hook encountered an unexpected internal "
        f"error while validating this tool call: {error}\n\n"
        "The tool call was BLOCKED as a safety precaution (fail-closed) "
        "rather than allowed through unvalidated.\n\n"
        "If this persists, ask the user to run: pace-maker intent-validation off",
        "fail_closed_error",
    )


def _resolve_project_name(cwd: Optional[str] = None) -> str:
    """Return the project label for a governance event (issue #134).

    Previously every call site used `os.path.basename(os.getcwd())` — the HOOK
    PROCESS's working directory, which is whatever the invoking tool call
    happened to be in, not the project. That mislabelled 44% of governance
    events as `hooks`, `src`, `.claude` and similar directory fragments; `src`
    is the worst because it is ambiguous across every repository.

    Resolves through the same `resolve_workspace_root()` that Cross-Session
    Awareness keys on, so both subsystems agree on what a project is. Prefers
    the hook payload's own `cwd` over the process cwd.

    Degrades rather than raises: a telemetry label must never break the gate.
    ImportError covers the local import, OSError the path handling.
    `resolve_workspace_root` already absorbs its own git-subprocess failures
    (TimeoutExpired, FileNotFoundError) and always returns, so no subprocess
    exception can surface here. Each fallback is strictly less informative
    than the last, never wrong in a new way.
    """
    base = cwd or os.getcwd()
    try:
        from .session_registry.workspace import resolve_workspace_root

        return os.path.basename(resolve_workspace_root(base))
    except (ImportError, OSError) as e:
        # Deliberate degradation, not a swallowed error: fall back to the raw
        # directory basename so the event is still recorded with the best
        # label available. Logged so the loss of fidelity stays visible.
        log_debug("hook", f"project-name resolution fell back for {base!r}: {e}")
        try:
            return os.path.basename(base)
        except (TypeError, AttributeError):
            return "unknown"


def _record_degraded_review_telemetry(
    degradation: Optional[Dict[str, Any]], reviewer: str, session_id: str
) -> None:
    """Record DG activity + governance events for a degraded-but-approved
    review (issue #131) — a verifier failed to respond but the review was
    still APPROVED by the responders. Shared by the Write/Edit and
    Danger-Bash gates (Messi Anti-Duplication).

    No-op when degradation is falsy or not actually degraded. Never raises —
    telemetry recording must never break the pre-tool hook.
    """
    if not degradation or not degradation.get("degraded"):
        return
    try:
        record_activity_event(DEFAULT_DB_PATH, "DG", "yellow", session_id)
        _failed = degradation.get("failed_providers", {})
        _failed_desc = "; ".join(f"{m}: {r}" for m, r in _failed.items())
        _project_name = _resolve_project_name()
        record_governance_event(
            db_path=DEFAULT_DB_PATH,
            event_type="DG",
            project_name=_project_name,
            session_id=session_id,
            feedback_text=(
                f"[{reviewer}] Degraded approval — verifier(s) unavailable: "
                f"{_failed_desc}"
            ),
        )
    except Exception:
        pass


def _record_reasoning_summary_telemetry(
    intent_source: Optional[str], reviewer: str, session_id: str
) -> None:
    """Record RS activity + governance events for an approval reached via
    the issue #151 reasoning-summary intent exception (configured models
    only) -- the story's own telemetry requirement: "Approvals via summary
    are recorded as an activity/governance event, so the rate is
    observable." Shared by the Write/Edit and Danger-Bash gates (Messi
    Anti-Duplication, mirrors _record_degraded_review_telemetry's shape).

    No-op unless intent_source is "reasoning_summary" or "visible_text"
    (issue #151 code review H2) -- an exception-model turn tagged
    "declaration" already complied with the normal strict contract and
    must NOT be recorded as a relaxed/RS approval, and None means a
    non-exception-model approval. Never raises — telemetry recording must
    never break the pre-tool hook.
    """
    if intent_source not in (
        "reasoning_summary",
        "visible_text",
        "prior_reasoning_summary",
    ):
        return
    try:
        record_activity_event(DEFAULT_DB_PATH, "RS", "green", session_id)
        _project_name = _resolve_project_name()
        # Live-test follow-up: the wording used to be a single generic
        # "Approved via reasoning-summary intent" phrase for BOTH sources --
        # misleading for `visible_text`, where no reasoning summary was
        # involved at all (the claude-usage monitor showed exactly this for
        # a visible-text-only approval). Name the ACTUAL source. The
        # `[reviewer]` bracket prefix and the `intent_source=` token are
        # unchanged (the monitor may parse them); feedback_text stays
        # untagged (issue #101 B2 -- this is governance/telemetry text, not
        # the Claude-facing block reason).
        if intent_source == "reasoning_summary":
            _rs_description = "reasoning summary"
        elif intent_source == "visible_text":
            _rs_description = "visible text without INTENT: marker"
        else:
            # Issue #151 live-replay follow-up (round 3, CHANGE 2): the
            # anchor itself was empty -- the intent came from the
            # IMMEDIATELY PRECEDING turn's own reasoning summary/visible
            # text instead.
            _rs_description = "previous message's reasoning summary"
        record_governance_event(
            db_path=DEFAULT_DB_PATH,
            event_type="RS",
            project_name=_project_name,
            session_id=session_id,
            feedback_text=(
                f"[{reviewer}] Approved via relaxed intent: {_rs_description} "
                f"(intent_source={intent_source})"
            ),
        )
    except Exception:
        pass


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
    # Tracks whether the Write/Edit intent-validation gate OR the Danger-Bash
    # gate has been confirmed ACTIVE for this specific tool call (i.e. we are
    # past the config check that gates entry, or a dangerous command was
    # actually matched). Declared before `try:` so it is guaranteed bound in
    # the except handler regardless of where an exception originates. Once
    # True, an unexpected exception must fail CLOSED (Messi Anti-Fallback):
    # an unvalidated edit/command is worse than a blocked one when the user
    # explicitly opted into the gate. Before this flag existed, ANY exception
    # anywhere in this function — including one AFTER the gate already
    # decided to block (e.g. a sqlite error inside record_blockage) —
    # silently fell back to {"continue": True}, defeating the gate.
    _gate_committed = False

    # Story #155 (review L2): the tool/chain intent in use for this edit, and
    # what is needed to end its chain, declared before `try:` so the
    # fail-closed handler below can always read them (None until the Write/
    # Edit gate has resolved one).
    _declared_intent: Optional[declaration_gate.DeclaredIntent] = None
    _declared_ctx: Optional[tuple] = None

    # Single wall clock for the whole gate (issue #108). Claude Code kills
    # this hook at PRE_TOOL_HOOK_TIMEOUT_SECONDS, and a KILLED PreToolUse hook
    # is an UNVALIDATED TOOL CALL — the harness simply proceeds. The phases
    # (anchor wait -> reviewers -> synthesis) each used to carry their own
    # fixed timeout with nothing summing them, so the chain could reach 90s
    # against a 60s allowance. Every phase now takes min(its cap, remaining),
    # so the gate always answers in time — with fewer reviewers if it must.
    _gate_deadline = (
        time.monotonic()
        + PRE_TOOL_HOOK_TIMEOUT_SECONDS
        - PRE_TOOL_SAFETY_MARGIN_SECONDS
    )
    try:
        # CSA result must be initialized before ANY operation that could throw,
        # so the outer except handler at the bottom of this function can safely
        # reference it via _merge_csa_reminder() for fail-open semantics.
        _csa_result: Dict[str, Any] = {}

        # Minimum Claude Code version check (Story #66 / issue #96): when
        # SessionStart flagged an unsupported Claude Code version, skip this
        # hook's own downstream work (stdin is not even read) and degrade
        # silently rather than validating against a version we don't
        # support. Scope (issue #100): PreToolUse and Stop ONLY — PostToolUse
        # (run_hook(), ~line 1045) has NO version guard at all and continues
        # running normally under a version block (Langfuse pushes, CSA
        # registry writes, usage.db telemetry, pacing all still fire), by
        # design per Story #66.
        if load_state(DEFAULT_STATE_PATH).get("version_block_active"):
            return {"continue": True}

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

        # Use transcript_path from hook_data (Claude Code provides it), fall back to derived
        transcript_path = hook_data.get("transcript_path") or (
            get_transcript_path(session_id) if session_id else None
        )

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

        # Check if we're in a subagent context and resolve transcript path
        agent_id = hook_data.get("agent_id")
        tool_use_id = hook_data.get("tool_use_id")

        # Primary: Use agent_id to construct direct path (deterministic, no timing dependency)
        if agent_id and transcript_path:
            projects_dir = os.path.dirname(transcript_path)
            # Claude Code 2.1.39+ structure: <session_id>/subagents/agent-<agent_id>.jsonl
            if session_id:
                direct_path = os.path.join(
                    projects_dir, session_id, "subagents", f"agent-{agent_id}.jsonl"
                )
                if os.path.exists(direct_path):
                    log_debug(
                        "hook",
                        f"Resolved subagent transcript via agent_id: {direct_path}",
                    )
                    transcript_path = direct_path
                else:
                    # Legacy flat structure: agent-<agent_id>.jsonl in projects dir
                    legacy_path = os.path.join(projects_dir, f"agent-{agent_id}.jsonl")
                    if os.path.exists(legacy_path):
                        log_debug(
                            "hook",
                            f"Resolved subagent transcript via agent_id (legacy): {legacy_path}",
                        )
                        transcript_path = legacy_path
                    else:
                        log_warning(
                            "hook",
                            f"Subagent transcript not found for agent_id={agent_id} "
                            f"(tried {direct_path} and {legacy_path})",
                            None,
                        )

        # Fallback: Search by tool_use_id if agent_id not available (older Claude Code versions)
        elif transcript_path and "/agent-" not in transcript_path and tool_use_id:
            import glob

            projects_dir = os.path.dirname(transcript_path)
            agent_transcripts = glob.glob(os.path.join(projects_dir, "agent-*.jsonl"))
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
                f"Fallback: Searching {len(recent_agents)} recent agent transcripts for tool_use_id: {tool_use_id}",
            )

            for agent_path in recent_agents:
                try:
                    with open(agent_path, "r") as f:
                        for line in f:
                            if tool_use_id in line:
                                log_debug(
                                    "hook",
                                    f"Found tool_use_id in subagent transcript: {agent_path}",
                                )
                                transcript_path = agent_path
                                break
                    if transcript_path == agent_path:
                        break
                except Exception as e:
                    log_debug("hook", f"Error searching {agent_path}: {e}")

        # Cross-Session Awareness: increment per-agent counter, get reminders/warnings.
        # Runs before Danger Bash so danger_bash_warning is available for Stage 2.
        try:
            from .session_registry._csa import on_pre_tool_use as csa_on_pre_tool_use
            from .session_registry.db import resolve_db_path

            _csa_config = load_config(DEFAULT_CONFIG_PATH)
            _csa_state = load_state(DEFAULT_STATE_PATH)
            _csa_command = tool_input.get("command") if tool_name == "Bash" else None
            _csa_result = csa_on_pre_tool_use(
                session_id=session_id or "",
                agent_id=agent_id or "root",
                pid=os.getpid(),
                tool_name=tool_name or "",
                command=_csa_command,
                db_path=resolve_db_path(),
                state=_csa_state,
                config=_csa_config,
            )
            save_state(_csa_state, DEFAULT_STATE_PATH)
        except Exception as e:
            log_warning("hook", f"CSA pre_tool_use failed: {e}")

        # 2a. Danger Bash validation (before Write/Edit check)
        if tool_name == "Bash":
            try:
                bash_config = load_config(DEFAULT_CONFIG_PATH)
                if (
                    bash_config.get("enabled", True)
                    and bash_config.get("intent_validation_enabled", False)
                    and bash_config.get("danger_bash_enabled", True)
                ):
                    command = tool_input.get("command", "")
                    description = tool_input.get("description", "")

                    from .danger_bash_rules import load_rules, match_command

                    rules = load_rules(DEFAULT_DANGER_RULES_PATH)
                    matched = match_command(command, rules)

                    if matched:
                        matched_ids = [m["id"] for m in matched]
                        log_debug(
                            "hook",
                            f"Danger bash rules matched: {matched_ids} for command: {command[:100]}",
                        )

                        # A dangerous command was confirmed matched — from
                        # here on an unexpected exception must fail CLOSED
                        # rather than silently letting the command through.
                        _gate_committed = True

                        # Issue #93 (bug #83 follow-up): tool-matched anchor,
                        # CONSUMED (not discarded) as the Phase 1/2 message --
                        # see _DANGER_BASH_MAX_WAIT_SECONDS's docstring above
                        # for the reduced-ceiling rationale.
                        #
                        # Outcome: found -> anchor's own text (may be "" if
                        # no INTENT: in TEXT; Phase 1 below blocks that).
                        # stale -> the byte-identical-matched tool_use IS
                        # this command, but a multi-tool-call turn's merged
                        # text may describe a SIBLING call, not necessarily
                        # this one -- accepted anyway; Phase 2 (LLM, prompt
                        # below) is the actual semantic check, not byte
                        # identity. not_found -> block, instructing a
                        # byte-identical re-issue.
                        #
                        # Termination: the per-attempt wait is bounded
                        # (_DANGER_BASH_MAX_WAIT_SECONDS, ~3s), but the
                        # NUMBER OF ROUNDS is NOT bounded -- there is no
                        # Messi Rule 14 guarantee here. A not_found -> block
                        # -> re-issue round-trip only recovers via the
                        # "stale" path while the ORIGINAL blocked attempt's
                        # tool_use remains within the last
                        # LAST_N_TURNS_FOR_TOOL_MATCH (2, see
                        # transcript_reader.py) logical assistant turns by
                        # the time the re-issue's own search runs. Measured
                        # directly by varying the number of assistant turns
                        # between the blocked attempt and the re-issue: 0 or
                        # 1 intervening turns -> "stale" (recovers); 2 or
                        # more intervening turns -> "not_found" again (the
                        # blocked attempt has scrolled outside the
                        # LAST_N_TURNS_FOR_TOOL_MATCH window) -- in that case
                        # the not_found/re-issue cycle repeats with no upper
                        # bound on the number of rounds.
                        _bash_diagnostics: dict = {}
                        _bash_anchor = get_current_turn_message_for_validation(
                            transcript_path,
                            tool_input={"command": command},
                            tool_name="Bash",
                            _max_wait_seconds=_DANGER_BASH_MAX_WAIT_SECONDS,
                            _diagnostics=_bash_diagnostics,
                        )
                        _bash_outcome = _bash_diagnostics.get("outcome")
                        # Issue #141, broadened by issue #148: same
                        # computation as the Write/Edit gate -- fires
                        # whenever the anchored turn had NO visible text,
                        # regardless of whether `thinking` was present,
                        # empty, or absent entirely. `is False` (not falsy)
                        # requires an EXPLICIT False, since a not_found
                        # anchor leaves the diagnostics key absent (.get
                        # returns None), which must never be misread as
                        # "no visible text". Issue #141 originally also
                        # required `anchor_has_thinking` to be truthy,
                        # which missed the empty-thinking and
                        # no-thinking-at-all shapes -- see issue #148.
                        _bash_no_visible_text = (
                            _bash_diagnostics.get("anchor_has_visible_text") is False
                        )

                        if _bash_anchor is not None:
                            current_message = _bash_anchor
                        elif _bash_outcome == "stale":
                            current_message = _bash_diagnostics.get("stale_text", "")
                            log_debug(
                                "hook",
                                "Danger bash: accepted STALE anchor turn "
                                "(byte-identical re-issue) for command: "
                                f"{command[:100]}",
                            )
                        else:
                            _sid = session_id or "unknown"
                            record_blockage(
                                db_path=DEFAULT_DB_PATH,
                                category="intent_validation_dangerbash",
                                reason=(
                                    "Danger Bash: transcript not yet flushed "
                                    "— failing closed for safety."
                                ),
                                hook_type="pre_tool_use",
                                session_id=_sid,
                                details={
                                    "tool": "Bash",
                                    "command": command[:500],
                                    "attempts": _bash_diagnostics.get("attempts"),
                                    "elapsed_seconds": _bash_diagnostics.get(
                                        "elapsed_seconds"
                                    ),
                                    "outcome": _bash_outcome,
                                },
                            )
                            return {
                                "decision": "block",
                                "reason": format_tag(
                                    "⛔ Dangerous Bash command detected — transcript not ready\n\n"
                                    "The current turn has not been written to the transcript yet. "
                                    "Cannot verify INTENT: declaration. Failing closed for safety.\n\n"
                                    "RE-ISSUE THE IDENTICAL Bash TOOL CALL with the SAME INTENT: "
                                    "declaration in the same message. IMPORTANT: the command must be "
                                    "byte-identical to this attempt — the validator binds to the exact "
                                    "command string, and a rephrased or reformulated command will not "
                                    "match. Re-issue it as your VERY NEXT tool call — if any other "
                                    "tool call intervenes first, this same block will recur.",
                                    "danger_bash_deferred",
                                ),
                            }

                        # Issue #151: reasoning-summary intent exception,
                        # configured models only. Mirrors the Write/Edit
                        # gate's gating exactly -- gated on the anchored
                        # turn's own assistant message.model (populated by
                        # transcript_reader on both the found and stale
                        # branches). resolve_reasoning_summary_intent_source
                        # (H2/M1, shared with the Write/Edit gate) decides
                        # whether this turn takes the STRICT path (real
                        # declaration already present in the anchor's own
                        # visible text, or nothing to relax against) or the
                        # RELAXED path -- ONLY in the relaxed case is
                        # current_message overwritten with the combined
                        # (visible text + reasoning summary) intent_text;
                        # otherwise current_message stays exactly as the
                        # found/stale gate above set it.
                        from .intent_validator import (
                            build_danger_bash_reasoning_summary_label,
                            build_danger_bash_relaxed_intent_wording,
                            build_danger_bash_visible_text_label,
                            resolve_reasoning_summary_intent_source,
                        )

                        _bash_anchor_model = _bash_diagnostics.get("anchor_model")
                        _bash_anchor_visible_text = (
                            _bash_diagnostics.get("anchor_prose_text") or ""
                        )
                        _bash_anchor_reasoning_summary = (
                            _bash_diagnostics.get("anchor_reasoning_summary") or ""
                        )
                        # Issue #151 code review H1: never trust the raw
                        # config value's shape.
                        _reasoning_summary_models = (
                            _normalize_reasoning_summary_intent_models(
                                bash_config.get(
                                    "reasoning_summary_intent_models",
                                    DEFAULT_CONFIG.get(
                                        "reasoning_summary_intent_models", []
                                    ),
                                )
                            )
                        )
                        _bash_reasoning_summary_intent = (
                            bool(_bash_anchor_model)
                            and _bash_anchor_model in _reasoning_summary_models
                        )

                        # Issue #154 item 2 (decided): the CHANGE 2
                        # prior-turn fallback is restricted to Write/Edit
                        # only -- the user's approval for it was based on
                        # Edit evidence, and letting an empty anchored
                        # Bash turn pass Phase 1 on the PREVIOUS turn's
                        # text would relax #93's deliberate "Bash is
                        # anchor-only" tightening and #139's stale-path
                        # anchor-only rule. `allow_prior_turn_fallback` is
                        # an EXPLICIT parameter on the shared resolver
                        # (never an implicit tool-name check buried inside
                        # it) -- danger-bash passes False, keeping its
                        # pre-CHANGE-2 behavior for an empty anchor (block
                        # with the no-visible-text notice) unchanged.
                        _bash_intent_source, _bash_relaxed_text = (
                            resolve_reasoning_summary_intent_source(
                                _bash_reasoning_summary_intent,
                                _bash_anchor_visible_text,
                                _bash_anchor_reasoning_summary,
                                recent_context=_bash_diagnostics.get(
                                    "anchor_recent_context"
                                ),
                                allow_prior_turn_fallback=False,
                            )
                        )
                        if _bash_relaxed_text is not None:
                            current_message = _bash_relaxed_text

                        # Phase 1: Check for INTENT: marker (fast reject, no
                        # LLM) -- OR, for the RELAXED sources only, just
                        # require the combined intent_text to be non-empty
                        # (no INTENT:/TDD/version-bump regex at all). The
                        # STRICT sources ("declaration" or None) use the
                        # exact same marker check a non-exception model
                        # gets.
                        from .intent_validator import (
                            _has_intent_marker,
                            build_no_visible_text_notice,
                        )

                        _RELAXED_BASH_INTENT_SOURCES = (
                            "reasoning_summary",
                            "visible_text",
                        )
                        _bash_phase1_passed = (
                            bool(current_message.strip())
                            if _bash_intent_source in _RELAXED_BASH_INTENT_SOURCES
                            else bool(_has_intent_marker(current_message))
                        )
                        if not _bash_phase1_passed:
                            # Phase 1 BLOCKED — no intent declared
                            _sid = session_id or "unknown"
                            _bash_phase1_details = {
                                "tool": "Bash",
                                "command": command[:500],
                                "matched_rules": matched_ids,
                                "reviewer": "unknown",
                                # Issue #141 code-review follow-up
                                # (item 3), key renamed by issue #148:
                                # lets usage.db / the claude-usage
                                # monitor distinguish a no-visible-text
                                # block from an ordinary missing-INTENT
                                # block.
                                "no_visible_text": _bash_no_visible_text,
                            }
                            if _bash_reasoning_summary_intent:
                                # Issue #151 code review L1: added ONLY for
                                # exception-model turns.
                                _bash_phase1_details["intent_source"] = (
                                    _bash_intent_source
                                )
                            record_blockage(
                                db_path=DEFAULT_DB_PATH,
                                category="intent_validation",
                                reason=(
                                    f"Dangerous Bash command detected but no INTENT: declaration found. "
                                    f"Matched rules: {matched_ids}. You must declare INTENT: before "
                                    f"running dangerous commands."
                                ),
                                hook_type="pre_tool_use",
                                session_id=_sid,
                                details=_bash_phase1_details,
                            )
                            try:
                                record_activity_event(
                                    DEFAULT_DB_PATH, "IV", "red", _sid
                                )
                                record_activity_event(
                                    DEFAULT_DB_PATH, "DB", "red", _sid
                                )
                                _project_name = _resolve_project_name()
                                record_governance_event(
                                    db_path=DEFAULT_DB_PATH,
                                    event_type="IV",
                                    project_name=_project_name,
                                    session_id=_sid,
                                    feedback_text=(
                                        f"[RegEx] Dangerous Bash command blocked (no INTENT:). "
                                        f"Rules: {matched_ids}. Command: {command[:200]}"
                                    ),
                                )
                            except Exception:
                                pass
                            _bash_no_intent_reason = (
                                f"⛔ Dangerous Bash command detected — no INTENT: declaration\n\n"
                                f"Matched danger rules: {matched_ids}\n"
                                f"Command: {command[:300]}\n\n"
                                f"You must declare INTENT: specifying exactly what this command "
                                f"will do before executing dangerous Bash operations."
                            )
                            if _bash_no_visible_text:
                                # Issue #150: lead the block reason with
                                # the pilot-validated notice (a
                                # ready-to-copy INTENT: example naming the
                                # ACTUAL command, since a generic notice
                                # appended at the end of the template
                                # (issue #141/#148's THINKING_ONLY_NOTICE)
                                # measured far worse recovery for Opus 5.5
                                # at xhigh reasoning effort -- see
                                # CLAUDE.md's "Issue #150" section.
                                _cmd_preview = _build_bash_command_preview(command)
                                _bash_example = f"INTENT: Run {_cmd_preview} to <goal>."
                                _bash_no_intent_reason = (
                                    build_no_visible_text_notice(_bash_example)
                                    + "\n\n"
                                    + _bash_no_intent_reason
                                )
                            return {
                                "decision": "block",
                                "reason": format_tag(
                                    _bash_no_intent_reason,
                                    "danger_bash_block",
                                ),
                            }

                        # Phase 2: LLM validates intent-to-command alignment
                        try:
                            _sid = session_id or "unknown"
                            record_activity_event(DEFAULT_DB_PATH, "DB", "blue", _sid)
                        except Exception:
                            pass

                        from .inference import resolve_and_call_with_reviewer
                        from .inference.verdict import verdict_passes
                        from .intent_validator import (
                            build_reviewer_unavailable_message,
                        )

                        matched_descriptions = ", ".join(
                            f"{m['id']}: {m['description']}" for m in matched
                        )
                        # Issue #151 code review M2: the ENTIRE prompt must
                        # be consistent when the intent source is RELAXED
                        # (visible_text/reasoning_summary) -- the intro
                        # line, the assistant-message label, VALIDATE item
                        # 1, and the mismatch line all reframe to "the
                        # stated intent" together (externalized, Messi Rule
                        # 11), never mixing "declared intent"/"INTENT:
                        # declaration" wording with a marker-less turn. The
                        # STRICT wording (declaration present, or non-
                        # exception model) stays inline here, UNCHANGED --
                        # byte-identical to the pre-#151 prompt.
                        if _bash_intent_source in _RELAXED_BASH_INTENT_SOURCES:
                            _bash_intro, _bash_validate_item1, _bash_mismatch_line = (
                                build_danger_bash_relaxed_intent_wording()
                            )
                            if _bash_intent_source == "reasoning_summary":
                                _bash_assistant_label = (
                                    build_danger_bash_reasoning_summary_label()
                                )
                            else:
                                _bash_assistant_label = (
                                    build_danger_bash_visible_text_label()
                                )
                        else:
                            _bash_intro = (
                                "You are validating if the declared intent matches "
                                "what a Bash command will actually do."
                            )
                            _bash_validate_item1 = (
                                "Does the INTENT: declaration SPECIFICALLY describe "
                                "what this command does?"
                            )
                            _bash_mismatch_line = (
                                "If the intent declaration appears to be for a "
                                "DIFFERENT tool call earlier in the message, treat "
                                "as mismatch."
                            )
                            _bash_assistant_label = (
                                "ASSISTANT MESSAGE (contains intent declaration)"
                            )
                        # Issue #151 live-replay follow-up (round 3,
                        # CHANGE 4): shared by BOTH the strict and relaxed
                        # wording variants (this is the common f-string
                        # both fall through to) -- see
                        # build_danger_bash_destructive_scope_note's own
                        # docstring for the live evidence.
                        from .intent_validator import (
                            build_danger_bash_destructive_scope_note,
                        )

                        _bash_destructive_scope_note = (
                            build_danger_bash_destructive_scope_note()
                        )
                        bash_prompt = (
                            f"{_bash_intro}\n\n"
                            f"{_bash_assistant_label}:\n"
                            f"{current_message[:3000]}\n\n"
                            f"BASH COMMAND:\n{command}\n\n"
                            f"BASH DESCRIPTION FIELD:\n{description}\n\n"
                            f"MATCHED DANGER RULES:\n{matched_descriptions}\n\n"
                            f"{_bash_destructive_scope_note}\n\n"
                            f"VALIDATE:\n"
                            f"1. {_bash_validate_item1}\n"
                            f"2. Does the command scope match the intent scope?\n"
                            f"3. Is the description field honest about the effect?\n"
                            f"4. Are there undeclared side effects?\n\n"
                            f"{_bash_mismatch_line}\n\n"
                            f"RESPONSE FORMAT - Choose EXACTLY one:\n"
                            f"- If intent matches command: respond with ONLY the word "
                            f"'APPROVED' and nothing else.\n"
                            f"- If intent does NOT match command: your response MUST "
                            f"begin with 'BLOCKED:' followed by detailed feedback "
                            f"explaining the mismatch. Never omit the 'BLOCKED:' prefix "
                            f"on a rejection."
                        )

                        # Inject CSA danger_bash_warning into Stage 2 context if siblings active
                        _danger_warning = _csa_result.get("danger_bash_warning", "")
                        if _danger_warning:
                            bash_prompt = bash_prompt + f"\n\n{_danger_warning}"

                        # Issue #153 code review MUST-FIX 1(a): this gate
                        # never routes through _call_stage2_validation (the
                        # Write/Edit Stage 2 choke point), so it masks its
                        # own prompt here, at its own call site, using the
                        # SAME shared helper.
                        from .intent_validator import _mask_reviewer_prompt

                        bash_prompt = _mask_reviewer_prompt(
                            bash_prompt, db_path=DEFAULT_DB_PATH
                        )

                        _bash_degradation: Dict[str, Any] = {}
                        response, reviewer = resolve_and_call_with_reviewer(
                            hook_model=bash_config.get("hook_model", "auto"),
                            prompt=bash_prompt,
                            system_prompt=(
                                "You are validating Bash command intent alignment. "
                                "Respond APPROVED if intent matches, or begin your "
                                "response with 'BLOCKED:' followed by detailed "
                                "feedback if not."
                            ),
                            call_context="intent_validation",
                            max_thinking_tokens=2000,
                            _degradation=_bash_degradation,
                            _deadline=_gate_deadline,
                        )

                        if verdict_passes(response):
                            # Phase 2 passed
                            try:
                                record_activity_event(
                                    DEFAULT_DB_PATH, "DB", "green", _sid
                                )
                            except Exception:
                                pass
                            # Issue #131: a competitive verifier infra
                            # failure or single-model fallback still yielded
                            # APPROVED — record it as degraded, not silent.
                            _record_degraded_review_telemetry(
                                _bash_degradation, reviewer, _sid
                            )
                            # Issue #151: same observability requirement as
                            # the Write/Edit gate -- an approval via the
                            # reasoning-summary intent exception must be
                            # recorded, not silent. No-op for every
                            # non-exception-model approval.
                            _record_reasoning_summary_telemetry(
                                _bash_intent_source, reviewer, _sid
                            )
                            log_debug("hook", "Danger bash Phase 2: APPROVED")
                        elif not response:
                            # Issue #142: zero reviewers responded at all —
                            # relaying "" under intent_validation_dangerbash
                            # via format_reviewer_relay("") produced a blank
                            # block indistinguishable from a genuine intent
                            # mismatch. Build a pace-maker-authored
                            # explanation and record it under its own
                            # category instead.
                            _sid = session_id or "unknown"
                            _raw = build_reviewer_unavailable_message(_bash_degradation)
                            record_blockage(
                                db_path=DEFAULT_DB_PATH,
                                category="intent_validation_reviewer_unavailable",
                                reason=_raw,
                                hook_type="pre_tool_use",
                                session_id=_sid,
                                details={
                                    "tool": "Bash",
                                    "command": command[:500],
                                    "matched_rules": matched_ids,
                                    "reviewer": reviewer,
                                    # Issue #142 code-review follow-up
                                    # (item 4): persist WHY nobody
                                    # responded instead of leaving it
                                    # write-only on _bash_degradation.
                                    "failed_providers": _bash_degradation.get(
                                        "failed_providers", {}
                                    ),
                                    "zero_survivors": _bash_degradation.get(
                                        "zero_survivors", False
                                    ),
                                },
                            )
                            try:
                                record_activity_event(
                                    DEFAULT_DB_PATH, "DB", "red", _sid
                                )
                                _project_name = _resolve_project_name()
                                # Issue #142 code-review follow-up (item 3):
                                # prefix with the reviewer identity, matching
                                # the sibling genuine-mismatch branch below.
                                _gov_feedback = _raw
                                if reviewer:
                                    _gov_feedback = f"[{reviewer}] {_gov_feedback}"
                                record_governance_event(
                                    db_path=DEFAULT_DB_PATH,
                                    event_type="IV",
                                    project_name=_project_name,
                                    session_id=_sid,
                                    feedback_text=_gov_feedback[:1000],
                                )
                            except Exception:
                                # Telemetry recording is best-effort — the
                                # governance decision is already recorded
                                # via record_blockage() above, and activity/
                                # governance recording must never break the
                                # pre-tool hook (same pattern used by every
                                # other telemetry try/except in this
                                # function).
                                pass
                            return {
                                "decision": "block",
                                "reason": format_tag(_raw, "fail_closed_error"),
                            }
                        else:
                            # Phase 2 BLOCKED — intent mismatch
                            _sid = session_id or "unknown"
                            _feedback = response
                            _reviewer = reviewer
                            if _reviewer:
                                _feedback = f"[{_reviewer}] {_feedback}"
                            _bash_phase2_details = {
                                "tool": "Bash",
                                "command": command[:500],
                                "matched_rules": matched_ids,
                                "reviewer": reviewer,
                            }
                            if _bash_reasoning_summary_intent:
                                # Issue #151 code review L1: added ONLY for
                                # exception-model turns.
                                _bash_phase2_details["intent_source"] = (
                                    _bash_intent_source
                                )
                            record_blockage(
                                db_path=DEFAULT_DB_PATH,
                                category="intent_validation_dangerbash",
                                reason=response[:500],
                                hook_type="pre_tool_use",
                                session_id=_sid,
                                details=_bash_phase2_details,
                            )
                            try:
                                record_activity_event(
                                    DEFAULT_DB_PATH, "DB", "red", _sid
                                )
                                _project_name = _resolve_project_name()
                                record_governance_event(
                                    db_path=DEFAULT_DB_PATH,
                                    event_type="IV",
                                    project_name=_project_name,
                                    session_id=_sid,
                                    feedback_text=_feedback[:1000],
                                )
                            except Exception:
                                pass
                            return {
                                "decision": "block",
                                # B3 (issue #101 review): pace-maker's own
                                # framing head is wrapped in the plain tag
                                # (same channel as Phase 1's block, for
                                # consistency) with the reviewer-relay tag
                                # nested inside, wrapping only the
                                # reviewer's own text.
                                "reason": format_tag(
                                    f"⛔ Dangerous Bash command — intent mismatch\n\n"
                                    f"Matched danger rules: {matched_ids}\n"
                                    f"Reviewer: {reviewer}\n\n"
                                    f"{format_reviewer_relay(response[:500], reviewer)}",
                                    "danger_bash_block",
                                ),
                            }
            except Exception as e:
                log_error(
                    "hook",
                    "Danger bash validation: unexpected internal error"
                    + (
                        " — failing closed"
                        if _gate_committed
                        else " (fail-open, pre-match)"
                    ),
                    e,
                )
                log_debug(
                    "hook",
                    f"Danger bash validation traceback:\n{traceback.format_exc()}",
                )
                if _gate_committed:
                    # A dangerous command was already confirmed matched —
                    # fail CLOSED rather than letting it through unvalidated.
                    return {
                        "decision": "block",
                        "reason": _fail_closed_message(e),
                    }
                # Fail open on unexpected errors before a dangerous command
                # was even matched (e.g. a rules-loading hiccup) — don't
                # block all Bash commands for an infra issue unrelated to
                # this specific command's danger status.

            # Issue #102: route through _merge_csa_reminder like every other
            # return path in this function, so the CSA periodic sibling
            # reminder reaches Claude for Bash tool calls too (previously
            # this bare {"continue": True} bypassed it entirely).
            return _merge_csa_reminder({"continue": True}, _csa_result)

        # 2b. Only validate Write/Edit tools beyond this point
        if tool_name not in ["Write", "Edit"]:
            _periodic = _csa_result.get("periodic_reminder", "")
            if _periodic:
                return {
                    "continue": True,
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "additionalContext": _periodic,
                    },
                }
            return {"continue": True}

        # Skip if no file_path (e.g., some edge cases)
        if not file_path:
            return _merge_csa_reminder({"continue": True}, _csa_result)

        # 3. Load config
        config = load_config(DEFAULT_CONFIG_PATH)

        # Master switch - all features disabled
        if not config.get("enabled", True):
            return _merge_csa_reminder({"continue": True}, _csa_result)

        # Check if feature enabled
        if not config.get("intent_validation_enabled", False):
            return _merge_csa_reminder({"continue": True}, _csa_result)

        # Feature confirmed active for this tool call — an unexpected
        # exception from here on must fail CLOSED (see _gate_committed
        # docstring above).
        _gate_committed = True

        # 4. Check if source code file
        from . import extension_registry

        extensions = extension_registry.load_extensions(DEFAULT_EXTENSION_REGISTRY_PATH)
        is_source = extension_registry.is_source_code_file(file_path, extensions)

        if not is_source:
            return _merge_csa_reminder(
                {"continue": True}, _csa_result
            )  # Bypass non-source files

        # 5. Extract proposed code from tool_input
        #
        # Issue #153 follow-up (CHANGE 3): the target file, when it exists,
        # is now read EXACTLY ONCE per Edit review -- `_edit_file_read`
        # (an Edit-only local) is reused below by the surrounding-context
        # section builder, never re-read.
        _edit_file_read = None
        # Issue #154 item 1: which Write case actually occurred, feeding
        # the NEW FILE WARNING template section's conditional wording --
        # "new_file" (default, genuinely new), "existing_diff" (Write
        # over an existing file, diff fits), or "existing_full_content"
        # (Write over an existing file, diff exceeded the cap).
        _write_case = "new_file"
        if tool_name == "Write":
            _write_new_content = tool_input.get("content", "")
            if os.path.isfile(file_path):
                # Issue #153 follow-up (CHANGE 3): Write OVER an EXISTING
                # file now shows a unified diff of current-on-disk vs. the
                # new content, mirroring the Edit diff's own reasoning --
                # a genuinely NEW file (nothing on disk yet) is unaffected
                # (keeps showing `content` in full, exactly as before).
                from .intent_validator import _build_write_diff_section

                proposed_code, _write_diff_mode = _build_write_diff_section(
                    file_path,
                    _write_new_content,
                    _deadline=_gate_deadline,
                    _db_path=DEFAULT_DB_PATH,
                )
                _write_case = (
                    "existing_diff"
                    if _write_diff_mode == "diff"
                    else "existing_full_content"
                )
            else:
                proposed_code = _write_new_content
        elif tool_name == "Edit":
            # Issue #153 follow-up (CHANGE 3): render a REAL unified diff
            # of the file's full content BEFORE/AFTER the edit, replacing
            # the old OLD/NEW block-pair view -- a live false block
            # (deleting get_audit_logs) was misread by haiku as "replaced
            # by unrelated code" because the block-pair view hid that the
            # edit's first line was unchanged; a real diff shows that line
            # once, unmarked, as context.
            from .intent_validator import (
                _build_edit_diff_section,
                _read_edit_target_file,
            )

            _edit_old_string = tool_input.get("old_string", "")
            _edit_new_string = tool_input.get("new_string", "")
            _edit_replace_all = bool(tool_input.get("replace_all"))
            _edit_file_read = _read_edit_target_file(
                file_path,
                _edit_old_string,
                _edit_replace_all,
                _deadline=_gate_deadline,
                _db_path=DEFAULT_DB_PATH,
            )
            proposed_code = _build_edit_diff_section(
                file_path,
                _edit_old_string,
                _edit_new_string,
                _edit_replace_all,
                _deadline=_gate_deadline,
                _db_path=DEFAULT_DB_PATH,
                _precomputed=_edit_file_read,
            )
        else:
            return {"continue": True}

        # 5b. Story #155 -- tool first, transcript fallback. BEFORE any
        # transcript work: a stored declare_intent declaration for this
        # agent+file (consumed here), else the agent's chain when it is for
        # this same file; a chain for a DIFFERENT file is deleted. None =
        # nothing declared (or the kill switch is off / the store failed):
        # the existing transcript path below runs unchanged.
        _declared_intent = declaration_gate.resolve_declared_intent(
            hook_data, file_path, config
        )
        _declared_ctx = (hook_data, file_path, config)
        # The synthesized "INTENT: <change> in <file> -- goal: <goal>"
        # (+ "Test coverage: ...") IS the Stage 1/Stage 2 intent, so TDD
        # enforcement, Stage 2 review and telemetry run exactly as for a
        # text declaration.
        _declared_message = (
            declaration_gate.render_intent_message(_declared_intent.intent, file_path)
            if _declared_intent is not None
            else ""
        )

        # 6. Read last 2 messages for validation (text + tool_use are separate entries).
        # Issue #140 code-review findings 1-3 (structural PROSE-ONLY list)
        # and re-review finding 2 (single-parse performance): ONE call with
        # _with_prose=True returns BOTH the rendered-with-tools list (feeds
        # Stage 2's prompt, unchanged) AND a SEPARATE prose-only list (every
        # entry is structural text only, never rendered with tool
        # parameters -- used exclusively for Stage 1's fallback below).
        # Two independent calls here each re-read/re-parsed the WHOLE
        # transcript (~0.86s measured on a real 99.7MB file, ~2.8s
        # extrapolated to 324MB), eating into the gate's anchor budget for
        # no benefit -- both lists cover the identical last-N messages.
        # This is structural, not string-splitting: an INTENT:-looking
        # string embedded only in a Write's `content` or an Edit's
        # `old_string`/`new_string` is simply never present in the prose
        # list to begin with.
        if _declared_intent is not None:
            # Story #155: the declaration IS the intent -- no transcript
            # read at all. Stage 2's "recent context" is the declaration.
            messages = [_declared_message]
            _write_edit_prose_messages = [_declared_message]
        else:
            messages, _write_edit_prose_messages = get_last_n_messages_for_validation(
                transcript_path, n=2, _with_prose=True
            )

        # 6b. Fix 3: anchor the Stage-1 current-turn message on the requestId
        # group of the Write/Edit tool_use being validated. This reliably
        # captures a same-turn INTENT/skip declaration that a fixed n-back
        # window can miss (fragmented turn / interrupt), while never pulling in
        # a stale prior-turn INTENT. Empty string falls back to the n-back path.
        # _diagnostics surfaces attempt-count and real elapsed-seconds into
        # telemetry on give-up (issue #91 second pass observability
        # requirement) -- mirrors the danger-bash gate above.
        _write_edit_diagnostics: dict = {}
        current_message_override: Optional[str]
        if _declared_intent is not None:
            # Story #155 AC4: validated from the declaration, WITHOUT waiting
            # on the transcript anchor. No diagnostics are populated, so every
            # anchor-derived flag below reads as absent (the same state as a
            # genuine not_found, minus the block).
            current_message_override = _declared_message
        else:
            current_message_override = get_current_turn_message_for_validation(
                transcript_path,
                tool_input=tool_input,
                tool_name=tool_name,
                _diagnostics=_write_edit_diagnostics,
                # Clamp to the gate's remaining time (issue #108). This wait
                # is SEQUENTIAL with the review that follows, so its full 30s
                # cap plus the review budget overran the harness timeout.
                _max_wait_seconds=max(
                    0.0,
                    min(
                        PRE_TOOL_ANCHOR_CAP_SECONDS,
                        _gate_deadline - time.monotonic(),
                    ),
                ),
                # Issue #139 finding #4: shortens ONLY the "stale" path's
                # latency -- does not lower the not_found ceiling above.
                _stale_grace_seconds=_WRITE_EDIT_STALE_GRACE_SECONDS,
            )

        # Issue #140 code-review findings 1-3: when the anchor was FOUND
        # with a real intent in its own text, current_message_override was
        # the FULL RENDERED form (prose + tool params). Substitute the
        # structural prose-only text -- computed by transcript_reader from
        # the same merged["text"] that already satisfied the intent-marker
        # gate -- so Stage 1's file-mention/TDD-declaration/version-bump
        # checks (which operate on whatever current_message_override
        # carries) can never see rendered tool parameters.
        #
        # Issue #140 code-review re-review finding 1 (MEDIUM): substitute
        # ONLY when the diagnostic is a genuine non-empty string, not via
        # `.get(key, default)` -- a `dict.get` default is skipped whenever
        # the KEY IS PRESENT, even if its value is `None` or `""`. Since
        # `current_message_override` is truthy here (real intent WAS
        # found), a `None`/empty `anchor_prose_text` would be a defect
        # (transcript_reader now sets it unconditionally before any
        # exception-prone computation -- see the anchor-shape comments in
        # transcript_reader.py), and falling BACK to the already-truthy
        # rendered override is strictly safer than ever assigning `None`
        # onto a currently-truthy value.
        if current_message_override:
            _anchor_prose = _write_edit_diagnostics.get("anchor_prose_text")
            if isinstance(_anchor_prose, str) and _anchor_prose:
                current_message_override = _anchor_prose

        if current_message_override is None:
            # Bug #83 follow-up (v2.33.2): mirror the danger-bash gate's
            # fail-CLOSED handling (see the `_bash_outcome` not_found
            # `else:` branch above, issue #93) instead of failing open.
            # Issue #139 (the issue #93 deadlock's Write/Edit twin): this
            # gate now ALSO consumes a "stale" outcome exactly like the
            # danger-bash gate does -- the previous "NOTE: ... does NOT
            # consume a stale match, intentionally out of scope" comment
            # here was itself the bug. get_current_turn_message_for_validation
            # returns None for BOTH "not_found" and "stale"
            # (_write_edit_diagnostics["outcome"] disambiguates them). The
            # not_found block message below instructs a byte-identical
            # re-issue; that re-issue's own tool_use is frequently STILL not
            # flushed within the window, but the ORIGINAL attempt is now one
            # turn back, which resolves to "stale" -- not "found". Blocking
            # unconditionally on every None (as before) meant that recovery
            # path could never succeed, producing an infinite deadlock (live
            # evidence: 5 consecutive 30s blocks on a one-line Edit).
            _write_edit_outcome = _write_edit_diagnostics.get("outcome")
            if _write_edit_outcome == "stale":
                # stale_text is already INTENT-gated on the turn's own TEXT
                # by transcript_reader (never leaks a sibling tool_use's
                # rendered content) -- see _find_turn_matching_tool_input's
                # "stale" branch. The match itself is byte-identical
                # file_path+old_string+new_string/content (+replace_all for
                # Edit -- issue #139 finding #1), so this is provably a
                # re-issue of exactly this edit; falls through to normal
                # Stage 1/2 validation below rather than the not-ready block.
                current_message_override = _write_edit_diagnostics.get("stale_text", "")
                if current_message_override:
                    # Issue #140 code-review findings 1-3: substitute the
                    # structural prose-only text for the rendered
                    # stale_text -- same rationale as the found-path
                    # substitution above. The truthy check above already
                    # proved this turn's TEXT carries a real intent marker
                    # (stale_text is only ever non-empty when that gate
                    # passed), so anchor_prose_text is guaranteed to carry
                    # the same declaration, minus the rendered tool params.
                    #
                    # Issue #140 code-review re-review finding 1 (MEDIUM):
                    # substitute ONLY when the diagnostic is a genuine
                    # non-empty string (same `isinstance` guard as the
                    # found-path substitution above), never via
                    # `.get(key, default)`. A `None`/falsy result here
                    # would leave `current_message_override` falsy, which
                    # would then fall through to validate_intent_and_code's
                    # n-back RESCUE fallback -- violating the stale path's
                    # documented ANCHOR-ONLY invariant (issue #93/#139),
                    # not merely producing a wrong value.
                    _anchor_prose = _write_edit_diagnostics.get("anchor_prose_text")
                    if isinstance(_anchor_prose, str) and _anchor_prose:
                        current_message_override = _anchor_prose
                else:
                    # Issue #139 code-review finding #2: an empty
                    # stale_text must NOT fall through to
                    # intent_validator.validate_intent_and_code's n-back
                    # RESCUE fallback (`current_message_override or
                    # extract_current_assistant_message(stage1_fallback_
                    # messages, ...)`). That fallback additionally checks
                    # the immediately-preceding message for an INTENT that
                    # merely names the file, with no relation to the
                    # stale-matched turn at all. The stale path must be
                    # ANCHOR-ONLY, exactly like the danger-bash gate's Phase
                    # 1 (which checks its anchor text directly, with no
                    # n-back fallback at all). Overriding the PROSE-ONLY
                    # n-back list (not `messages`, which still feeds Stage
                    # 2's prompt) to [] makes intent_validator's Stage-1
                    # fallback resolve to "" too (identical to a genuinely
                    # empty transcript), so Stage 1 blocks on the ordinary
                    # "no INTENT" reason without duplicating that block's
                    # construction here.
                    _write_edit_prose_messages = []
                log_info(
                    "hook",
                    "Intent validation: accepted STALE anchor turn "
                    f"(byte-identical re-issue, outcome=stale_accepted) for "
                    f"{tool_name} on {file_path} "
                    f"(attempts={_write_edit_diagnostics.get('attempts')}, "
                    "elapsed_seconds="
                    f"{_write_edit_diagnostics.get('elapsed_seconds')})",
                )
            else:
                # Failing open here meant intent validation enforced
                # NOTHING for any Write/Edit that raced the transcript
                # flush — confirmed live via this exact telemetry category
                # plus a raced edit that passed unvalidated. Fail-closed is
                # safe because the block is transient: the agent re-issues
                # the IDENTICAL tool call, that turn is then flushed (or, if
                # not, the re-issue now resolves to "stale" per the branch
                # above), and validation proceeds normally. Not wrapped in
                # _merge_csa_reminder, matching the danger-bash template
                # exactly.
                _sid = session_id or "unknown"
                _is_subagent = "/agent-" in (transcript_path or "")
                log_warning(
                    "hook",
                    "Intent validation: transcript not yet flushed for current "
                    f"{tool_name} tool_use on {file_path} "
                    f"(subagent={_is_subagent}). Failing closed — re-issue.",
                )
                record_blockage(
                    db_path=DEFAULT_DB_PATH,
                    category="intent_validation_deferred",
                    reason=(
                        "Transcript not yet flushed: current tool_use absent. "
                        "Failing closed — re-issue (TOCTOU race guard)."
                    ),
                    hook_type="pre_tool_use",
                    session_id=_sid,
                    details={
                        "tool": tool_name,
                        "file_path": file_path,
                        "subagent": _is_subagent,
                        "attempts": _write_edit_diagnostics.get("attempts"),
                        "elapsed_seconds": _write_edit_diagnostics.get(
                            "elapsed_seconds"
                        ),
                        "outcome": _write_edit_outcome,
                    },
                )
                try:
                    record_activity_event(DEFAULT_DB_PATH, "IV", "red", _sid)
                    _project_name = _resolve_project_name()
                    record_governance_event(
                        db_path=DEFAULT_DB_PATH,
                        event_type="IV",
                        project_name=_project_name,
                        session_id=_sid,
                        feedback_text=(
                            f"[RegEx] Transcript-flush race: current {tool_name} "
                            f"tool_use on {file_path} not yet flushed. Failing "
                            f"closed — re-issue the identical tool call."
                        ),
                    )
                except Exception:
                    pass
                _deferred_body = (
                    "⛔ Intent validation deferred — transcript timing race "
                    "(not a rejection)\n\n"
                    f"The current {tool_name} tool call has not yet been "
                    "flushed to the conversation transcript. This is a "
                    "TRANSIENT TIMING ISSUE, not a rejection of your intent "
                    "or code.\n\n"
                    f"RE-ISSUE THE IDENTICAL {tool_name} TOOL CALL with the "
                    "SAME INTENT: declaration in the same message. The "
                    "re-issue will find the now-flushed turn and validate "
                    "normally.\n\n"
                    "IMPORTANT: the file_path and content must be IDENTICAL "
                    "to this attempt — the validator binds to the exact "
                    "tool call content, and a different edit will not match."
                )
                if declaration_gate.intent_declaration_tool_enabled(config):
                    # Bug #159: this block only happens when no tool
                    # declaration existed (the gate reads those BEFORE the
                    # transcript), and a declaration removes the transcript
                    # wait altogether -- so point at it here too. Inside the
                    # one pace-maker tag (the whole message is ours).
                    from .intent_validator import build_declare_intent_deferred_hint

                    _deferred_body += "\n\n" + build_declare_intent_deferred_hint(
                        file_path
                    )
                return {
                    "decision": "block",
                    # Bug #163: leads with the re-declare note when a rejected
                    # sibling just consumed this agent's declaration.
                    "reason": declaration_gate.lead_with_rejected_sibling_note(
                        format_tag(_deferred_body, "intent_validation_deferred"),
                        hook_data,
                        file_path,
                        config,
                    ),
                }

        # Story #155 AC8: the transcript fallback additionally accepts a
        # declare_intent tool_use for THIS file inside the anchored turn (the
        # declaration and the edit sent in the same message, with no stored
        # declaration/chain). Only when the anchored turn has no INTENT: text
        # of its own (override empty) and the kill switch is on.
        if _declared_intent is None:
            _declared_intent = declaration_gate.same_message_fallback(
                current_message_override,
                _write_edit_diagnostics,
                file_path,
                hook_data,
                config,
            )
            if _declared_intent is not None:
                _declared_message = declaration_gate.render_intent_message(
                    _declared_intent.intent, file_path
                )
                current_message_override = _declared_message
                # Stage 2 must see the declared change/goal too (the tool
                # call itself renders only its file_path in `messages`).
                messages = list(messages) + [_declared_message]
                # A declaration WAS made -- never tell the agent its message
                # "had NO visible text" on a Stage-1 block below.
                _write_edit_diagnostics["anchor_has_visible_text"] = True

        # Activity events: IV/TD/CC blue (validation in-progress) — settings-aware
        try:
            _sid = session_id or "unknown"
            _tdd_on = config.get("tdd_enabled", True)
            record_activity_event(DEFAULT_DB_PATH, "IV", "blue", _sid)
            record_activity_event(
                DEFAULT_DB_PATH, "TD", "blue" if _tdd_on else "green", _sid
            )
            record_activity_event(DEFAULT_DB_PATH, "CC", "blue", _sid)
        except Exception:
            pass  # Activity recording must never break pre-tool hook

        # 7. Call unified validation via SDK
        from . import intent_validator

        # Issue #141, broadened by issue #148: the anchored turn had NO
        # visible text -- whether it had a `thinking` block with content,
        # an empty `thinking` block, or no `thinking` block at all. `is
        # False` (not falsy) requires an EXPLICIT False from
        # transcript_reader, since a not_found/no-anchor case leaves the
        # diagnostics key absent (.get returns None), which must never be
        # misread as "no visible text". Issue #141 originally also
        # required `anchor_has_thinking` to be truthy, which missed the
        # empty-thinking and no-thinking-at-all shapes -- see issue #148.
        _write_edit_no_visible_text = (
            _write_edit_diagnostics.get("anchor_has_visible_text") is False
        )

        # Issue #151: reasoning-summary intent exception, configured models
        # only. Gated on the anchored turn's own assistant message.model
        # (populated by transcript_reader on both the found and stale
        # branches) being present in config's reasoning_summary_intent_models
        # list (default ["claude-opus-5-5"]). anchor_visible_text is read
        # UNCONDITIONALLY from anchor_prose_text (never gated on an INTENT:
        # marker, unlike current_message_override above) -- the exception
        # path needs the turn's raw text regardless of marker presence.
        _write_edit_anchor_model = _write_edit_diagnostics.get("anchor_model")
        _write_edit_anchor_visible_text = (
            _write_edit_diagnostics.get("anchor_prose_text") or ""
        )
        _write_edit_anchor_reasoning_summary = (
            _write_edit_diagnostics.get("anchor_reasoning_summary") or ""
        )
        # Issue #151 code review H1: never trust the raw config value's
        # shape -- normalize it first (see the function's docstring for
        # the TypeError/substring-match failure modes this closes).
        _reasoning_summary_models = _normalize_reasoning_summary_intent_models(
            config.get(
                "reasoning_summary_intent_models",
                DEFAULT_CONFIG.get("reasoning_summary_intent_models", []),
            )
        )
        _write_edit_reasoning_summary_intent = (
            bool(_write_edit_anchor_model)
            and _write_edit_anchor_model in _reasoning_summary_models
        )

        # Issue #151 code review H2/M1: the SHARED decision function (also
        # used by the danger-bash gate below) resolves whether this turn
        # takes the STRICT path (declaration already present, or nothing
        # to relax against) or the RELAXED path.
        from .intent_validator import resolve_reasoning_summary_intent_source

        # Issue #151 live-replay follow-up (round 3, CHANGE 2): read the
        # raw anchor-relative prior-turns data out BEFORE resolving intent
        # source, so the resolver can fall back to the immediately
        # preceding turn's own reasoning summary when the anchor itself
        # has neither visible text nor a summary.
        _write_edit_raw_recent_context = _write_edit_diagnostics.get(
            "anchor_recent_context"
        )
        if not isinstance(_write_edit_raw_recent_context, list):
            _write_edit_raw_recent_context = []

        _write_edit_intent_source, _write_edit_relaxed_text = (
            resolve_reasoning_summary_intent_source(
                _write_edit_reasoning_summary_intent,
                _write_edit_anchor_visible_text,
                _write_edit_anchor_reasoning_summary,
                recent_context=_write_edit_raw_recent_context,
                # Issue #154 item 2 (decided): the CHANGE 2 prior-turn
                # fallback is Write/Edit-ONLY -- explicit here, not an
                # implicit tool-name check inside the shared resolver
                # (the danger-bash gate passes False explicitly instead).
                allow_prior_turn_fallback=True,
            )
        )
        if _declared_intent is not None:
            # Story #155: a tool-sourced declaration always takes the STRICT
            # path (Stage-1 regex incl. TDD, then Stage 2), even for an
            # exception-listed model whose anchored turn would otherwise
            # have been relaxed -- tagged so telemetry can tell which path
            # validated the edit.
            _write_edit_relaxed_text = None
            _write_edit_intent_source = _declared_intent.source

        # Issue #151 live-test follow-up: the relaxed path is anchor-only
        # by design (#93/#139) -- it never saw the PRIOR turns' reasoning,
        # so a terse current-turn intent (e.g. "Now the SSH ... test
        # expectations.") that continues an earlier stated plan was
        # false-rejected by a weak verifier at CHECK 0.
        #
        # Issue #151 live-test follow-up, round 2 (performance fix): the
        # FIRST fix above re-parsed the ENTIRE transcript a SECOND time via
        # get_last_n_messages_for_validation(n=4, ...) -- measured 0.824s on
        # top of the pre-existing 1.216s n=2 call above, on a real 99.7MB
        # transcript, eating the shared _gate_deadline before Stage 2 could
        # even run. transcript_reader now computes this ANCHOR-RELATIVE,
        # inside get_current_turn_message_for_validation's call above
        # (_find_turn_matching_tool_input -> _build_prior_turns_context),
        # reusing the SAME fixed-cost tail window already read for anchor
        # resolution -- zero additional file I/O. Just read it out of the
        # diagnostics dict already populated above; no second transcript
        # read at all. Measured on an equivalent ~30MB synthetic transcript:
        # ~5ms total (vs. the ~2s the removed second call cost on a 99.7MB
        # transcript). This also fixes the stale-path bug the first design
        # had: context is now always relative to whichever turn is
        # CURRENTLY anchored (found or stale), so the anchor's own text can
        # never leak into its own "recent context" -- the old design's
        # positional `prose[:-1]` slice (relative to the whole transcript)
        # could surface the anchor's own summary once a stale re-issue's
        # thinking was flushed.
        # Issue #151 live-replay follow-up (round 3, CHANGE 1): three live
        # false blocks showed a DETAILED, file-naming current intent being
        # judged against an EARLIER turn's stale plan surfaced via RECENT
        # CONTEXT -- the prompt's own wording guards were not enough.
        # RECENT CONTEXT is now shown ONLY when the CURRENT (or, for
        # CHANGE 2, the prior-turn-sourced) intent text is "terse" (see
        # _should_include_recent_context) -- a CODE decision, not left to
        # a weak reviewer's own judgment.
        _write_edit_recent_context: Optional[List["tuple[str, str]"]] = None
        _write_edit_recent_context_included = False
        if _write_edit_relaxed_text is not None:
            from .intent_validator import _should_include_recent_context

            _write_edit_candidate_context = _write_edit_raw_recent_context
            if (
                _write_edit_intent_source == "prior_reasoning_summary"
                and _write_edit_candidate_context
            ):
                # CHANGE 2: the LAST prior turn was already consumed AS the
                # intent itself (resolve_reasoning_summary_intent_source's
                # "prior_reasoning_summary" case) -- never show it a SECOND
                # time inside its own RECENT CONTEXT section.
                _write_edit_candidate_context = _write_edit_candidate_context[:-1]
            if _write_edit_candidate_context and _should_include_recent_context(
                _write_edit_relaxed_text, file_path
            ):
                _write_edit_recent_context = _write_edit_candidate_context
                _write_edit_recent_context_included = True

        # Issue #153: for Edit reviews ONLY (both the normal/strict and the
        # #151 relaxed Stage 2 paths) -- surface the CURRENT on-disk file
        # content around old_string, and the OTHER Write/Edit calls in the
        # same anchored turn, so Stage 2 can tell "this Edit changes part
        # of a longer function" from "this function is incomplete" (the
        # mock_remove_with_transaction false-BLOCK), and see a sibling
        # edit that completes a fragment the current edit leaves seemingly
        # unfinished (the OIDC split-multi-edit false-BLOCK, where the
        # file on disk still has the OLD tail at validation time -- only
        # the sibling turn record, not on-disk context, can help there).
        # Write/Bash get neither section (both default to "").
        _write_edit_surrounding_context_section = ""
        _write_edit_sibling_edits_section = ""
        if tool_name == "Edit":
            from .intent_validator import (
                _build_edit_surrounding_context_section,
                _build_sibling_edits_section,
            )

            # Issue #153 code review MUST-FIX 1(b): pass hook.py's OWN
            # (already patchable-for-tests) DEFAULT_DB_PATH explicitly, so
            # the secret-like-content skip check can consult the stored
            # SECRET_FILE values. This is the SAME attribute the
            # `_guard_production_db` autouse test fixture already guards.
            _write_edit_surrounding_context_section = _build_edit_surrounding_context_section(
                file_path,
                tool_input.get("old_string", ""),
                bool(tool_input.get("replace_all")),
                _deadline=_gate_deadline,
                _db_path=DEFAULT_DB_PATH,
                # Issue #153 follow-up (CHANGE 3): reuse the SAME
                # single file read step 5 already performed for the
                # diff -- never read the target file twice.
                _precomputed=_edit_file_read,
            )
            _write_edit_sibling_edits_section = _build_sibling_edits_section(
                _write_edit_diagnostics.get("anchor_sibling_edits")
            )

        result = intent_validator.validate_intent_and_code(
            messages=messages,
            code=proposed_code,
            file_path=file_path,
            tool_name=tool_name,
            hook_model=config.get("hook_model", "auto"),
            current_message_override=current_message_override,
            _deadline=_gate_deadline,
            no_visible_text=_write_edit_no_visible_text,
            stage1_fallback_messages=_write_edit_prose_messages,
            reasoning_summary_relaxed_text=_write_edit_relaxed_text,
            reasoning_summary_intent_source=_write_edit_intent_source,
            reasoning_summary_recent_context=_write_edit_recent_context,
            edit_surrounding_context_section=_write_edit_surrounding_context_section,
            edit_sibling_edits_section=_write_edit_sibling_edits_section,
            # Issue #153 code review MUST-FIX 1(a): masks ALL stored
            # secrets in the WHOLE Stage 2 prompt right before it reaches
            # the reviewer provider -- see _mask_reviewer_prompt's
            # docstring for the full rationale and the test-isolation
            # contract (None would silently skip masking; hook.py always
            # supplies its own real, patchable DEFAULT_DB_PATH here).
            stage2_db_path=DEFAULT_DB_PATH,
            # Issue #154 item 1: picks the NEW FILE WARNING section's
            # matching wording variant ("new_file" for Edit and every
            # genuinely-new Write; "existing_diff"/"existing_full_content"
            # for a Write over an existing file -- see step 5 above).
            write_case=_write_case,
            # Story #155 AC11: Stage-1 block messages prefer the tool (and
            # keep the INTENT: fallback) only while the tool path is on.
            declare_intent_hint=declaration_gate.intent_declaration_tool_enabled(
                config
            ),
            # Review M2: a tool-sourced intent satisfies TDD ONLY through its
            # structured test_coverage (None = text declaration, regex scan).
            tool_declared_tdd=(
                bool(_declared_intent.intent.test_coverage.strip())
                if _declared_intent is not None
                else None
            ),
            # Bug #159: a Stage 2 rejection of a tool/chain-declared intent
            # gets only the "declaration consumed, declare again" note (the
            # agent already used the tool); every other Stage 2 rejection
            # gets the review hint, while the tool path is on.
            intent_from_tool=_declared_intent is not None,
        )

        # Story #155: apply the verdict to the agent's chain (approved
        # tool-sourced intent -> chain; any rejection -> chain deleted) and
        # record which path validated this edit.
        declaration_gate.record_outcome(
            hook_data,
            file_path,
            _declared_intent,
            bool(result.get("approved", False)),
            config,
        )
        if (
            _declared_intent is None
            and not result.get("approved", False)
            and result.get("reviewer") == "RegEx"
        ):
            # Bug #163: a Stage 1 "no declaration" block right after a
            # rejected sibling consumed this agent's declaration for this
            # file leads with the re-declare note. Message only.
            result["feedback"] = declaration_gate.lead_with_rejected_sibling_note(
                result.get("feedback", "Validation failed"),
                hook_data,
                file_path,
                config,
            )
        log_info(
            "hook",
            "Intent validation: "
            f"{'approved' if result.get('approved', False) else 'rejected'} "
            f"{tool_name} on {file_path} via "
            f"{_declared_intent.source if _declared_intent else 'transcript'}",
        )

        # 8. Return result
        if result.get("approved", False):
            # Activity events: IV/TD/CC green (all checks passed)
            try:
                _sid = session_id or "unknown"
                record_activity_event(DEFAULT_DB_PATH, "IV", "green", _sid)
                record_activity_event(DEFAULT_DB_PATH, "TD", "green", _sid)
                record_activity_event(DEFAULT_DB_PATH, "CC", "green", _sid)
                record_activity_event(DEFAULT_DB_PATH, "BG", "green", _sid)
            except Exception:
                pass  # Activity recording must never break pre-tool hook
            # Issue #131: a competitive verifier infra failure or
            # single-model fallback still yielded APPROVED — record it as
            # degraded, not silent.
            _record_degraded_review_telemetry(
                result.get("degradation"),
                result.get("reviewer", "unknown"),
                session_id or "unknown",
            )
            # Issue #151: an approval via the reasoning-summary intent
            # exception must be observable, not silent (the story's own
            # telemetry requirement) -- records an RS activity + governance
            # event. No-op (via the intent_source guard inside the helper)
            # for every non-exception-model approval.
            _record_reasoning_summary_telemetry(
                result.get("intent_source"),
                result.get("reviewer", "unknown"),
                session_id or "unknown",
            )
            return _merge_csa_reminder({"continue": True}, _csa_result)
        else:
            # AC4: Record blockage for intent validation failure
            # Determine category based on failure type
            if result.get("tdd_failure", False):
                category = "intent_validation_tdd"
            elif result.get("bug_failure", False):
                category = "intent_validation_bug"
            elif result.get("reviewer_unavailable_failure", False):
                # Issue #142: zero reviewers responded — a reviewer
                # infrastructure failure, never a genuine clean-code/bug
                # rejection or a missing-INTENT block. Checked BEFORE
                # clean_code_failure so it can never be mislabeled as a
                # clean-code rejection.
                category = "intent_validation_reviewer_unavailable"
            elif result.get("clean_code_failure", False):
                category = "intent_validation_cleancode"
            else:
                category = "intent_validation"

            _write_edit_details = {
                "tool": tool_name,
                "file_path": file_path,
                "reviewer": result.get("reviewer", "unknown"),
                # Issue #141 code-review follow-up (item 3), key
                # renamed by issue #148: lets usage.db / the
                # claude-usage monitor distinguish a no-visible-text
                # block from an ordinary block.
                "no_visible_text": _write_edit_no_visible_text,
                # Issue #142 code-review follow-up (item 4): persist WHY
                # nobody responded on a reviewer_unavailable_failure
                # block instead of leaving it write-only on the
                # in-memory degradation dict. A no-op for every other
                # category (empty dict/False).
                "failed_providers": result.get("degradation", {}).get(
                    "failed_providers", {}
                ),
                "zero_survivors": result.get("degradation", {}).get(
                    "zero_survivors", False
                ),
            }
            if _write_edit_reasoning_summary_intent:
                # Issue #151 code review L1: "intent_source" is added ONLY
                # for exception-model turns ("declaration" |
                # "reasoning_summary" | "visible_text" |
                # "prior_reasoning_summary") -- a non-exception model's
                # blockage details must stay byte-identical (no
                # "intent_source": null key at all).
                _write_edit_details["intent_source"] = result.get("intent_source")
                # Issue #151 live-replay follow-up (round 3, CHANGE 1):
                # record whether RECENT CONTEXT was actually shown to the
                # reviewer, so the terse-gating decision is observable in
                # telemetry (not just inferred from prompt contents).
                _write_edit_details["recent_context_included"] = (
                    _write_edit_recent_context_included
                )
            if _declared_intent is not None:
                # Story #155: which tool path validated this edit
                # ("declare_intent" | "declare_intent_chain"). Absent for a
                # transcript-sourced block, so those details stay
                # byte-identical to pre-#155.
                _write_edit_details["intent_source"] = _declared_intent.source
            record_blockage(
                db_path=DEFAULT_DB_PATH,
                category=category,
                reason=result.get("feedback", "Validation failed"),
                hook_type="pre_tool_use",
                session_id=session_id or "unknown",
                details=_write_edit_details,
            )

            # Record governance event for live event feed
            try:
                _category_to_event_type = {
                    "intent_validation": "IV",
                    "intent_validation_tdd": "TD",
                    "intent_validation_cleancode": "CC",
                    "intent_validation_bug": "BG",
                }
                _event_type = _category_to_event_type.get(category, "IV")
                _project_name = _resolve_project_name()
                # B2 (issue #101 review): governance-event feedback_text
                # must stay UNTAGGED/raw — the pace-maker provenance tag
                # (and reviewer-relay wrapper) belongs only on the
                # Claude-facing "feedback"/reason, never here. Falls back
                # to "feedback" for any older/partial result dict that
                # lacks "raw_feedback".
                _feedback = result.get(
                    "raw_feedback", result.get("feedback", "Validation failed")
                )
                _reviewer = result.get("reviewer", "")
                if _reviewer:
                    _feedback = f"[{_reviewer}] {_feedback}"
                record_governance_event(
                    db_path=DEFAULT_DB_PATH,
                    event_type=_event_type,
                    project_name=_project_name,
                    session_id=session_id or "unknown",
                    feedback_text=_feedback,
                )
            except Exception:
                pass  # Governance recording must never break pre-tool hook

            # Activity events: specific failed check is red, others green
            # Settings-aware: TD shows green when tdd_enabled is off
            # CC has no separate toggle — always active when IV is on
            try:
                _sid = session_id or "unknown"
                _tdd_on = config.get("tdd_enabled", True)
                _iv_status = (
                    "red"
                    if category
                    in (
                        "intent_validation",
                        # Issue #142 code-review follow-up (item 1): a
                        # zero-survivor reviewer-infrastructure failure is
                        # a real block — IV must not show green just
                        # because no *specific* check (TD/CC/BG) fired.
                        "intent_validation_reviewer_unavailable",
                    )
                    else "green"
                )
                _td_status = (
                    "red"
                    if category == "intent_validation_tdd" and _tdd_on
                    else "green"
                )
                _cc_status = (
                    "red" if category == "intent_validation_cleancode" else "green"
                )
                _bg_status = "red" if category == "intent_validation_bug" else "green"
                record_activity_event(DEFAULT_DB_PATH, "IV", _iv_status, _sid)
                record_activity_event(DEFAULT_DB_PATH, "TD", _td_status, _sid)
                record_activity_event(DEFAULT_DB_PATH, "CC", _cc_status, _sid)
                record_activity_event(DEFAULT_DB_PATH, "BG", _bg_status, _sid)
            except Exception:
                pass  # Activity recording must never break pre-tool hook

            return _merge_csa_reminder(
                {
                    "decision": "block",
                    "reason": result.get("feedback", "Validation failed"),
                },
                _csa_result,
            )

    except Exception as e:
        if _gate_committed:
            # The Write/Edit intent-validation gate was already confirmed
            # active for this tool call — an unvalidated edit is worse than
            # a blocked one, so fail CLOSED instead of silently allowing it
            # through (Messi Anti-Fallback).
            log_error(
                "hook",
                "Pre-tool hook: unexpected internal error during Write/Edit "
                "intent validation — failing closed",
                e,
            )
            log_debug("hook", f"Pre-tool hook traceback:\n{traceback.format_exc()}")
            print(
                f"[PACE-MAKER ERROR] Pre-tool hook (fail-closed): {e}",
                file=sys.stderr,
            )
            # Story #155 (review L2): like any rejection, an internal-error
            # block ends the agent's chain when a tool/chain intent was in
            # use, so the retry needs a fresh declaration. Guarded: this
            # handler must always get to return its block.
            if _declared_intent is not None and _declared_ctx is not None:
                try:
                    declaration_gate.record_outcome(
                        _declared_ctx[0],
                        _declared_ctx[1],
                        _declared_intent,
                        False,
                        _declared_ctx[2],
                    )
                except Exception as outcome_exc:
                    log_warning(
                        "hook",
                        "could not end the declaration chain after an "
                        "internal error (block unaffected)",
                        outcome_exc,
                    )
            # Merge CSA reminder for consistency with every other Write/Edit
            # return path in this function (see the "not approved" branch
            # above) — a fail-closed block is still a reachable return path
            # that CSA wiring must cover.
            return _merge_csa_reminder(
                {
                    "decision": "block",
                    "reason": _fail_closed_message(e),
                },
                _csa_result,
            )
        # Graceful degradation - log error and allow (exception occurred
        # before the gate was confirmed active for this tool call, or for a
        # non-gated tool type)
        print(f"[PACE-MAKER ERROR] Pre-tool hook: {e}", file=sys.stderr)
        return _merge_csa_reminder({"continue": True}, _csa_result)


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
