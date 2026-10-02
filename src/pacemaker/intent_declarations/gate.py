"""
Hook-side wiring helpers for tool-first intent declaration (Story #155).

Three seams, each called from exactly one place in ``pacemaker.hook``:

- ``record_declare_intent``   -- PostToolUse (``run_hook``): store a
  ``declare_intent`` call keyed by session_id + agent_key + normalized path.
- ``resolve_declared_intent`` -- Write/Edit gate (``run_pre_tool_hook``),
  BEFORE the transcript anchor wait: declaration -> chain -> (None = fall
  back to the transcript).
- ``record_outcome``          -- Write/Edit gate, after the verdict: an
  approval of a tool-sourced intent (re)creates the agent's chain, any
  rejection deletes it.

Plus ``same_message_declared_intent`` (the transcript fallback's extra source:
a ``declare_intent`` tool_use in the anchored turn) and
``render_intent_message`` (the synthesized Stage-1/Stage-2 intent text).

Failure policy: the store is an optimisation of WHERE the intent text comes
from, never a gate of its own. A store failure is logged at WARNING and the
Write/Edit simply takes the (unchanged) transcript path, which still
validates it. Nothing here raises into a hook -- with one deliberate
exception: ``store.resolve_db_path()`` raises ``RuntimeError`` in test mode
when the conftest guard env var is unset, and that is NOT swallowed, so a
test that forgot its isolation fails loudly instead of touching the real DB.
"""

import sqlite3
from dataclasses import dataclass
from collections.abc import Iterable
from typing import Any, Dict, Optional

from ..constants import (
    DECLARATION_MAX_FIELD_CHARS,
    DECLARATION_MAX_MESSAGE_LENGTH,
    DECLARE_INTENT_TOOL_NAMES,
)
from ..logger import log_debug, log_warning
from ..prompt_loader import PromptLoader
from ..prompt_provenance import format_tag
from .fields import (
    intent_declaration_tool_enabled,
    missing_required_fields,
    normalize_file_path,
    truncate_text,
)
from .store import (
    IntentDeclarationStore,
    Resolution,
    SOURCE_DECLARATION,
    StoredIntent,
    resolve_db_path,
)

MAIN_AGENT_KEY = "main"

# Bug #163: static note template in prompts/common/ (a literal ``<file_path>``
# token, replaced with str.replace -- never PromptLoader ``variables=``).
_SIBLING_NOTE_TEMPLATE = "declare_intent_rejected_sibling_note.md"

# Errors the store/normalization layer can legitimately raise at runtime.
_RECOVERABLE_ERRORS = (sqlite3.Error, OSError, ValueError)


@dataclass(frozen=True)
class DeclaredIntent:
    """An intent the gate takes from the tool path instead of the transcript.

    ``source`` is ``"declare_intent"`` (a consumed declaration, or a
    same-message ``declare_intent`` tool_use) or ``"declare_intent_chain"``.
    """

    source: str
    intent: StoredIntent


def _agent_key(hook_data: Dict[str, Any]) -> str:
    agent_id = hook_data.get("agent_id")
    return str(agent_id) if agent_id else MAIN_AGENT_KEY


def _session_id(hook_data: Dict[str, Any]) -> Optional[str]:
    session_id = hook_data.get("session_id")
    return session_id if isinstance(session_id, str) and session_id else None


def _cwd(hook_data: Dict[str, Any]) -> Optional[str]:
    cwd = hook_data.get("cwd")
    return cwd if isinstance(cwd, str) and cwd else None


def _clean(value: Any) -> str:
    """A free-text declared field: stripped, capped at
    ``DECLARATION_MAX_FIELD_CHARS`` (truncated with an ellipsis), and ``""``
    for anything that is not a string."""
    if not isinstance(value, str):
        return ""
    return truncate_text(value.strip(), DECLARATION_MAX_FIELD_CHARS)


def render_intent_message(intent: StoredIntent, file_path: str) -> str:
    """The synthesized intent text that becomes Stage 1's / Stage 2's intent.

    ::

        INTENT: <change> in <file_path> — goal: <goal>
        Test coverage: <test_coverage>        (only when given)

    Each field is collapsed to a single line so a declaration cannot smuggle
    extra structure (e.g. a forged second line) into the message. The path
    shown is the Write/Edit's own ``file_path`` (what Stage 1's file-mention
    check and the reviewer compare against).

    The whole message is capped at ``DECLARATION_MAX_MESSAGE_LENGTH`` (the
    transcript path's ``MAX_MESSAGE_LENGTH``), truncated with an ellipsis.
    """
    change = " ".join(intent.change.split())
    goal = " ".join(intent.goal.split())
    message = f"INTENT: {change} in {file_path} — goal: {goal}"
    test_coverage = " ".join(intent.test_coverage.split())
    if test_coverage:
        message += f"\nTest coverage: {test_coverage}"
    return truncate_text(message, DECLARATION_MAX_MESSAGE_LENGTH)


def record_declare_intent(hook_data: Dict[str, Any], config: Dict[str, Any]) -> bool:
    """PostToolUse recorder. Returns True iff a declaration row was stored.

    Stores nothing -- and says why at DEBUG/WARNING -- when the kill switch is
    off, the tool is not a declare_intent tool, the declaration is incomplete,
    there is no session_id, or the store fails. Never raises into the hook.
    """
    if not intent_declaration_tool_enabled(config):
        return False
    if hook_data.get("tool_name") not in DECLARE_INTENT_TOOL_NAMES:
        return False
    arguments = hook_data.get("tool_input")
    if not isinstance(arguments, dict):
        log_debug("intent_declarations", "declare_intent call has no tool_input")
        return False
    missing = missing_required_fields(arguments)
    if missing:
        log_debug(
            "intent_declarations",
            f"declare_intent call not recorded: missing {missing}",
        )
        return False
    session_id = _session_id(hook_data)
    if session_id is None:
        log_warning(
            "intent_declarations",
            "declare_intent call not recorded: hook payload has no session_id",
        )
        return False
    db_path = resolve_db_path()
    try:
        IntentDeclarationStore(db_path).record(
            session_id,
            _agent_key(hook_data),
            normalize_file_path(arguments["file_path"].strip(), _cwd(hook_data)),
            _clean(arguments["change"]),
            _clean(arguments["goal"]),
            _clean(arguments.get("test_coverage")),
        )
    except _RECOVERABLE_ERRORS as exc:
        log_warning(
            "intent_declarations",
            "declare_intent call not recorded (transcript fallback will "
            "apply to the next Write/Edit)",
            exc,
        )
        return False
    return True


def resolve_declared_intent(
    hook_data: Dict[str, Any], file_path: str, config: Dict[str, Any]
) -> Optional[DeclaredIntent]:
    """Write/Edit gate lookup (steps 1-3): declaration, then chain.

    ``None`` means "no tool intent -- run the transcript path unchanged"
    (kill switch off, no session_id, nothing stored for this file, or a store
    failure). A chain for a DIFFERENT file is deleted as a side effect.
    """
    if not intent_declaration_tool_enabled(config):
        return None
    session_id = _session_id(hook_data)
    if session_id is None:
        return None
    db_path = resolve_db_path()
    try:
        resolution: Optional[Resolution] = IntentDeclarationStore(db_path).resolve(
            session_id,
            _agent_key(hook_data),
            normalize_file_path(file_path, _cwd(hook_data)),
        )
    except _RECOVERABLE_ERRORS as exc:
        log_warning(
            "intent_declarations",
            "declaration lookup failed -- using the transcript path",
            exc,
        )
        return None
    if resolution is None:
        return None
    return DeclaredIntent(resolution.source, resolution.intent)


def record_outcome(
    hook_data: Dict[str, Any],
    file_path: str,
    declared: Optional[DeclaredIntent],
    approved: bool,
    config: Dict[str, Any],
) -> None:
    """Apply the Write/Edit verdict to the agent's chain.

    - approved + tool-sourced intent (``declared``): upsert the chain with
      this file and intent, so consecutive edits of the same file reuse it.
    - approved + transcript-sourced (``declared`` is None): chain untouched
      (there is no declared intent to chain).
    - rejected (Stage 1 or Stage 2 block, reviewer unavailable): delete the
      agent's chain whatever the source -- the retry needs a fresh
      declaration.
    """
    if not intent_declaration_tool_enabled(config):
        return
    session_id = _session_id(hook_data)
    if session_id is None:
        return
    agent_key = _agent_key(hook_data)
    db_path = resolve_db_path()
    try:
        store = IntentDeclarationStore(db_path)
        if not approved:
            # Bug #163: a rejection of a tool/chain-declared edit consumed
            # that declaration -- leave the short-lived marker for the file.
            store.reject(
                session_id,
                agent_key,
                (
                    normalize_file_path(file_path, _cwd(hook_data))
                    if declared is not None
                    else None
                ),
            )
        elif declared is not None:
            normalized = normalize_file_path(file_path, _cwd(hook_data))
            store.approve(
                session_id,
                agent_key,
                StoredIntent(
                    normalized,
                    declared.intent.change,
                    declared.intent.goal,
                    declared.intent.test_coverage,
                ),
            )
    except _RECOVERABLE_ERRORS as exc:
        log_warning(
            "intent_declarations",
            "could not update the declaration chain after validation",
            exc,
        )


def lead_with_rejected_sibling_note(
    feedback: str, hook_data: Dict[str, Any], file_path: str, config: Dict[str, Any]
) -> str:
    """Bug #163: lead a "no declaration" block reason with the re-declare note
    when a rejected edit of this same agent and file consumed the declaration
    within the marker TTL.

    Message-only: ``feedback`` is returned unchanged when the kill switch is
    off, there is no session_id, there is no unexpired marker, or the store
    fails (logged); the block itself is decided elsewhere and never altered.
    The note is pace-maker-authored (``intent_validation_block`` channel, as
    the other declare_intent hints) and the existing text stays below it.
    """
    if not intent_declaration_tool_enabled(config):
        return feedback
    session_id = _session_id(hook_data)
    if session_id is None:
        return feedback
    db_path = resolve_db_path()
    try:
        marked = IntentDeclarationStore(db_path).has_rejection_marker(
            session_id,
            _agent_key(hook_data),
            normalize_file_path(file_path, _cwd(hook_data)),
        )
    except _RECOVERABLE_ERRORS as exc:
        log_warning(
            "intent_declarations",
            "rejected-declaration marker lookup failed -- generic block text",
            exc,
        )
        return feedback
    if not marked:
        return feedback
    note = PromptLoader().load_prompt(_SIBLING_NOTE_TEMPLATE, subfolder="common")
    note = note.strip().replace("<file_path>", file_path)
    return format_tag(note, "intent_validation_block") + "\n\n" + feedback


def same_message_fallback(
    current_message_override: Optional[str],
    diagnostics: Dict[str, Any],
    file_path: str,
    hook_data: Dict[str, Any],
    config: Dict[str, Any],
) -> Optional[DeclaredIntent]:
    """The Write/Edit gate's transcript-fallback extra source (AC8), decided
    here so hook.py stays wiring: a ``declare_intent`` tool_use for THIS file
    inside the anchored turn, used only when the anchored turn carries no
    ``INTENT:`` text of its own (``current_message_override`` empty) and the
    kill switch is on. ``diagnostics`` is the anchor diagnostics dict
    (``anchor_declare_intents``). Callers invoke this only when no stored
    declaration/chain already supplied the intent."""
    if current_message_override or not intent_declaration_tool_enabled(config):
        return None
    return same_message_declared_intent(
        diagnostics.get("anchor_declare_intents"), file_path, _cwd(hook_data)
    )


def same_message_declared_intent(
    candidates: Any, file_path: str, cwd: Optional[str]
) -> Optional[DeclaredIntent]:
    """The transcript fallback's extra source: a ``declare_intent`` tool_use
    inside the ANCHORED turn (declaration and edit sent in one message).

    ``candidates`` are the tool_use ``input`` dicts of that turn's declare
    calls, oldest first. The newest complete one naming ``file_path``
    (compared after normalization) wins. Garbage input yields ``None``.
    """
    if not isinstance(candidates, Iterable) or isinstance(candidates, (str, bytes)):
        return None
    try:
        target = normalize_file_path(file_path, cwd)
    except _RECOVERABLE_ERRORS:
        return None
    for arguments in reversed(list(candidates)):
        if missing_required_fields(arguments):
            continue
        try:
            declared_path = normalize_file_path(arguments["file_path"].strip(), cwd)
        except _RECOVERABLE_ERRORS:
            continue
        if declared_path != target:
            continue
        return DeclaredIntent(
            SOURCE_DECLARATION,
            StoredIntent(
                target,
                _clean(arguments["change"]),
                _clean(arguments["goal"]),
                _clean(arguments.get("test_coverage")),
            ),
        )
    return None
