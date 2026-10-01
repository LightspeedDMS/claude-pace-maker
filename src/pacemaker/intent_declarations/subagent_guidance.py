"""
Guaranteed subagent guidance delivery (bug #157, fix 3).

SubagentStart is the normal channel for a subagent's pace-maker guidance and
provenance manifest, but it can be cancelled (hook timeout, machine load) --
and then the subagent gets NOTHING. Two hook-side seams close that gap:

- ``record_start_completed`` -- the very LAST step of ``run_subagent_start_hook``
  (after its output was written): "SubagentStart completed for (session_id,
  agent_id)".
- ``claim_late_guidance`` -- called from ``run_hook`` (PostToolUse) for tool calls
  that carry an ``agent_id``: True exactly ONCE per agent, and only if no
  completion was recorded -- then PostToolUse injects the same guidance.

Where the record lives (a deliberate reuse): the table ``subagent_guidance`` in
the #155 declaration store's DB (``intent_declarations.db``) rather than a new
DB/file, because that store already provides exactly what is needed -- keys by
(session_id, agent_key), a self-cleaning TTL purge on every access, WAL + busy
timeout for concurrent hook processes, and the env-override / test-mode guard.
The CSA registry (``session_registry.db``) was rejected: it is gated by
``cross_session_awareness_enabled`` (this delivery must work with it off), it is
read by the claude-usage monitor (a cross-process contract not to be widened for
this), and its agent rows are purged after 20 minutes. NOT governed by
``intent_declaration_tool_enabled``: the guidance text is decided by the
caller's config gates, not by that kill switch.

Failure policy: a store failure is logged at WARNING and means "do not inject"
(``claim`` -> False) / "not recorded" (``record`` -> False). The worst outcomes
are a duplicate guidance block (record lost) or a missed late delivery (claim
failed) -- never a crash, never an injection on every tool call. As in
``gate``, the test-mode ``RuntimeError`` from ``resolve_db_path()`` is NOT
swallowed, so a test that forgot its isolation fails loudly.
"""

import sqlite3
from typing import Any, Dict, Optional, Tuple

from ..logger import log_warning
from .store import IntentDeclarationStore, resolve_db_path

_RECOVERABLE_ERRORS = (sqlite3.Error, OSError, ValueError)


def _key(hook_data: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """``(session_id, agent_id)`` -- both required (a main-thread payload has
    no ``agent_id`` and is never tracked)."""
    session_id = hook_data.get("session_id")
    agent_id = hook_data.get("agent_id")
    if (
        isinstance(session_id, str)
        and session_id
        and isinstance(agent_id, str)
        and agent_id
    ):
        return session_id, agent_id
    return None


def record_start_completed(hook_data: Dict[str, Any]) -> bool:
    """Record that SubagentStart finished for this agent. True iff recorded."""
    key = _key(hook_data)
    if key is None:
        return False
    db_path = resolve_db_path()
    try:
        IntentDeclarationStore(db_path).mark_subagent_start_completed(*key)
    except _RECOVERABLE_ERRORS as exc:
        log_warning(
            "subagent_guidance",
            "could not record SubagentStart completion (a later first tool "
            "call may repeat the guidance)",
            exc,
        )
        return False
    return True


def needs_late_guidance(hook_data: Dict[str, Any]) -> bool:
    """Cheap, read-only pre-check for a PostToolUse payload: True iff it is a
    subagent's and neither a SubagentStart completion nor a late delivery is
    recorded yet. Not authoritative -- ``claim_late_guidance`` (atomic) still
    decides at emit time -- but it keeps the common path (every subagent tool
    call after the first) free of text building and write transactions. A store
    failure means "no" (never inject on every tool call)."""
    key = _key(hook_data)
    if key is None:
        return False
    db_path = resolve_db_path()
    try:
        return not IntentDeclarationStore(db_path).is_late_guidance_settled(*key)
    except _RECOVERABLE_ERRORS as exc:
        log_warning(
            "subagent_guidance",
            "late guidance pre-check failed -- not injecting",
            exc,
        )
        return False


def claim_late_guidance(hook_data: Dict[str, Any]) -> bool:
    """True iff this PostToolUse must inject the subagent guidance now: the
    payload is a subagent's, its SubagentStart never completed, and no late
    delivery happened yet (the claim is atomic and recorded)."""
    key = _key(hook_data)
    if key is None:
        return False
    db_path = resolve_db_path()
    try:
        return IntentDeclarationStore(db_path).claim_late_guidance(*key)
    except _RECOVERABLE_ERRORS as exc:
        log_warning(
            "subagent_guidance",
            "late guidance claim failed -- not injecting",
            exc,
        )
        return False
