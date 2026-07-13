#!/usr/bin/env python3
"""
Transcript reader functions for extracting user messages.

This module provides functions to extract user messages from JSONL transcripts
for use in intent validation context building.
"""

import json
import re
import time
from typing import Any, Dict, List, Optional, Union

from .logger import log_warning

MAX_MESSAGE_LENGTH = 10000


def get_all_user_messages(transcript_path: str) -> List[str]:
    """
    Extract ALL user messages from JSONL transcript.

    This function provides complete user intent by extracting all user messages
    from the entire conversation, ensuring no context is lost.

    Args:
        transcript_path: Path to JSONL transcript file

    Returns:
        List of all user message texts in chronological order
    """
    try:
        all_user_messages = []

        with open(transcript_path, "r") as f:
            for line in f:
                entry = json.loads(line)

                # Extract message content
                message = entry.get("message", {})
                role = message.get("role")

                # Only process user messages
                if role != "user":
                    continue

                content = message.get("content", [])

                # Extract text from content blocks
                text_parts = []
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                elif isinstance(content, str):
                    text_parts.append(content)

                if text_parts:
                    message_text = "\n".join(text_parts)
                    all_user_messages.append(message_text)

        return all_user_messages

    except Exception as e:
        log_warning("transcript_reader", "Failed to extract user messages", e)
        return []


def get_last_n_assistant_messages(transcript_path: str, n: int = 5) -> List[str]:
    """
    Extract last N assistant messages from JSONL transcript.

    This function provides the "recent assistant responses" to show what
    Claude actually did in the conversation.

    Args:
        transcript_path: Path to JSONL transcript file
        n: Number of assistant messages to extract (default: 5)

    Returns:
        List of assistant message texts (last N only, most recent last)
    """
    try:
        all_assistant_messages = []

        with open(transcript_path, "r") as f:
            for line in f:
                entry = json.loads(line)

                # Extract message content
                message = entry.get("message", {})
                role = message.get("role")

                # Only process assistant messages
                if role != "assistant":
                    continue

                content = message.get("content", [])

                # Extract text from content blocks
                text_parts = []
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                elif isinstance(content, str):
                    text_parts.append(content)

                if text_parts:
                    message_text = "\n".join(text_parts)
                    all_assistant_messages.append(message_text)

        # Return last N messages
        if len(all_assistant_messages) >= n:
            return all_assistant_messages[-n:]
        else:
            return all_assistant_messages

    except Exception as e:
        log_warning("transcript_reader", "Failed to extract assistant messages", e)
        return []


def _extract_message_parts(content: Union[list, str]) -> dict:
    """Extract text and tool parts from message content."""
    text_parts = []
    tools = []

    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    tools.append(
                        {
                            "id": block.get("id"),
                            "name": block.get("name", "unknown"),
                            "input": block.get("input", {}),
                        }
                    )
    elif isinstance(content, str):
        text_parts.append(content)

    return {"text": "\n".join(text_parts) if text_parts else "", "tools": tools}


def _format_message_with_tools(msg: dict) -> str:
    """Format a message including its tool information."""
    parts = [msg["text"]] if msg["text"] else []
    for tool in msg["tools"]:
        tool_str = f"[TOOL: {tool['name']}]\n"
        inp = tool["input"]
        for key in ["file_path", "content", "old_string", "new_string"]:
            if key in inp:
                tool_str += f"{key}: {inp[key]}\n"
        parts.append(tool_str)
    return "\n".join(parts)


def get_last_n_messages_for_validation(transcript_path: str, n: int = 5) -> List[str]:
    """
    Extract last N assistant messages for pre-tool validation context.

    Special formatting:
    - Messages 1 to N-1: Text only (tool parameters/code stripped)
    - Message N (most recent): Full content including tool parameters

    Args:
        transcript_path: Path to JSONL transcript file
        n: Number of messages to extract (default: 5)

    Returns:
        List of formatted message texts
    """
    from .logger import log_debug

    try:
        log_debug("transcript_reader", f"Reading transcript: {transcript_path}")

        # Group JSONL entries by requestId so that thinking, text, and
        # tool_use blocks from the same Claude turn are combined into one
        # logical message.  Without this, n=2 can miss the INTENT: text
        # when it's in a separate JSONL entry from the tool_use.
        grouped_order = []
        grouped = {}

        with open(transcript_path, "r") as f:
            for line in f:
                entry = json.loads(line)
                message = entry.get("message", {})

                if message.get("role") != "assistant":
                    continue

                content = message.get("content", [])
                msg_parts = _extract_message_parts(content)

                request_id = entry.get("requestId")
                if not request_id:
                    standalone_key = id(msg_parts)
                    grouped_order.append(standalone_key)
                    grouped[standalone_key] = msg_parts
                    continue

                if request_id in grouped:
                    existing = grouped[request_id]
                    if msg_parts["text"]:
                        if existing["text"]:
                            existing["text"] += "\n" + msg_parts["text"]
                        else:
                            existing["text"] = msg_parts["text"]
                    existing["tools"].extend(msg_parts["tools"])
                else:
                    grouped_order.append(request_id)
                    grouped[request_id] = msg_parts

        messages = [grouped[key] for key in grouped_order]

        log_debug(
            "transcript_reader",
            f"Total assistant turns found: {len(messages)}",
        )

        # Get last N messages
        recent = messages[-n:] if len(messages) >= n else messages
        log_debug("transcript_reader", f"Extracting last {len(recent)} messages")

        # Format: text-only for first N-1, full for last
        result = []
        for i, msg in enumerate(recent):
            if i == len(recent) - 1 and msg["tools"]:
                # Last message: include tool info
                formatted = _format_message_with_tools(msg)
                log_debug(
                    "transcript_reader",
                    f"Message {i} (with tools): {formatted[:100]}...",
                )
                result.append(formatted)
            else:
                # Earlier messages: text only
                log_debug(
                    "transcript_reader",
                    f"Message {i} (text only): {msg['text'][:100]}...",
                )
                result.append(msg["text"])

        log_debug("transcript_reader", f"Returning {len(result)} formatted messages")
        return result

    except Exception as e:
        log_warning("transcript_reader", "Failed to extract messages for validation", e)
        return []


def _legacy_get_current_turn_message(transcript_path: str) -> str:
    """Extract the current assistant turn anchored on the last Write/Edit
    tool_use's requestId. (Legacy path — kept for backward compat.)

    Fix 3: the INTENT/skip declaration that belongs to the same logical turn
    as the tool_use being validated shares the tool_use entry's requestId.
    A fixed n-back window can miss that declaration when the turn is
    fragmented across JSONL entries (e.g. a separate prose block, or an
    interrupt-style turn in between). Anchoring on the tool_use's requestId
    captures exactly the same-turn text+tools and — critically — never pulls
    in a STALE INTENT from a prior turn (which carries a different requestId).

    Returns the merged, tool-formatted text of the requestId group containing
    the most-recent Write/Edit tool_use. Returns "" when no such tool_use is
    present. When entries lack a requestId (older Claude Code), falls back to
    the last assistant entry that carries the tool_use.

    Intent-marker gate (best-of-both): the override is only authoritative when
    the anchored turn's TEXT actually carries an ``INTENT:`` marker. If the
    same-turn group has a tool_use but NO intent marker in its text, this
    returns "" so the caller's ``override or n-back`` falls through to the
    1-back-tolerant n-back path (``extract_current_assistant_message``), which
    rescues an INTENT declared in the immediately-preceding assistant message
    (fragmented turn / pre-flush state) AND still enforces deep-stale
    protection. The marker is checked on TEXT only (not on tool content), so a
    bare ``intent:`` appearing inside the edited file's code never qualifies.

    Args:
        transcript_path: Path to JSONL transcript file

    Returns:
        Formatted current-turn message when the anchored turn carries an
        INTENT marker; "" otherwise (no tool_use, or no same-turn INTENT).
    """
    try:
        # Preserve discovery order so "last" tool_use is well defined.
        entries = []  # list of (request_id_or_None, msg_parts)
        with open(transcript_path, "r") as f:
            for line in f:
                entry = json.loads(line)
                message = entry.get("message", {})
                if message.get("role") != "assistant":
                    continue
                msg_parts = _extract_message_parts(message.get("content", []))
                entries.append((entry.get("requestId"), msg_parts))

        # Find the LAST entry carrying a Write/Edit tool_use — that anchors
        # the current turn.
        anchor_index = None
        for i in range(len(entries) - 1, -1, -1):
            if _has_write_or_edit(entries[i][1]):
                anchor_index = i
                break

        if anchor_index is None:
            return ""

        anchor_request_id, anchor_parts = entries[anchor_index]

        # Staleness/pre-flush gate: if a LATER assistant turn (different
        # requestId) appears after the anchored tool_use, the transcript has
        # moved on — the anchor belongs to a PRIOR turn and the real current
        # edit's tool_use is not yet flushed. Defer to the n-back path, which
        # sees the already-flushed same-turn INTENT. (A later entry sharing the
        # anchor's requestId is still the same turn and does NOT make it stale.)
        if anchor_request_id is not None:
            for request_id, _parts in entries[anchor_index + 1 :]:
                if request_id is not None and request_id != anchor_request_id:
                    return ""

        # When requestId is present, merge ALL entries sharing it (same logical
        # turn). When absent, fall back to the anchor entry alone.
        if anchor_request_id is not None:
            merged: Dict[str, Any] = {"text": "", "tools": []}
            for request_id, parts in entries:
                if request_id != anchor_request_id:
                    continue
                if parts["text"]:
                    merged["text"] = (
                        merged["text"] + "\n" + parts["text"]
                        if merged["text"]
                        else parts["text"]
                    )
                merged["tools"].extend(parts["tools"])
        else:
            merged = anchor_parts

        # Intent-marker gate: only authoritative when the anchored turn's TEXT
        # carries an INTENT marker. Otherwise defer to the n-back rescue.
        if not re.search(r"(?i)\bintent\s*:", merged["text"]):
            return ""

        return _format_message_with_tools(merged)

    except Exception as e:
        log_warning(
            "transcript_reader",
            "Failed to extract current-turn message for validation",
            e,
        )
        return ""


def _tool_input_matches(tool: dict, tool_name: str, tool_input: dict) -> bool:
    """Return True if a tool_use block matches the given tool_name + key fields."""
    if tool.get("name") != tool_name:
        return False
    inp = tool.get("input", {})
    if tool_name == "Write":
        return inp.get("file_path") == tool_input.get("file_path") and inp.get(
            "content"
        ) == tool_input.get("content")
    if tool_name == "Edit":
        return inp.get("file_path") == tool_input.get("file_path") and inp.get(
            "new_string"
        ) == tool_input.get("new_string")
    if tool_name == "Bash":
        return inp.get("command") == tool_input.get("command")
    return inp == tool_input


def _find_turn_matching_tool_input(
    transcript_path: str,
    tool_input: dict,
    tool_name: str,
) -> Optional[str]:
    """Scan transcript for the assistant turn containing the matching tool_use.

    Bug #90 hardening: a content-only match is not enough. When a command is
    RE-ISSUED after a prior blocked attempt with IDENTICAL tool_input, the
    transcript already contains that earlier (possibly INTENT-less) turn's
    tool_use plus its tool_result feedback. If the re-issued (current) turn
    has not yet flushed, a naive "last matching entry" scan finds the STALE
    earlier turn instead of correctly waiting.

    The staleness signal is scoped to the SPECIFIC matched tool_use's own
    ``id``: a match is stale only when a ``tool_result`` entry exists
    elsewhere in the transcript whose ``tool_use_id`` equals the anchor
    tool_use's ``id`` (bug #90 v2 — a prior "is this the literal last line
    of the transcript" frontier check was too strict and broke multi-tool
    turns: when one message makes several tool calls sharing a requestId,
    the FIRST tool's completed tool_result legitimately appears before the
    SECOND tool's own PreToolUse validation fires, and that must NOT be
    treated as staleness for the second tool's still-unexecuted match).
    A tool_result for a *different* tool_use id (a sibling call in the same
    turn) never marks this match stale.

    Returns:
        None  — file missing, no matching tool_use found, or the only match
                found is STALE (a tool_result already exists for its own id)
        ""    — matching turn found, is NOT stale, but its TEXT lacks an
                INTENT: marker
        str   — matching turn found, is NOT stale, with INTENT: in TEXT
    """
    try:
        raw_entries: List[dict] = []
        with open(transcript_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                message = entry.get("message", {})
                role = message.get("role")
                msg_parts = (
                    _extract_message_parts(message.get("content", []))
                    if role == "assistant"
                    else None
                )
                raw_entries.append(
                    {
                        "role": role,
                        "request_id": entry.get("requestId"),
                        "parts": msg_parts,
                        "content": message.get("content", []),
                    }
                )

        total = len(raw_entries)

        # Find the LAST assistant entry whose tools include a matching tool_use.
        anchor_index = None
        anchor_tool_id = None
        for i in range(total - 1, -1, -1):
            e = raw_entries[i]
            if e["role"] != "assistant":
                continue
            for tool in e["parts"]["tools"]:
                if _tool_input_matches(tool, tool_name, tool_input):
                    anchor_index = i
                    anchor_tool_id = tool.get("id")
                    break
            if anchor_index is not None:
                break

        if anchor_index is None:
            return None

        anchor_request_id = raw_entries[anchor_index]["request_id"]

        # Staleness gate (bug #90 v2): stale iff a tool_result exists ANYWHERE
        # in the transcript carrying this SPECIFIC tool_use's own id — proof
        # that exact tool_use was already executed/processed once before.
        # Entries without a resolvable id fall back to "not stale" (preserves
        # the pre-#90 LAST-match-wins behavior for that edge case rather than
        # risking an unrecoverable block).
        if anchor_tool_id is not None:
            for e in raw_entries:
                if e["role"] != "user":
                    continue
                content = e["content"]
                if not isinstance(content, list):
                    continue
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_result"
                        and block.get("tool_use_id") == anchor_tool_id
                    ):
                        return None

        # Merge all entries sharing the anchor's requestId (same logical turn).
        if anchor_request_id is not None:
            merged: Dict[str, Any] = {"text": "", "tools": []}
            for e in raw_entries:
                if e["role"] != "assistant" or e["request_id"] != anchor_request_id:
                    continue
                parts = e["parts"]
                if parts["text"]:
                    merged["text"] = (
                        merged["text"] + "\n" + parts["text"]
                        if merged["text"]
                        else parts["text"]
                    )
                merged["tools"].extend(parts["tools"])
        else:
            merged = raw_entries[anchor_index]["parts"]

        # Intent-marker gate: only return non-empty when INTENT: is in TEXT.
        if not re.search(r"(?i)\bintent\s*:", merged["text"]):
            return ""

        return _format_message_with_tools(merged)

    except (FileNotFoundError, OSError):
        return None
    except Exception as e:
        log_warning(
            "transcript_reader",
            "Failed to find turn matching tool input",
            e,
        )
        return None


def get_current_turn_message_for_validation(
    transcript_path: str,
    tool_input: Optional[dict] = None,
    tool_name: Optional[str] = None,
    *,
    _max_retries: int = 20,
    _retry_sleep: float = 0.25,
) -> Optional[str]:
    """Extract the current assistant turn message for intent validation.

    When ``tool_input`` is provided (bug #83 fix): uses a content-matched
    anchor to find the exact tool_use being validated.  Returns None when
    the matching entry is not yet in the transcript (TOCTOU race) — the
    caller decides how to react (as of v2.33.2, both the Write/Edit gate and
    the danger-bash gate fail CLOSED on this signal: block + instruct the
    agent to re-issue the identical tool call, rather than silently passing
    the unvalidated edit through).

    When ``tool_input`` is None (legacy path): returns str (never None) using
    the old last-Write/Edit anchor for backward compatibility.

    Args:
        transcript_path: Path to JSONL transcript file.
        tool_input: PreToolUse tool_input dict.  None => legacy path.
        tool_name: Tool name (Write/Edit/Bash).  Required with tool_input.
        _max_retries: Max re-reads after first miss (bounded per Messi Rule
            14). Default 20 => 21 total reads, ~5.0s max wait at the default
            _retry_sleep (coordinator refinement, v2.33.2 — widened from the
            original ~1.0s/10 reads to catch more in-window transcript
            flushes before falling back to fail-closed + re-issue). Both the
            Write/Edit gate and the danger-bash gate call this function
            without overriding these parameters, so this default is the
            single source of truth for the wait ceiling on both pre-tool
            gates.
        _retry_sleep: Seconds between attempts. Default 0.25s — fewer,
            longer-spaced reads than a naive finer-grained scheme, since the
            transcript can be tens of MB on busy sessions and is re-read in
            full on every attempt; ≪ the 60s outer hook timeout.

    Returns:
        None  -- tool_input given but matching turn absent (not-yet-flushed
                 signal; caller decides the reaction).
        ""    -- matching turn found but no INTENT: in TEXT (n-back fallback).
        str   -- matching turn with INTENT: in TEXT (use directly).
        (When tool_input is None, always returns str per legacy contract.)
    """
    if tool_input is None:
        # Legacy path: returns str (never None).
        return _legacy_get_current_turn_message(transcript_path)

    # New path (bug #83): tool-matched anchor with bounded retry.
    for attempt in range(_max_retries + 1):
        result = _find_turn_matching_tool_input(
            transcript_path, tool_input, tool_name or ""
        )
        if result is not None:
            return result
        if attempt < _max_retries:
            time.sleep(_retry_sleep)

    return None


def _has_write_or_edit(msg_parts: dict) -> bool:
    """Return True if msg_parts contains a Write or Edit tool_use."""
    return any(tool.get("name") in ("Write", "Edit") for tool in msg_parts["tools"])


def _extract_text_only(content: Union[list, str]) -> str:
    """
    Extract only text content from message content blocks.

    Skips tool_use and tool_result blocks, returning only text content.

    Args:
        content: Message content (list of blocks or string)

    Returns:
        Extracted text content
    """
    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        return "\n".join(text_parts)
    elif isinstance(content, str):
        return content
    return ""


def _find_last_compact_boundary_line(transcript_path: str) -> int:
    """
    Find the line number of the last compact_boundary entry.

    When a conversation is compacted, old content stays in the JSONL file
    but should be ignored. Only content AFTER the last compact_boundary
    represents the current conversation.

    Args:
        transcript_path: Path to JSONL transcript file

    Returns:
        Line number (0-indexed) of last compact_boundary, or -1 if none found
    """
    last_boundary_line = -1
    try:
        with open(transcript_path, "r") as f:
            for line_num, line in enumerate(f):
                entry = json.loads(line)
                # Check for compact_boundary marker
                if entry.get("subtype") == "compact_boundary":
                    last_boundary_line = line_num
    except Exception as e:
        log_warning("transcript_reader", "Failed to find compact boundary", e)

    return last_boundary_line


def build_stop_hook_context(
    transcript_path: str,
    first_n_pairs: int = 10,
    token_budget: int = 48000,
) -> dict:
    """
    Build context for stop hook validation with first-pairs + backwards-walk algorithm.

    This function extracts:
    1. First N user/assistant message pairs from the beginning (to understand session goals)
    2. Recent messages by walking backwards from end (to understand current state)

    CRITICAL: Only reads content AFTER the last compact_boundary marker to ensure
    validation occurs against the current conversation, not stale pre-compaction content.

    A "pair" consists of a user message and all assistant messages that follow it
    before the next user message.

    Args:
        transcript_path: Path to JSONL transcript file
        first_n_pairs: Number of initial user/assistant pairs to include (default: 10)
        token_budget: Maximum tokens for entire context (default: 48000)

    Returns:
        Dictionary with:
        - 'first_pairs': List of (user_msg, assistant_msgs) tuples from beginning
        - 'backwards_messages': List of (role, text) tuples from backwards walk (most recent first)
        - 'truncated_count': Number of messages omitted in the middle
        - 'total_tokens': Estimated token count
    """
    try:
        # Find last compact boundary to skip pre-compaction content
        compact_boundary_line = _find_last_compact_boundary_line(transcript_path)

        # Parse transcript and extract all messages (text only)
        # Skip all content before and including the compact boundary
        all_messages = []

        with open(transcript_path, "r") as f:
            prev_was_meta = False
            for line_num, line in enumerate(f):
                # Skip lines before and including the compact boundary
                if line_num <= compact_boundary_line:
                    continue

                entry = json.loads(line)

                # Skip META messages — these are stop hook feedback injections,
                # not real user messages.  Including them causes a death spiral:
                # rejection feedback -> short assistant response -> evaluated as
                # "last message" -> rejected again -> repeat.
                if entry.get("isMeta"):
                    prev_was_meta = True
                    continue

                message = entry.get("message", {})
                role = message.get("role")

                # Only process user and assistant messages
                if role not in ["user", "assistant"]:
                    prev_was_meta = False
                    continue

                content = message.get("content", [])
                text = _extract_text_only(content)

                if not text.strip():
                    prev_was_meta = False
                    continue

                # Skip short assistant responses that immediately follow META
                # messages.  These are reflexive "I'm waiting" messages, not
                # substantive content.  Real E2E tables are 500+ chars; waiting
                # messages are typically <150 chars.  200 gives comfortable margin.
                _META_SHORT_THRESHOLD = 200
                if (
                    role == "assistant"
                    and prev_was_meta
                    and len(text.strip()) < _META_SHORT_THRESHOLD
                ):
                    prev_was_meta = False
                    continue

                prev_was_meta = False
                all_messages.append({"role": role, "text": text})

        if not all_messages:
            return {
                "first_pairs": [],
                "backwards_messages": [],
                "truncated_count": 0,
                "total_tokens": 0,
            }

        # Build first N pairs
        first_pairs = []
        pairs_found = 0
        i = 0

        while i < len(all_messages) and pairs_found < first_n_pairs:
            if all_messages[i]["role"] == "user":
                user_msg = all_messages[i]["text"]
                assistant_msgs = []

                # Collect all assistant messages until next user message
                j = i + 1
                while j < len(all_messages) and all_messages[j]["role"] == "assistant":
                    assistant_msgs.append(all_messages[j]["text"])
                    j += 1

                first_pairs.append((user_msg, assistant_msgs))
                pairs_found += 1
                i = j
            else:
                i += 1

        # Calculate tokens used by first pairs (estimate: 4 chars per token)
        first_pairs_text = ""
        for user_msg, assistant_msgs in first_pairs:
            first_pairs_text += user_msg + "\n".join(assistant_msgs)
        first_pairs_tokens = len(first_pairs_text) // 4

        # Find the index where first pairs ended
        first_pairs_end_index = 0
        if first_pairs:
            # Count how many messages were included in first pairs
            for user_msg, assistant_msgs in first_pairs:
                first_pairs_end_index += 1 + len(assistant_msgs)  # user + assistants

        # Walk backwards from end until budget exhausted.
        # When META filtering has collapsed all_messages to fewer entries than
        # first_pairs_end_index (e.g. all remaining messages were already absorbed
        # into first_pairs), set the stop boundary to -1 so the backwards walk
        # covers the entire message list.  This preserves recent context (the last
        # substantive E2E table) even when the transcript has been shrunk by META
        # filtering.  When there are messages beyond first_pairs_end_index the stop
        # boundary stays at first_pairs_end_index - 1 (normal non-overlapping case).
        remaining_budget = token_budget - first_pairs_tokens
        backwards_messages = []
        backwards_tokens = 0

        backwards_stop = (
            first_pairs_end_index - 1
            if len(all_messages) > first_pairs_end_index
            else -1
        )
        for i in range(len(all_messages) - 1, backwards_stop, -1):
            msg = all_messages[i]
            msg_tokens = len(msg["text"]) // 4

            if backwards_tokens + msg_tokens > remaining_budget:
                break

            backwards_messages.append((msg["role"], msg["text"]))
            backwards_tokens += msg_tokens

        # Calculate truncated count
        truncated_count = (
            len(all_messages) - first_pairs_end_index - len(backwards_messages)
        )
        if truncated_count < 0:
            truncated_count = 0

        total_tokens = first_pairs_tokens + backwards_tokens

        return {
            "first_pairs": first_pairs,
            "backwards_messages": backwards_messages,
            "truncated_count": truncated_count,
            "total_tokens": total_tokens,
        }

    except Exception as e:
        log_warning("transcript_reader", "Failed to build stop hook context", e)
        return {
            "first_pairs": [],
            "backwards_messages": [],
            "truncated_count": 0,
            "total_tokens": 0,
        }


def detect_silent_tool_stop(transcript_path: str) -> bool:
    """
    Detect if Claude stopped silently after a tool use without producing text output.

    Reads the last ~32KB of the transcript, walks backward through JSONL lines,
    skips progress entries and malformed JSON, and checks if the LAST non-progress
    entry is a user message containing at least one tool_result content block.

    This directly answers: "Did Claude receive a tool result but stop without
    responding?" Claude Code writes text, tool_use, and thinking as SEPARATE JSONL
    entries per turn, so checking the last assistant entry's content blocks is
    unreliable (stale entries from previous turns are found). Checking for a
    trailing user:tool_result entry is reliable because Claude always responds
    after receiving tool results when functioning normally.

    Args:
        transcript_path: Path to JSONL transcript file

    Returns:
        True if last non-progress entry is a user message with tool_result content
        (silent stop detected), False otherwise (Claude responded, no tool results,
        empty/missing file)
    """
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()

            if file_size == 0:
                return False

            read_size = min(32000, file_size)
            f.seek(file_size - read_size)
            content = f.read().decode("utf-8", errors="ignore")

        lines = [line.strip() for line in content.split("\n") if line.strip()]

        if not lines:
            return False

        # Walk backward, skip progress entries and malformed JSON.
        # Stop at the first valid non-progress entry and evaluate it.
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Skip progress entries (by top-level type or message role)
            if entry.get("type") == "progress":
                continue
            if entry.get("message", {}).get("role") == "progress":
                continue

            # This is the last meaningful entry - check if it is user:tool_result
            message = entry.get("message", {})
            if message.get("role") != "user":
                return False

            content_blocks = message.get("content", [])
            if not isinstance(content_blocks, list):
                return False

            has_tool_result = any(
                isinstance(block, dict) and block.get("type") == "tool_result"
                for block in content_blocks
            )
            return has_tool_result

        # No valid non-progress entries found
        return False

    except Exception as e:
        log_warning("transcript_reader", "Failed to detect silent tool stop", e)
        return False


def format_stop_hook_context(context: dict) -> str:
    """
    Format the context dict into a string for the stop hook prompt.

    Args:
        context: Context dictionary from build_stop_hook_context()

    Returns:
        Formatted string with first pairs, truncation marker, and recent messages
    """
    output = []

    # First pairs section
    if context["first_pairs"]:
        output.append(
            "=== BEGINNING OF SESSION (First 10 user requests and responses) ===\n"
        )

        for idx, (user_msg, assistant_msgs) in enumerate(context["first_pairs"], 1):
            output.append(f"[USER {idx}]")
            output.append(user_msg)
            output.append("")

            for asst_idx, assistant_msg in enumerate(assistant_msgs, 1):
                if len(assistant_msgs) > 1:
                    output.append(f"[ASSISTANT {idx}.{asst_idx}]")
                else:
                    output.append(f"[ASSISTANT {idx}]")
                output.append(assistant_msg)
                output.append("")

    # Truncation marker
    if context["truncated_count"] > 0:
        output.append(
            f"=== [TRUNCATED - ~{context['truncated_count']} messages omitted] ===\n"
        )

    # Recent messages section
    if context["backwards_messages"]:
        output.append("=== RECENT CONVERSATION (Most recent messages) ===\n")

        # Reverse to show chronologically (oldest to newest)
        for role, text in reversed(context["backwards_messages"]):
            output.append(f"[{role.upper()}]")
            output.append(text)
            output.append("")

    return "\n".join(output)
