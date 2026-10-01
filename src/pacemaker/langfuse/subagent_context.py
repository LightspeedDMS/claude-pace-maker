"""
Bounded parent-transcript reader for SubagentStart (bug #157, fix 2).

``orchestrator.handle_subagent_start`` needs three facts from the PARENT
transcript: the spawning Agent/Task call's prompt, the session's model and the
user's email. It used to get them with three functions that each parsed the
WHOLE file (``extract_task_tool_prompt``, ``jsonl_parser.parse_session_metadata``,
``jsonl_parser.extract_user_id`` -- 1.45 s together on a 52 MB parent), on a
hook with a 10 s budget.

The spawning call is at (or extremely near) EOF, so this module reads ONE
bounded HEAD window (where legacy ``session_start`` / ``auth_profile`` entries
live) and ONE bounded TAIL window, parses each line once, and answers all three
questions in a single pass -- cost is O(window sizes), flat in file size.

Semantics are the old parsers' own ("latest wins"), with one rule about the
windows (code-review M1): the HEAD window may answer ONLY ``user_id`` and a
``session_start`` model; the prompt and the last model come from the TAIL
(or, for a file that fits in both windows, from the whole file). An older spawn
prompt / model sitting in the head must never beat a newer one outside both
windows.
- prompt: the LAST Task/Agent ``tool_use`` with a non-empty prompt;
- model: the LAST ``message.model`` / ``entry.model`` seen, except that a
  ``session_start`` entry's model wins and stops the scan (exactly
  ``parse_session_metadata``); ``"unknown"`` when none exists;
- user_id: the FIRST ``auth_profile`` / ``profile`` email (the OAuth-API
  fallback stays the caller's business, as before).

Fallback for data the TAIL did not hold: when the file is bigger than both
windows and the PROMPT or MODEL is still missing, ONE streaming pass over the
whole file finds the LATEST occurrence (one parse instead of the old three;
rare -- the spawning call is near EOF, but it may not be flushed yet when
SubagentStart fires). The user_id has no full-file fallback: real Claude
Code transcripts carry no ``auth_profile`` entry, so scanning the whole file
for it would be a guaranteed cost for nothing -- the head window still covers
the legacy layout, and the caller falls back to the OAuth API as before.
"""

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from ..logger import log_debug, log_warning

# Mirrors orchestrator.SUBAGENT_TOOL_NAMES (kept local so this module stays a
# leaf the orchestrator imports, never the other way round).
SUBAGENT_TOOL_NAMES = {"Task", "Agent"}

HEAD_WINDOW_BYTES = 64 * 1024
TAIL_WINDOW_BYTES = 1024 * 1024
UNKNOWN_MODEL = "unknown"


@dataclass(frozen=True)
class SubagentStartContext:
    prompt: Optional[str]
    model: str
    user_id: Optional[str]


class _Scan:
    """Accumulates the three answers over entries fed in file order."""

    def __init__(self) -> None:
        self.prompt: Optional[str] = None
        self.model: Optional[str] = None
        self.user_id: Optional[str] = None
        self._model_locked = False

    def feed_head(self, entry: Dict[str, Any]) -> None:
        """A HEAD-window entry of a file bigger than both windows: only the
        legacy email and a ``session_start`` model (which locks the model, as
        in the old parser) count -- never the prompt or an ordinary model."""
        self._feed_user(entry)
        if entry.get("type") == "session_start":
            self._feed_model(entry)

    def feed(self, entry: Dict[str, Any]) -> None:
        self._feed_user(entry)
        self._feed_model(entry)
        self._feed_prompt(entry)

    def _feed_user(self, entry: Dict[str, Any]) -> None:
        if self.user_id:
            return
        if entry.get("type") == "auth_profile":
            profile = entry.get("profile", {})
            email = profile.get("email") if isinstance(profile, dict) else None
            if email:
                self.user_id = email
                return
        profile = entry.get("profile")
        if isinstance(profile, dict) and profile.get("email"):
            self.user_id = profile["email"]

    def _feed_model(self, entry: Dict[str, Any]) -> None:
        if self._model_locked:
            return
        if entry.get("type") == "session_start":
            self.model = entry.get("model", UNKNOWN_MODEL)
            self._model_locked = True  # the old parser breaks at session_start
            return
        message = entry.get("message", {})
        if isinstance(message, dict) and "model" in message:
            self.model = message["model"]
        elif "model" in entry:
            self.model = entry["model"]

    def _feed_prompt(self, entry: Dict[str, Any]) -> None:
        if entry.get("type") != "assistant":
            return
        message = entry.get("message", {})
        if not isinstance(message, dict):
            return
        content = message.get("content", [])
        if not isinstance(content, list):
            return
        for item in content:
            if (
                isinstance(item, dict)
                and item.get("type") == "tool_use"
                and item.get("name") in SUBAGENT_TOOL_NAMES
            ):
                tool_input = item.get("input", {})
                prompt = (
                    tool_input.get("prompt") if isinstance(tool_input, dict) else None
                )
                if prompt:
                    self.prompt = prompt


def _parse_lines(raw_lines: Iterable[bytes]) -> Iterator[Dict[str, Any]]:
    """Parse JSONL lines once; malformed or non-object lines are skipped (a
    window can start/end mid-line, and a transcript being appended to can
    end with a partial line)."""
    for raw in raw_lines:
        text = raw.strip()
        if not text:
            continue
        try:
            entry = json.loads(text.decode("utf-8", errors="replace"))
        except (ValueError, RecursionError):
            continue
        if isinstance(entry, dict):
            yield entry


def _read_windows(
    path: str, head_bytes: int, tail_bytes: int
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], bool]:
    """``(head entries, tail entries, whole_file_read)``. Whole file when it
    fits in the two windows (then ALL entries are returned as the "tail" so the
    full old semantics apply and there is no head); otherwise head entries (cut
    at the last newline) and tail entries (cut after the first newline, unless
    the window happens to start exactly on a line boundary), kept SEPARATE
    because the head may only answer user_id / session_start questions."""
    with open(path, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        if size <= head_bytes + tail_bytes:
            return [], list(_parse_lines(handle.read().split(b"\n"))), True
        head = handle.read(head_bytes)
        head = head[: head.rfind(b"\n") + 1] if b"\n" in head else b""
        handle.seek(size - tail_bytes - 1)
        tail = handle.read()
        if tail[:1] == b"\n":
            tail = tail[1:]
        else:
            cut = tail.find(b"\n")
            tail = tail[cut + 1 :] if cut != -1 else b""
    return (
        list(_parse_lines(head.split(b"\n"))),
        list(_parse_lines(tail.split(b"\n"))),
        False,
    )


def _stream_whole_file(path: str) -> Iterator[Dict[str, Any]]:
    with open(path, "rb") as handle:
        yield from _parse_lines(handle)


def read_subagent_start_context(
    transcript_path: str,
    head_bytes: int = HEAD_WINDOW_BYTES,
    tail_bytes: int = TAIL_WINDOW_BYTES,
) -> SubagentStartContext:
    """Prompt / model / user_id of the parent session, from bounded windows.

    Never raises for a missing or unreadable transcript: everything defaults
    (no prompt, ``"unknown"`` model, no user), exactly as the old parsers
    degraded."""
    scan = _Scan()
    complete = True
    try:
        head, tail, complete = _read_windows(transcript_path, head_bytes, tail_bytes)
        # The HEAD answers only user_id (legacy auth_profile) and a
        # session_start model. It must NEVER supply the prompt or the "last
        # model": an older spawn prompt / model sitting in the head would beat
        # a newer one outside both windows (code-review M1).
        for entry in head:
            scan.feed_head(entry)
        for entry in tail:
            scan.feed(entry)
        if not complete and (scan.prompt is None or scan.model is None):
            log_debug(
                "subagent_context",
                "window miss (prompt/model): one streaming pass over the "
                f"whole parent transcript {transcript_path}",
            )
            full = _Scan()
            for entry in _stream_whole_file(transcript_path):
                full.feed(entry)
            if scan.prompt is None:
                scan.prompt = full.prompt
            if scan.model is None:
                scan.model = full.model
    except FileNotFoundError:
        log_warning(
            "subagent_context", f"Parent transcript not found: {transcript_path}", None
        )
    except OSError as exc:
        log_warning(
            "subagent_context",
            f"Failed to read parent transcript: {transcript_path}",
            exc,
        )
    return SubagentStartContext(
        prompt=scan.prompt,
        model=scan.model if scan.model is not None else UNKNOWN_MODEL,
        user_id=scan.user_id,
    )
