#!/usr/bin/env python3
"""
Intent-based validation for Stop hook using Claude Agent SDK.

This module validates if Claude completed the user's original request by:
1. Extracting ALL user messages from transcript (complete user intent)
2. Extracting last N assistant messages from transcript (what Claude has been doing)
3. Extracting last assistant message from transcript (what Claude just said)
4. Calling SDK to act as user proxy and judge completion
5. Parsing SDK response (APPROVED or BLOCKED)
"""

import fnmatch
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .transcript_reader import (
    build_stop_hook_context,
    format_stop_hook_context,
    INTENT_MARKER_PATTERN,
)
from .constants import (
    DEFAULT_CONFIG,
    RECENT_CONTEXT_TERSE_MAX_CHARS,
)
from .logger import log_warning, log_debug
from .inference.verdict import is_positive, verdict_passes
from .prompt_provenance import format_tag, format_reviewer_relay


def _strip_llm_noise(text: str) -> str:
    """Strip all § intel/sentiment lines from an LLM response."""
    lines = text.splitlines()
    return "\n".join(line for line in lines if not line.strip().startswith("§"))


def _find_verdict(text: str) -> str:
    """Search for APPROVED or BLOCKED: as a standalone line anywhere in text.

    Returns "APPROVED", the raw BLOCKED line, or "" if neither found.
    BLOCKED takes priority over APPROVED when both appear.

    Positive detection uses guarded-lenient starts-with matching (via
    is_positive from inference.verdict), so "APPROVED.", "APPROVED — ok" etc.
    are accepted.  Strict equality is deliberately NOT used here.
    """
    cleaned = _strip_llm_noise(text)
    # Collect the first BLOCKED: line (we need the raw text for the reason).
    blocked_line = ""
    for line in cleaned.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("BLOCKED:"):
            blocked_line = stripped
            break
    if blocked_line:
        return blocked_line
    # Positive detection via canonical primitive (starts-with, not equality).
    if is_positive(cleaned, "APPROVED"):
        return "APPROVED"
    return ""


def get_config(key: str) -> Any:
    """
    Get configuration value for the given key.

    Args:
        key: Configuration key to retrieve

    Returns:
        Configuration value from DEFAULT_CONFIG
    """
    return DEFAULT_CONFIG.get(key)


def truncate_user_message(message: str, max_length: int) -> str:
    """
    Truncate user message if it exceeds max_length.

    Args:
        message: User message to potentially truncate
        max_length: Maximum allowed length for message

    Returns:
        Truncated message with suffix if over limit, otherwise original message
    """
    if not message or len(message) <= max_length:
        return message

    # Truncate and append suffix
    truncated = message[:max_length]
    return f"{truncated}[TRUNCATED>{max_length} CHARS]"


# Try to import Claude Agent SDK
try:
    import claude_agent_sdk  # type: ignore[import-not-found]  # noqa: F401

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False


def load_prompt_template(file_path: str) -> str:
    """
    Load validation prompt template from external file.

    Args:
        file_path: Path to the prompt template markdown file

    Returns:
        Template content as string

    Raises:
        FileNotFoundError: If the template file doesn't exist
    """
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def get_prompt_template() -> str:
    """
    Get validation prompt template from external file.

    Loads from: src/pacemaker/prompts/stop/stop_hook_validator_prompt.md

    Returns:
        Validation prompt template string

    Raises:
        FileNotFoundError: If template file is missing (installation broken)
        Exception: If template cannot be loaded
    """
    module_dir = os.path.dirname(__file__)
    prompt_path = os.path.join(
        module_dir, "prompts", "stop", "stop_hook_validator_prompt.md"
    )

    if not os.path.exists(prompt_path):
        raise FileNotFoundError(
            f"Stop hook validator prompt template not found at {prompt_path}. "
            "This indicates a broken installation. Run ./install.sh to fix."
        )

    return load_prompt_template(prompt_path)


def get_pre_tool_prompt_template() -> str:
    """
    Get pre-tool validation prompt template from external file.

    Loads from: src/pacemaker/prompts/pre_tool_use/pre_tool_validator_prompt.md

    Returns:
        Pre-tool validation prompt template string

    Raises:
        FileNotFoundError: If template file is missing (installation broken)
        Exception: If template cannot be loaded
    """
    module_dir = os.path.dirname(__file__)
    prompt_path = os.path.join(
        module_dir, "prompts", "pre_tool_use", "pre_tool_validator_prompt.md"
    )

    if not os.path.exists(prompt_path):
        raise FileNotFoundError(
            f"Pre-tool validator prompt template not found at {prompt_path}. "
            "This indicates a broken installation. Run ./install.sh to fix."
        )

    return load_prompt_template(prompt_path)


def build_validation_prompt(conversation_context: str) -> str:
    """
    Build SDK validation prompt from template.

    Args:
        conversation_context: Formatted conversation context from format_stop_hook_context()

    Returns:
        Complete validation prompt for SDK
    """
    # Get template from external file
    template = get_prompt_template()

    # Fill template
    return template.format(conversation_context=conversation_context)


def parse_sdk_response(response_text: str) -> Dict[str, Any]:
    """
    Parse SDK response into Claude Code Stop hook format.

    Expected formats:
    - "APPROVED" → {"continue": true}
    - "BLOCKED: feedback" → {"decision": "block", "reason": "feedback"}
    - Unexpected → {"continue": true} (fail open)

    Args:
        response_text: Raw SDK response text

    Returns:
        Decision dict for Claude Code Stop hook
    """
    verdict = _find_verdict(response_text)

    if verdict == "APPROVED":
        return {"continue": True}

    elif verdict.upper().startswith("BLOCKED:"):
        feedback = verdict[len("BLOCKED:") :].strip()
        return {"decision": "block", "reason": feedback}

    else:
        # Unexpected format - fail open
        return {"continue": True}


def call_sdk_validation(conversation_context: str, hook_model: str = "auto") -> str:
    """
    Synchronous SDK validation call via provider abstraction.

    Args:
        conversation_context: Formatted conversation context from format_stop_hook_context()
        hook_model: Model selection - "auto", "sonnet", "opus", "gpt-5.4", "gpt-5.5" (legacy alias: "gpt-5")

    Returns:
        SDK response text
    """
    if not SDK_AVAILABLE and hook_model in ("auto", "sonnet", "opus", "haiku"):
        raise ImportError("Claude Agent SDK not available")

    prompt = build_validation_prompt(conversation_context)

    from .inference import resolve_and_call

    return resolve_and_call(
        hook_model=hook_model,
        prompt=prompt,
        system_prompt="You are acting as the user who originally made this request. Judge if Claude delivered what you asked for.",
        call_context="stop_hook",
        max_thinking_tokens=4000,
    )


def validate_intent(
    session_id: str,
    transcript_path: str,
    conversation_context_size: int = 5,
    hook_model: str = "auto",
) -> Dict[str, Any]:
    """
    Validate if Claude completed user's original intent.

    Uses first-pairs + backwards-walk algorithm:
    1. Extract first N user/assistant pairs (session goals)
    2. Walk backwards from end to fill remaining token budget
    3. Format context with truncation marker
    4. Call SDK to validate completion
    5. Parse response and return decision

    Args:
        session_id: Session ID (currently unused but kept for compatibility)
        transcript_path: Path to conversation transcript
        conversation_context_size: Deprecated - now uses config settings

    Returns:
        Decision dict:
        - {"continue": True} - Allow exit
        - {"decision": "block", "reason": "feedback"} - Block with feedback
    """
    try:
        # Verify transcript exists
        if not os.path.exists(transcript_path):
            return {"continue": True}

        # Get config values
        from .constants import DEFAULT_CONFIG

        token_budget = DEFAULT_CONFIG.get("stop_hook_token_budget", 48000)
        first_n_pairs = DEFAULT_CONFIG.get("stop_hook_first_n_pairs", 10)

        # Build context using new algorithm
        context = build_stop_hook_context(
            transcript_path=transcript_path,
            first_n_pairs=first_n_pairs,
            token_budget=token_budget,
        )

        # Fail open if no context available
        if not context["first_pairs"] and not context["backwards_messages"]:
            return {"continue": True}

        # Format context for prompt
        formatted_context = format_stop_hook_context(context)

        # Call SDK for validation
        sdk_response = call_sdk_validation(formatted_context, hook_model=hook_model)

        # Log raw SDK response for debugging
        log_debug(
            "intent_validator",
            f"SDK raw response: {sdk_response[:500] if sdk_response else 'EMPTY'}",
        )

        # Parse response
        return parse_sdk_response(sdk_response)

    except Exception as e:
        # Any error - fail open (graceful degradation)
        log_debug("intent_validator", f"SDK ERROR (failing open): {e}")
        return {"continue": True}


def _build_intent_declaration_prompt(
    messages: List[str], file_path: str, tool_name: str
) -> str:
    """
    Build SDK prompt for checking if intent was declared using external template.

    Args:
        messages: Last N assistant messages
        file_path: Target file path
        tool_name: Tool being used (Write/Edit)

    Returns:
        Prompt string for SDK with variables replaced
    """
    from .prompt_loader import PromptLoader

    filename = os.path.basename(file_path)
    action = "create or modify" if tool_name == "Write" else "edit"

    messages_text = "\n\n".join(
        [f"Message {i+1}:\n{msg}" for i, msg in enumerate(messages)]
    )

    # Load template and replace variables
    loader = PromptLoader()
    return loader.load_prompt(
        "intent_declaration_prompt.md",
        subfolder="common",
        variables={
            "action": action,
            "filename": filename,
            "tool_name": tool_name,
            "messages_text": messages_text,
        },
    )


def _call_sdk_intent_validation(prompt: str, hook_model: str = "auto") -> str:
    """
    Synchronous SDK intent validation via provider abstraction.

    Args:
        prompt: Validation prompt
        hook_model: Model selection - "auto", "sonnet", "opus", "gpt-5.4", "gpt-5.5" (legacy alias: "gpt-5")

    Returns:
        SDK response text (YES or NO)
    """
    if not SDK_AVAILABLE and hook_model in ("auto", "sonnet", "opus", "haiku"):
        raise ImportError("Claude Agent SDK not available")

    from .inference import resolve_and_call

    return resolve_and_call(
        hook_model=hook_model,
        prompt=prompt,
        system_prompt="You are validating if intent was declared. Respond with YES or NO only.",
        call_context="intent_validation",
        max_thinking_tokens=2000,
    )


def validate_intent_declared(
    messages: List[str], file_path: str, tool_name: str
) -> Dict[str, Any]:
    """
    Validate if intent to modify file was declared in messages.

    Args:
        messages: Last N assistant messages from transcript
        file_path: Target file path
        tool_name: Tool being used (Write/Edit)

    Returns:
        {
            "intent_found": True/False
        }
    """
    try:
        # Build prompt
        prompt = _build_intent_declaration_prompt(messages, file_path, tool_name)

        # Call SDK
        response = _call_sdk_intent_validation(prompt)

        # Parse YES/NO response (case-insensitive)
        response_stripped = response.strip()

        # Empty response = infrastructure failure (API down, auth error, rate limit)
        # Fail-open: don't block writes due to infrastructure issues
        if not response_stripped:
            log_warning(
                "intent_validator",
                "SDK returned empty response (infrastructure failure), failing open",
            )
            return {"intent_found": True}

        response_upper = response_stripped.upper()

        if response_upper == "YES":
            return {"intent_found": True}
        else:
            # NO or any other non-empty response = no intent found
            return {"intent_found": False}

    except Exception as e:
        # Exception = infrastructure failure — fail-open
        log_warning(
            "intent_validator", "Intent declaration validation failed (failing open)", e
        )
        return {"intent_found": True}


def extract_current_assistant_message(messages: List[str], file_path: str = "") -> str:
    """
    Extract the CURRENT assistant message.

    With requestId grouping, text and tool_use from the same turn are
    already combined in messages[-1]. The 1-back check handles the
    ungrouped fallback (missing requestId / older Claude Code) where
    they are separate entries.

    Only checks messages[-2] — never further back, to avoid picking up
    stale intent declarations from previous turns.

    Issue #140 (code-review finding 3): this function's marker check is
    intentionally PLAIN -- ``_has_intent_marker(current_tool)`` /
    ``_has_intent_marker(prev)`` on the string exactly as given, with no
    string-splitting on any "[TOOL: ...]"-shaped substring. An earlier
    attempt at this fix stripped a rendered blob down to "everything before
    the first '[TOOL: ' occurrence" -- that is unsafe against prose that
    legitimately QUOTES the marker text (e.g. "the log showed
    `[TOOL: Bash]`." followed by a real INTENT declaration), which would be
    truncated away and produce a false block. The actual hardening against
    an ``INTENT:``-looking string embedded only in rendered tool parameters
    (a Write's ``content``, an Edit's ``old_string``/``new_string``) now
    lives STRUCTURALLY upstream: the real hook path
    (``validate_intent_and_code`` via ``hook.py``) supplies this function
    with messages built from
    ``transcript_reader.get_last_n_messages_for_validation(...,
    _with_prose=True)``, which derives prose directly from the JSONL's own
    text-block boundaries -- there is simply no tool-rendered content in
    the strings this function receives from the real pipeline, so no
    stripping is ever needed here.

    When ``file_path`` is provided, the selected message is checked with a
    defense-in-depth guard: if the message carries an INTENT: marker but does
    NOT mention the target file (by basename or full path), "" is returned to
    prevent a stale wrong-file turn from false-passing Stage 1.  Messages that
    lack an INTENT: marker are returned as-is so their diagnostic log is
    preserved (bug #83).
    """
    if not messages:
        return ""

    if len(messages) == 1:
        result = messages[-1]
    else:
        current_tool = messages[-1]

        if _has_intent_marker(current_tool):
            result = current_tool
        else:
            prev = messages[-2]
            if prev and _has_intent_marker(prev):
                result = f"{prev}\n\n{current_tool}"
            else:
                result = current_tool

    # Defense-in-depth (bug #83): discard ONLY when the selected message has an
    # INTENT: marker AND does NOT mention the target file.  The _has_intent_marker
    # guard is required — without it, no-intent messages that don't name the file
    # would also be silently discarded, swallowing their Stage-1 diagnostic logs.
    if (
        file_path
        and result
        and _has_intent_marker(result)
        and not _mentions_file(result, file_path)
    ):
        return ""

    return result


def _has_intent_marker(text: str):
    """Return re.Match if INTENT: marker found (case-insensitive), else None."""
    return INTENT_MARKER_PATTERN.search(text)


def _mentions_file(text: str, file_path: str) -> bool:
    """Return True if basename or full path appears in text."""
    basename = os.path.basename(file_path)
    return basename in text or file_path in text


def _mentions_file_basename_or_stem(text: str, file_path: str) -> bool:
    """Issue #151 live-replay follow-up (round 3, CHANGE 1): case-
    insensitive check for whether ``text`` mentions the target file's
    basename (e.g. "group_access_manager.py") OR its STEM (basename
    without extension, e.g. "group_access_manager"). Deliberately
    distinct from ``_mentions_file`` above, which checks the FULL path
    case-sensitively for an unrelated purpose (bug #83's stale-anchor
    discard guard) -- reusing it here would miss "group_access_manager"
    without its extension and would never match on case differences."""
    if not file_path:
        return False
    basename = os.path.basename(file_path)
    if not basename:
        return False
    stem = os.path.splitext(basename)[0]
    lowered = (text or "").lower()
    if basename.lower() in lowered:
        return True
    return bool(stem) and stem.lower() in lowered


def _should_include_recent_context(intent_text: str, file_path: str) -> bool:
    """Issue #151 live-replay follow-up (round 3, CHANGE 1): decide
    whether RECENT CONTEXT should be shown in the relaxed Stage 2 prompt
    at all, given the CURRENT turn's own combined intent text.

    Live evidence -- three false blocks where a DETAILED, file-naming
    current intent was overridden by an EARLIER turn's stale plan
    surfaced via RECENT CONTEXT ("Reverting A" judged against "create
    variant A", "Restoring B, then variant C" judged against "remove ...
    variant B", a detailed docstring-update intent judged against
    "delete get_audit_logs"). The prompt's own wording guards were not
    enough -- this is a CODE decision, made before the prompt is even
    built, not left to a weak reviewer's own judgment.

    Rule: include it only when the intent is "terse": shorter than
    RECENT_CONTEXT_TERSE_MAX_CHARS, OR it does not mention the target
    file's basename/stem. Either condition alone is enough -- a long
    intent that never names the file is still ambiguous enough to
    benefit from context; a short intent that DOES name the file is
    still short enough to be genuinely terse.

    Issue #154 live-replay follow-up: an earlier revision ALSO withheld
    RECENT CONTEXT whenever the intent contained an explicit direction
    word (remove/revert/restore/etc.), on the theory that such an intent
    already states its own self-contained goal. That clause was REMOVED:
    live cases F1 "Reverting A..." and F2 "Restoring B, then variant C"
    are terse restore intents that only make sense with the EARLIER
    turns defining what "A"/"B" refer to -- withholding RECENT CONTEXT
    for them made a weak reviewer (haiku) block both at CHECK 0 as "too
    vague". The direction-MISJUDGMENT risk that clause guarded against is
    now handled by CHANGE 3's unified diff (explicit `-`/`+` markers)
    instead of a code-level suppression here.
    """
    stripped = (intent_text or "").strip()
    if len(stripped) < RECENT_CONTEXT_TERSE_MAX_CHARS:
        return True
    return not _mentions_file_basename_or_stem(stripped, file_path)


def _is_core_path(
    file_path: str,
    core_path_segments: List[str],
    exclusions: List[str],
    extensions: List[str],
) -> bool:
    """Return True if file_path is a core (production) code path requiring
    a TDD test-coverage declaration (issue #92 3-layer algorithm).

    Layer 0 — universal negative signals, evaluated BEFORE any positive
    match (Layer 1 or Layer 2/2c) so no core-path word or marker can ever
    bypass them: excluded path, non-source-code extension, or a
    test-filename-suffix pattern (e.g. *_test.go, which has no
    directory-level exclusion signal at all).

    Layer 1 — fast path: bare-segment word-list match, config-driven via
    core_path_segments (e.g. core_paths.yaml).

    Layer 2 (+2c) — structural marker-file fallback, only reached when
    Layer 1 misses: an uncapped upward directory walk for per-ecosystem
    project markers, excluding a found marker that is itself a dedicated
    test project.

    Args:
        file_path: Target file path (relative or absolute)
        core_path_segments: Bare-segment word list (e.g. from
            core_paths.load_paths_with_migration())
        exclusions: Excluded path prefixes/patterns (e.g. from
            excluded_paths.load_exclusions())
        extensions: Recognized source-code extensions (e.g. from
            extension_registry.load_extensions())

    Returns:
        True if file_path requires a TDD test-coverage declaration
    """
    from .excluded_paths import is_excluded_path
    from .extension_registry import is_source_code_file
    from .core_path_markers import matches_test_filename_pattern, has_core_marker

    # Layer 0 — universal negative signals.
    if is_excluded_path(file_path, exclusions):
        return False
    if not is_source_code_file(file_path, extensions):
        return False
    if matches_test_filename_pattern(file_path):
        return False

    # Layer 1 — fast path: bare-segment word-list match.
    # A segment that reduces to "" after stripping trailing slashes (e.g.
    # a bare "/") must be filtered out here, at the regex-construction
    # site itself — not just at the CLI's add_path() layer — because a
    # hand-edited core_paths.yaml bypasses that CLI guard entirely. An
    # empty segment would otherwise become an empty regex alternation
    # branch that matches every path (issue #92 review finding N-2).
    escaped_segments = [
        re.escape(stripped)
        for seg in core_path_segments
        if (stripped := seg.rstrip("/"))
    ]
    if escaped_segments:
        pattern = r"(?:^|/)(?:" + "|".join(escaped_segments) + r")/"
        if re.search(pattern, file_path):
            return True

    # Layer 2 (+2c) — structural marker-file fallback.
    return has_core_marker(file_path)


def _is_version_bump(intent_text: str) -> bool:
    """Return True if intent_text describes a version bump.

    Robust to phrasings where the word "version" is embedded in a token such
    as ``__version__`` or wrapped in backticks/quotes (no clean word boundary).
    A bump is recognized when EITHER:
      - a bump verb (bump|update|change|set|version bump) co-occurs with a
        version token (``version`` anywhere, including ``__version__``,
        backticked or quoted forms) AND a digit appears, OR
      - a bump verb co-occurs with a semver literal (``X.Y.Z``).
    A semver literal alone, without a bump verb, is NOT treated as a bump to
    avoid over-matching unrelated text.
    """
    has_bump_verb = bool(
        re.search(r"(?i)\b(bump|update|change|set)\b", intent_text)
        or re.search(r"(?i)version\s+bump", intent_text)
    )
    if not has_bump_verb:
        return False

    has_version_token_with_digit = bool(
        re.search(r"(?i)version\b.*?\d", intent_text, re.DOTALL)
        or re.search(r"(?i)version[`'\"]*.*?\d", intent_text, re.DOTALL)
    )
    has_semver = bool(re.search(r"\d+\.\d+\.\d+", intent_text))
    return has_version_token_with_digit or has_semver


def _has_tdd_declaration(intent_text: str) -> bool:
    """Return True if a structured TDD or skip-TDD declaration is present.

    Accepts the exact markers the NO_TDD feedback message prescribes
    (``TEST FILE:`` / ``TEST SCOPE:``) as well as the legacy
    ``Test coverage:`` / ``covered by`` / ``test:`` forms. A marker only
    counts when it is followed by an actual declaration body (``\\S``) so a
    bare colon with nothing after it is not a false positive.
    """
    # Colon-markers: the prescribed "TEST FILE:" / "TEST SCOPE:" plus legacy
    # "Test coverage:" / "test:" — require a same-line declaration body after
    # the colon so a bare colon (nothing after) is not a false positive.
    if re.search(
        r"(?i)\b(test\s+file|test\s+scope|test\s+coverage|test)\s*:[^\S\n]*\S",
        intent_text,
    ):
        return True
    # "covered by <something>" — legacy form without a colon.
    if re.search(r"(?i)covered\s+by\s+\S+", intent_text):
        return True
    return bool(re.search(r"(?i)user\s+permission\s+(to\s+)?skip\s+tdd", intent_text))


def _regex_stage1_check(
    current_message: str,
    file_path: str,
    exclusions: list,
    core_path_segments: Optional[List[str]] = None,
    extensions: Optional[List[str]] = None,
    tool_declared_tdd: Optional[bool] = None,
) -> str:
    """Regex-based Stage 1 structural check. Returns YES, NO, or NO_TDD.

    ``tool_declared_tdd`` (Story #155 / review M2): ``None`` (default) is a
    TEXT declaration -- TDD satisfaction and the version-bump exemption are
    found by scanning the text after ``INTENT:``, exactly as before. A bool
    marks a TOOL-sourced (declare_intent) intent, whose message is
    synthesized from structured fields: free-form change/goal wording (e.g.
    "covered by existing tests", "fix failing test: x") and the injected
    absolute path (a bump verb + a version-ish path) must NOT satisfy TDD,
    so on a core path the answer is decided ONLY by this flag -- True iff a
    non-blank structured ``test_coverage`` was declared -- and no
    version-bump exemption applies.

    Args:
        current_message: Current assistant message text
        file_path: Target file path (relative or absolute)
        exclusions: List of excluded path prefixes (e.g. ["tests/", ".tmp/"])
        core_path_segments: Optional bare-segment word list for Layer 1 of
            _is_core_path(). Defaults to core_paths.get_default_paths()
            (pure, in-memory, no file I/O) when not supplied — the real
            hook entry point (validate_intent_and_code) passes the
            migrated on-disk list via core_paths.load_paths_with_migration().
        extensions: Optional recognized source-code extensions for Layer 0
            of _is_core_path(). Defaults to
            extension_registry.get_default_extensions() (pure, in-memory,
            no file I/O) when not supplied.

    Returns:
        "YES"    – intent declared, file mentioned, TDD satisfied (or not required)
        "NO"     – intent marker missing, file not mentioned, or invalid file_path
        "NO_TDD" – intent and file present, core path, but TDD not declared
    """
    if not file_path or not os.path.basename(file_path):
        return "NO"

    # Issue #140 (code review finding 3): current_message is a PLAIN string
    # here -- no string-splitting on any "[TOOL: ...]"-shaped substring
    # (that approach is unsafe against prose that legitimately quotes the
    # marker text; see extract_current_assistant_message's docstring for
    # the full rationale). The actual "must be prose only" guarantee is
    # structural, enforced by the CALLER: validate_intent_and_code always
    # passes a current_message built from either
    # transcript_reader's anchor_prose_text diagnostic or
    # get_last_n_messages_for_validation(..., _with_prose=True) on the real
    # hook path, so there is no rendered tool content in current_message to
    # begin with. That same guarantee is what closes findings 1 (the
    # TDD-declaration/version-bump check below, over
    # current_message[intent_match.end():]) and 2 (_mentions_file, just
    # below) -- both operate on current_message AS GIVEN, and it is prose
    # only by construction, not by any stripping performed here.
    intent_match = _has_intent_marker(current_message)
    if not intent_match:
        return "NO"

    if not _mentions_file(current_message, file_path):
        return "NO"

    if core_path_segments is None:
        from .core_paths import get_default_paths

        core_path_segments = get_default_paths()
    if extensions is None:
        from .extension_registry import get_default_extensions

        extensions = get_default_extensions()

    if not _is_core_path(file_path, core_path_segments, exclusions, extensions):
        return "YES"

    if tool_declared_tdd is not None:
        return "YES" if tool_declared_tdd else "NO_TDD"

    intent_text = current_message[intent_match.end() :]
    if _is_version_bump(intent_text) or _has_tdd_declaration(intent_text):
        return "YES"

    return "NO_TDD"


def _log_stage1_rejection(verdict: str, file_path: str, current_message: str) -> None:
    """Log the Stage-1-rejection WARNING using the EXACT message evaluated.

    ``current_message`` MUST be the effective message used by
    ``_regex_stage1_check`` to compute ``verdict`` (i.e.
    ``current_message_override or extract_current_assistant_message(...)``).
    Requiring it as an explicit parameter — rather than the caller inlining
    a closure over a local variable — makes it structurally impossible for a
    future refactor to log a different/stale value than the one that
    actually produced the verdict (Fix 4 diagnostic, hardened).
    """
    log_warning(
        "intent_validator",
        f"Stage 1 rejection: verdict={verdict} "
        f"file={file_path} "
        f"current_message[:200]={current_message[:200]!r}",
    )


def build_reviewer_unavailable_message(degradation: Optional[Dict[str, Any]]) -> str:
    """Build the pace-maker-authored explanation for a zero-survivor review
    (issue #142): every configured reviewer failed to respond at all —
    timeout, ProviderError (e.g. CLI not found), or any other infrastructure
    failure — so there is no third-party feedback to relay.

    Public (not `_`-prefixed): shared across module boundaries by both the
    Write/Edit Stage 2 gate (intent_validator.py) and the danger-bash Phase
    2 gate (hook.py imports it directly).

    Previously the blank response was wrapped in format_reviewer_relay() and
    recorded under the "intent_validation_cleancode"/"intent_validation_
    dangerbash" categories, producing an empty-looking rejection
    indistinguishable from a genuine rejection. This builds a real
    explanation from the _degradation out-param's "failed_providers"
    ({model: reason}), populated by resolve_and_call_with_reviewer()/
    run_mechanical() on their zero-survivor paths (see inference/registry.py
    and inference/competitive.py).

    Wording is deliberately neutral about WHY nobody responded — "timed out"
    would misdescribe a non-timeout failure such as a missing CLI binary,
    and this message is shared by both gates (danger-bash, not just
    Write/Edit), so it must not bake in Write/Edit-specific phrasing.

    Degrades gracefully when degradation info is missing/empty — still
    returns a non-empty, actionable message rather than raising.
    """
    failed = (degradation or {}).get("failed_providers") or {}
    if failed:
        detail = "; ".join(f"{model}: {reason}" for model, reason in failed.items())
        providers_text = f" ({detail})"
    else:
        providers_text = ""
    return (
        f"⛔ No reviewer responded{providers_text}. This is a reviewer "
        "infrastructure failure, NOT a rejection of your intent or code — "
        "re-issue the identical tool call."
    )


def build_no_visible_text_notice(intent_example: str) -> str:
    """Issue #150: build the pilot-validated "lead with the fix" notice for
    a Stage-1/Phase-1 block whose anchored turn had NO visible text at all.

    Public (not `_`-prefixed): shared across module boundaries by both the
    Write/Edit Stage 1 gate (this module) and the danger-bash Phase 1 gate
    (hook.py imports it directly) — same sharing pattern as
    ``build_reviewer_unavailable_message`` above.

    Replaces the old design (issue #141/#148's ``transcript_reader.
    THINKING_ONLY_NOTICE``, appended at the END of the generic block
    template). A pilot (see CLAUDE.md's "Issue #150" section) measured that
    an Opus 5.5 agent at xhigh reasoning effort recovers far more reliably
    when this text LEADS the block reason, with a ready-to-copy INTENT:
    example naming the actual target, rather than a generic notice
    appended after a long template the model has already stopped reading.

    Args:
        intent_example: A single, ready-to-copy "INTENT: ..." line (or
            multi-line, when a "Test coverage:" line is appended by the
            caller) naming the ACTUAL target — a file path for Write/Edit
            ("INTENT: Edit <path> to <change>, so that <goal>.") or a
            command preview for danger-bash ("INTENT: Run <command
            preview> to <goal>."). Callers build this; this function only
            renders the shared surrounding wording (Messi Rule 11 — kept
            in prompts/common/no_visible_text_notice.md, not inlined
            here).

            NOT passed through PromptLoader's ``variables=`` templated
            substitution (code-review follow-up, item 1) — the ACTUAL
            file_path/command can itself legitimately contain a
            "{{word}}"-shaped substring (a templated path, or a sed/awk
            script's own placeholder marker), and PromptLoader rescans
            the SUBSTITUTED result for exactly that shape, raising
            ValueError on a false-positive match. The .md template's own
            ``{{intent_example}}`` placeholder was removed; this function
            instead appends ``intent_example`` in code, AFTER loading the
            static template text, so nothing in it is ever rescanned.

    Returns:
        The rendered notice text (untagged — callers decide tagging via
        format_tag/format_reviewer_relay, per Story #101).

    Do NOT reword the surrounding paragraph loaded from the .md file: the
    pilot found that some rewordings (e.g. asking the model to "write the
    INTENT: line as a visible sentence") are refused by the API's
    [reasoning_extraction] safeguard, and the shipped wording caused zero
    refusals across 20 pilot sessions. That pilot covered ONLY the
    SessionStart/SubagentStart guidance's "C text" (see
    ``display_intent_validation_guidance()``'s docstring/CLAUDE.md's
    "Issue #150" section) — the wording rendered by THIS function (the
    block-notice paragraph loaded from no_visible_text_notice.md) was
    NOT itself piloted. It reuses the same core sentence deliberately (to
    stay consistent with the piloted guidance and avoid inventing new,
    unvetted phrasing), but its live effect on recovery rate is unproven
    until issue #150's own step 3 (re-run the xhigh pilot against the
    real gate once intent validation is re-enabled).
    """
    from .prompt_loader import PromptLoader

    loader = PromptLoader()
    template = loader.load_prompt("no_visible_text_notice.md", subfolder="common")
    return f"{template.rstrip()}\n\n{intent_example}"


def _write_edit_no_visible_text_example(
    tool_name: str, file_path: str, is_core_path: bool
) -> str:
    """Issue #150 code-review follow-up (item 3): build the ready-to-copy
    INTENT: example for the Write/Edit no-visible-text notice, including a
    "Test coverage:" line whenever the target is a core path.

    Without this, a core-path edit with no visible text at all gets
    blocked TWICE in a row: once for the missing INTENT (the "NO" branch
    -- reachable on a core path too, since a missing INTENT marker always
    resolves to "NO" regardless of core-path-ness, per
    _regex_stage1_check's docstring), and again for the missing test
    declaration once the agent adds only an INTENT line (the "NO_TDD"
    branch). Folding both requirements into ONE example lets the agent
    fix both in a single re-issue.

    Kept out for non-core paths, since Test coverage: is not required
    there and adding it would be actively wrong guidance.
    """
    example = f"INTENT: {tool_name} {file_path} to <change>, so that <goal>."
    if is_core_path:
        example += "\nTest coverage: <test file> - <test name>"
    return example


def build_declare_intent_hint(file_path: str) -> str:
    """Story #155 AC11: the one-sentence pointer that Stage-1 block messages
    (NO / NO_TDD, including the no-visible-text notice's) carry when the
    declare_intent tool path is enabled -- "call `declare_intent` for <file>
    (preferred), or write the INTENT: line, then re-issue".

    The wording lives in prompts/common/declare_intent_hint.md (Messi Rule
    11) as STATIC text with a literal ``<file_path>`` token that is replaced
    here with ``str.replace`` -- never PromptLoader's ``variables=``
    substitution, whose rescan for ``{{word}}`` placeholders would crash on
    a real path/command that itself contains one (the #150 code-review
    item-1 hazard).
    """
    return _load_static_hint("declare_intent_hint.md", file_path)


def _load_static_hint(template_name: str, file_path: str = "") -> str:
    """Load a static ``prompts/common`` hint template and replace its literal
    ``<file_path>`` token with ``str.replace`` -- never PromptLoader's
    ``variables=`` (the #150 item-1 ``{{word}}`` rescan hazard)."""
    from .prompt_loader import PromptLoader

    template = PromptLoader().load_prompt(template_name, subfolder="common")
    return template.strip().replace("<file_path>", file_path)


def build_declare_intent_review_hint(file_path: str) -> str:
    """Bug #159 (review M1): the hint for a STAGE 2 rejection. The CODE was
    rejected, so unlike the Stage-1 hint ("...then re-issue this call") this
    one starts with "Address the review above" -- a weak model must never read
    it as "declare, then re-issue the identical call"."""
    return _load_static_hint("declare_intent_hint_review.md", file_path)


def build_declare_intent_deferred_hint(file_path: str) -> str:
    """Bug #159 (review L1): the hint for the transcript-timing-race
    "deferred" block, worded as a faster alternative to that block's own
    "re-issue the identical call" instruction, not a competing one."""
    return _load_static_hint("declare_intent_hint_deferred.md", file_path)


def build_declare_intent_consumed_note() -> str:
    """Bug #159 (review L2): the note for a Stage 2 rejection of a
    tool-declared/chain intent. #155's rules consumed that declaration (and
    ended the chain) with this rejection, so the retry needs a fresh one."""
    return _load_static_hint("declare_intent_consumed_note.md")


def build_declare_intent_unavailable_consumed_note() -> str:
    """Bug #159 (re-review): the variant of the consumed note for a
    reviewer-UNAVAILABLE block of a tool/chain intent. The declaration was
    consumed and the chain ended just the same, but nothing was reviewed, so
    there is no review to address -- only "declare again"."""
    return _load_static_hint("declare_intent_consumed_note_unavailable.md")


def _append_declare_intent_hint(
    feedback: str, file_path: str, enabled: bool, intent_from_tool: bool = False
) -> str:
    """Bug #159: append pace-maker guidance to a Claude-facing Stage 2
    rejection, OUTSIDE the reviewer-relay segment.

    ``feedback`` is the already-wrapped ``format_reviewer_relay`` text; the
    reviewer's words are never altered. The addition is pace-maker-authored,
    so it follows as its own ``intent_validation_block``-tagged block (the
    channel the Stage-1 hint already travels in). Which text is appended
    depends on where the intent came from:

    * transcript-sourced (default): the review hint -- "address the review,
      declare when you retry" (``build_declare_intent_review_hint``);
    * ``intent_from_tool``: the agent already used the tool, but the
      rejection consumed its declaration/chain, so only the "declare again"
      note (``build_declare_intent_consumed_note``).

    Nothing when the tool path is off (``enabled`` False -- byte-identical to
    before). The governance ``raw_feedback`` is never passed through here.
    """
    if not enabled:
        return feedback
    addition = (
        build_declare_intent_consumed_note()
        if intent_from_tool
        else build_declare_intent_review_hint(file_path)
    )
    return feedback + "\n\n" + format_tag(addition, "intent_validation_block")


def _sdk_unavailable_message() -> str:
    """Issue #151 code review L2 (Messi Rule 4): the SDK-unavailable
    fail-closed message is IDENTICAL regardless of which Stage 1 path
    (normal/declaration or the reasoning-summary exception) reached Stage
    2 -- Stage 2's SDK dependency doesn't change based on intent source.
    Single source of truth, replacing what used to be two independently
    maintained copies of this exact string."""
    return """⛔ Intent Validation Unavailable

Claude Agent SDK is not available for Stage 2 code review.

This is a REQUIRED dependency for intent validation to function.
Please install the SDK or disable intent validation in config:

  pace-maker tdd off

System failing closed to prevent bypassing intent declaration requirements."""


def build_danger_bash_reasoning_summary_label() -> str:
    """Issue #151: the danger-bash Phase 2 prompt's assistant-message label
    when the intent came from a reasoning summary (exception models only,
    and only when there is no visible INTENT: marker -- see
    resolve_reasoning_summary_intent_source). Externalized (Messi Rule 11)
    rather than inlined in hook.py, mirroring build_no_visible_text_notice's
    sharing pattern.
    """
    from .prompt_loader import PromptLoader

    loader = PromptLoader()
    return loader.load_prompt(
        "danger_bash_reasoning_summary_label.md", subfolder="common"
    ).strip()


def build_danger_bash_visible_text_label() -> str:
    """Issue #151 code review M2: the danger-bash Phase 2 prompt's
    assistant-message label when the intent came from RELAXED visible
    text with NO formal INTENT: marker (exception models only). Must
    never claim "(contains intent declaration)" -- there was no formal
    declaration, only ordinary assistant prose."""
    from .prompt_loader import PromptLoader

    loader = PromptLoader()
    return loader.load_prompt(
        "danger_bash_visible_text_label.md", subfolder="common"
    ).strip()


def build_danger_bash_destructive_scope_note() -> str:
    """Issue #151 live-replay follow-up (round 3, CHANGE 4): a live false
    block on `cp X backup && rm X && grep -rn refs src/ docs/ tests/` with
    the intent "delete the partial template" -- the reviewer correctly
    matched the `rm` against the intent, then blocked anyway because the
    read-only `grep` wasn't mentioned. Alignment must be judged ONLY
    against the destructive/dangerous operations in the command (the ones
    that matched the danger rules, or that modify/delete state); extra
    read-only or non-destructive steps in the same command (searches,
    listings, reads, printing, copying to a backup) are not themselves a
    mismatch just because the intent doesn't name them. Shared by BOTH
    the strict and relaxed Phase 2 wording variants (issue #87
    weak-verifier rule: kept short).
    """
    from .prompt_loader import PromptLoader

    loader = PromptLoader()
    return loader.load_prompt(
        "danger_bash_destructive_scope_note.md", subfolder="common"
    ).strip()


def build_danger_bash_relaxed_intent_wording() -> "tuple[str, str, str]":
    """Issue #151 code review M2: the danger-bash Phase 2 prompt's intro
    sentence, VALIDATE item 1 wording, and DIFFERENT-tool-call mismatch
    line, for the RELAXED intent sources (visible_text/reasoning_summary)
    ONLY. The STRICT/declaration wording stays inline in hook.py,
    unchanged (byte-identical for non-exception models and exception-model
    turns that already declared a real INTENT: -- see
    resolve_reasoning_summary_intent_source).

    Returns (intro, validate_item1, mismatch_line) -- three paragraphs
    parsed out of one externalized template (Messi Rule 11), separated by
    blank lines.
    """
    from .prompt_loader import PromptLoader

    loader = PromptLoader()
    text = loader.load_prompt(
        "danger_bash_relaxed_intent_wording.md", subfolder="common"
    )
    paragraphs = [p.strip() for p in text.strip().split("\n\n") if p.strip()]
    return paragraphs[0], paragraphs[1], paragraphs[2]


def _build_recent_context_section(
    recent_context: Optional[List["tuple[str, str]"]],
    max_chars: int = 2000,
) -> str:
    """Issue #151 live-test follow-up: build the relaxed Stage 2 prompt's
    "RECENT CONTEXT" section -- up to a few PRIOR turns' prose-only
    visible text and non-empty thinking/reasoning-summary text, given to
    the reviewer as clarifying context (never as the intent itself).

    Repro (real reviewer replay, haiku): an Opus 5.5 turn's visible text
    ("Now the SSH, git, tool-access and API-key test expectations.") with
    no thinking was rejected 3x at CHECK 0 ("reads as a section header").
    The PREVIOUS turn's reasoning summary held the full plan, but the
    relaxed path is anchor-only (deliberately, per #93/#139's ANCHOR-ONLY
    invariant for the CURRENT turn's own intent) -- it never saw it. This
    section is the fix: additive CONTEXT, not a relaxation of what counts
    as the current turn's own intent.

    Args:
        recent_context: ``(visible_text, thinking_text)`` tuples, ONE per
            prior turn, in OLDEST-TO-NEWEST order, with the current
            (anchor) turn already excluded by the caller. Each string
            must already be PROSE-ONLY (#140 -- never rendered tool
            parameters, ``new_string``, or ``content``); this function
            trusts that contract and does no extraction of its own. Turns
            where both strings are empty are dropped.
        max_chars: Total character budget for the rendered turn bodies
            (header/instructions text is NOT counted against this, since
            it is fixed-size and always the same). When the turns don't
            fit, OLDEST turns are dropped first, so the MOST RECENT turns
            are always kept. If even the single most recent turn alone
            exceeds the budget, it is kept but truncated to its TAIL (the
            most recent/specific part of that turn's own text), rather
            than dropped entirely.

    Returns:
        The rendered section (including its own header/instructions), or
        "" when there is nothing to show (``recent_context`` is falsy, or
        every turn is empty).
    """
    if not recent_context:
        return ""

    # Each turn is kept as (label, text) PAIRS, not a pre-joined string --
    # round 2 item 4: a prior version blind-sliced the whole joined
    # "Label: text" block from the right when truncating a single
    # oversized turn, which could chop the "Visible text:"/"Reasoning
    # summary:" label itself clean off the front. Keeping labels and
    # values separate lets the truncation fallback below shrink only the
    # VALUE portion, never the label.
    turns: List[List[Tuple[str, str]]] = []
    for visible_text, thinking_text in recent_context:
        visible_text = (visible_text or "").strip()
        thinking_text = (thinking_text or "").strip()
        pairs: List[Tuple[str, str]] = []
        if visible_text:
            pairs.append(("Visible text", visible_text))
        if thinking_text:
            pairs.append(("Reasoning summary", thinking_text))
        if not pairs:
            continue
        turns.append(pairs)

    if not turns:
        return ""

    def _render(pairs: List[Tuple[str, str]]) -> str:
        return "\n".join(f"{label}: {text}" for label, text in pairs)

    def _render_capped(pairs: List[Tuple[str, str]], budget: int) -> str:
        """Render a single turn's (label, text) pairs so the result fits
        within ``budget`` chars WITHOUT ever dropping a line's label --
        only each line's VALUE is truncated (kept to its TAIL, the most
        recent/specific text), the "Label: " prefix is always preserved."""
        full = _render(pairs)
        if len(full) <= budget:
            return full
        n = len(pairs)
        separators = n - 1  # "\n" between lines
        prefixes = sum(len(label) + 2 for label, _ in pairs)  # "Label: "
        remaining = max(n, budget - separators - prefixes)
        per_line = max(1, remaining // n)
        return "\n".join(f"{label}: {text[-per_line:]}" for label, text in pairs)

    # Walk newest -> oldest, keeping whole turns while they fit the
    # budget, so the MOST RECENT turns are preferentially kept.
    kept: List[str] = []
    total = 0
    for pairs in reversed(turns):
        rendered = _render(pairs)
        added = len(rendered) + (2 if kept else 0)  # "\n\n" separator
        if not kept and added > max_chars:
            # Even the single most recent turn alone exceeds the budget --
            # keep it anyway, truncated (labels preserved), rather than
            # showing nothing at all, and stop (an older turn can't fit
            # either).
            kept.append(_render_capped(pairs, max_chars))
            break
        if kept and total + added > max_chars:
            break
        kept.append(rendered)
        total += added
    kept.reverse()  # restore oldest -> newest for display

    body = "\n\n".join(kept)

    note_path = os.path.join(
        os.path.dirname(__file__),
        "prompts",
        "pre_tool_use",
        "reasoning_summary_recent_context_note.md",
    )
    header = load_prompt_template(note_path)
    # Round 2 item 4 (cosmetic): the leading "\n" here is the ONLY blank-
    # line separator between {intent_text} and this section -- the
    # template no longer has its own hardcoded blank line before
    # {recent_context_section}, so an empty return ("") leaves exactly one
    # blank line (from the template's own newlines) instead of two.
    return f"\n{header.rstrip()}\n\n{body}\n"


# Issue #153: fail-safe cap on the on-disk file size read for surrounding
# edit context -- a file larger than this is skipped (short note only)
# rather than fully read into memory just to locate one substring.
_SURROUNDING_CONTEXT_MAX_FILE_BYTES = 2_000_000


def _surrounding_context_note(note: str) -> str:
    """Short, single-line fallback for
    ``_build_edit_surrounding_context_section`` -- never a full crash,
    never a large blank gap, just a brief explanation of why the section
    was omitted (issue #153 fail-safe requirement)."""
    return f"\nSURROUNDING FILE CONTEXT: {note}\n"


# Issue #153 code review MUST-FIX 1(b): path-pattern heuristic for files
# that are ALMOST CERTAINLY secret-bearing, independent of whether their
# content happens to contain any value already registered in the secrets
# store. Matched case-insensitively against the file's BASENAME only.
_SECRET_LIKE_PATH_PATTERNS = (
    "*.env*",
    "*secret*",
    "*credential*",
    "*.pem",
    "*.key",
    "*.p12",
    "id_rsa*",
)


def _is_secret_like_path(file_path: Optional[str]) -> bool:
    """Issue #153 code review MUST-FIX 1(b): return True when
    ``file_path``'s basename matches a known secret-like naming pattern
    (``*.env*``, ``*secret*``, ``*credential*``, ``*.pem``, ``*.key``,
    ``*.p12``, ``id_rsa*``), case-insensitively. Pure/no I/O -- never
    raises, even on ``None``/empty input (defensively returns False)."""
    try:
        name = os.path.basename(file_path or "").lower()
    except Exception:
        return False
    if not name:
        return False
    return any(fnmatch.fnmatch(name, pattern) for pattern in _SECRET_LIKE_PATH_PATTERNS)


def _content_matches_stored_secret_file(
    content: str, db_path: Optional[str] = None
) -> bool:
    """Issue #153 code review MUST-FIX 1(b): return True when ``content``
    EXACTLY equals the value of a stored ``type="file"`` secret (i.e. this
    exact file was previously declared via a ``SECRET_FILE:`` declaration
    -- see ``secrets/parser.py``'s ``parse_file_secret``, which stores the
    file's FULL CONTENT as the secret value). This is a defense-in-depth
    check independent of substring masking: showing a whole declared
    secret file, even with known substrings masked, can still reveal
    structure the user explicitly marked as sensitive.

    ``db_path=None`` (the default) is a DELIBERATE no-op that NEVER
    touches any database -- test-isolation safety (this codebase's
    autouse `_guard_production_db` fixture guards ``hook.DEFAULT_DB_PATH``
    specifically; a caller that hasn't been given that value has no safe
    default to fall back to, so it must do nothing rather than silently
    resolve to a real path). The real hook.py call site always passes its
    own ``DEFAULT_DB_PATH`` explicitly.

    Fail-safe (Messi Anti-Fallback): any exception (missing/corrupt DB,
    permission error, etc.) logs a WARNING and returns False -- this check
    must never crash or block validation, only skip its own benefit.
    """
    if db_path is None:
        return False
    try:
        from .secrets.database import list_secrets

        for secret in list_secrets(db_path):
            if (
                secret.get("type") == "file"
                and secret.get("value")
                and secret["value"] == content
            ):
                return True
        return False
    except Exception as e:
        log_warning(
            "intent_validator",
            "Secret-file content check failed -- continuing without it "
            "(fail-safe, never blocks)",
            e,
        )
        return False


def _read_target_file_for_review(
    file_path: str,
    _deadline: Optional[float] = None,
    _db_path: Optional[str] = None,
) -> "tuple[Optional[str], str]":
    """Issue #153 follow-up (CHANGE 3): shared single-read helper used by
    BOTH the Edit-specific reader (``_read_edit_target_file``, which adds
    its own ``old_string``-specific checks on top) and Write's own
    ``_build_write_diff_section`` -- so a Write-over-an-existing-file diff
    gets the SAME fail-safe gating (deadline/secret-like-path/size/read
    error/secret-content-match) as an Edit's surrounding context, without
    a second copy of this logic (Messi Anti-Duplication).

    Returns ``(content, note)``:
      - ``content`` is ``None`` and ``note`` explains why when: the
        ``_deadline`` has already passed, the path is secret-like
        (``_is_secret_like_path``), the file doesn't exist, the file
        exceeds ``_SURROUNDING_CONTEXT_MAX_FILE_BYTES``, the file can't be
        read (``OSError``), or its content exactly matches a stored
        ``SECRET_FILE`` secret (``_content_matches_stored_secret_file``,
        only checked when ``_db_path`` is supplied).
      - ``content`` is the file's full on-disk text (never ``None``) and
        ``note == ""`` on success.

    Does NOT know about ``old_string``/occurrences -- that is layered on
    top by ``_read_edit_target_file`` for Edit reviews specifically.
    """
    import time as _time_module

    if _deadline is not None and _time_module.monotonic() >= _deadline:
        return None, "omitted (validation deadline reached — cheap read skipped)."
    if _is_secret_like_path(file_path):
        return None, "omitted (secret-like file path)."
    try:
        if not os.path.isfile(file_path):
            return None, "omitted (file not found on disk)."
        if os.path.getsize(file_path) > _SURROUNDING_CONTEXT_MAX_FILE_BYTES:
            return None, "omitted (file too large)."
        # Review low-priority fix: newline="" disables universal-newline
        # translation entirely -- the default open() mode silently
        # converts every "\r\n"/"\r" to "\n" on read, so a multi-line
        # old_string carrying an embedded "\r\n" (verbatim, exactly as
        # Claude Code's own Edit tool_input reads it from a CRLF file)
        # would otherwise NEVER match content.count(old_string).
        with open(file_path, "r", newline="", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return None, "omitted (file could not be read)."
    if _content_matches_stored_secret_file(content, db_path=_db_path):
        return None, "omitted (file content matches a stored secret file)."
    return content, ""


def _read_edit_target_file(
    file_path: str,
    old_string: str,
    replace_all: bool,
    _deadline: Optional[float] = None,
    _db_path: Optional[str] = None,
) -> "tuple[Optional[str], Optional[int], str]":
    """Issue #153 follow-up (CHANGE 3): Edit-specific single-read helper,
    layering ``old_string``-location/occurrence checks on top of
    ``_read_target_file_for_review``. Shared by BOTH
    ``_build_edit_surrounding_context_section`` and
    ``_build_edit_diff_section`` via the ``_precomputed`` param those
    functions accept -- the target file is read from disk EXACTLY ONCE
    per Edit review, never twice.

    Returns ``(content, idx, note)``:
      - Any fail-safe trigger (see ``_read_target_file_for_review``, plus
        ``old_string`` empty, not found, or ambiguous without
        ``replace_all``) -> ``(None, None, <note>)``, using the EXACT
        SAME note wording ``_build_edit_surrounding_context_section``
        produced before this refactor (byte-identical rendered output).
      - Success -> ``(content, idx, "")`` where ``idx`` is ``old_string``'s
        first-occurrence character offset within ``content``.

    Preserves the ORIGINAL check ORDER exactly: deadline -> empty
    old_string -> secret-like path -> file exists/size/read -> secret-
    content-match -> occurrences. The deadline and empty-old_string
    checks are Edit-specific (a Write-over-existing-file diff has no
    ``old_string`` at all) and run here BEFORE delegating to the shared
    reader for everything else.
    """
    import time as _time_module

    if _deadline is not None and _time_module.monotonic() >= _deadline:
        return (
            None,
            None,
            "omitted (validation deadline reached — cheap read skipped).",
        )
    if not old_string:
        return None, None, "omitted (no old_string to locate)."

    content, note = _read_target_file_for_review(
        file_path, _deadline=None, _db_path=_db_path
    )
    if content is None:
        return None, None, note

    occurrences = content.count(old_string)
    if occurrences == 0:
        return (
            None,
            None,
            "omitted (old_string not found on disk — possibly created by a "
            "prior sibling edit not yet applied).",
        )
    if occurrences > 1 and not replace_all:
        return (
            None,
            None,
            "omitted (old_string is ambiguous — found more than once in " "the file).",
        )

    idx = content.find(old_string)
    return content, idx, ""


def _build_edit_surrounding_context_section(
    file_path: str,
    old_string: str,
    replace_all: bool,
    context_lines: int = 15,
    max_chars: int = 4000,
    _deadline: Optional[float] = None,
    _db_path: Optional[str] = None,
    _precomputed: Optional["tuple[Optional[str], Optional[int], str]"] = None,
) -> str:
    """Issue #153: render up to ``context_lines`` lines of CURRENT on-disk
    file content immediately before and after ``old_string``, labelled as
    the file's content BEFORE this edit is applied -- so Stage 2 can tell
    "this Edit changes part of a longer function" from "this function is
    incomplete" (the ``mock_remove_with_transaction`` false-BLOCK case: a
    signature-only Edit was judged as if it were the WHOLE function, when
    the real body sits below the edited lines on disk).

    Issue #153 follow-up (CHANGE 3): the actual file-reading/fail-safe
    logic now lives in ``_read_edit_target_file`` (shared with
    ``_build_edit_diff_section``, so the target file is read from disk
    EXACTLY ONCE per Edit review). ``_precomputed`` accepts that
    function's own ``(content, idx, note)`` return value directly, to
    reuse a read the caller already performed; when omitted, this
    function calls ``_read_edit_target_file`` itself (standalone/test
    use). Rendered output is BYTE-IDENTICAL to before this refactor for
    every existing caller -- only the file-reading half moved, not the
    fail-safe wording or the rendering logic below.

    Untrusted file content is inserted via plain string concatenation only
    (never PromptLoader's ``variables=`` substitution, issue #150 lesson)
    -- callers substitute this return value into a template via plain
    ``str.format()``, which never re-scans substituted values for further
    placeholder syntax. A forged ``[pace-maker · ...]`` tag or literal
    ``{``/``}`` sequence appearing in the file's real content is rendered
    harmlessly as inert text.
    """
    if _precomputed is not None:
        content, idx, note = _precomputed
    else:
        content, idx, note = _read_edit_target_file(
            file_path, old_string, replace_all, _deadline=_deadline, _db_path=_db_path
        )
    if content is None:
        return _surrounding_context_note(note)

    before_lines = content[:idx].splitlines()
    # Review low-priority fix (off-by-one): when old_string ends EXACTLY
    # at a line boundary, the remainder starts with the "\n" that
    # terminates old_string's OWN last line. .splitlines() on a string
    # starting with "\n" always yields a leading EMPTY element for that
    # (meaningless) terminator -- consuming one context_lines slot for
    # nothing, so a requested 15-line after-window showed only 14 real
    # lines. Stripping exactly one leading "\n" before splitting removes
    # that phantom without affecting a GENUINE blank line immediately
    # after (which would still correctly appear as its own leading empty
    # element once the phantom is gone).
    after_remainder = content[idx + len(old_string) :]
    if after_remainder.startswith("\r\n"):
        after_remainder = after_remainder[2:]
    elif after_remainder.startswith("\n") or after_remainder.startswith("\r"):
        after_remainder = after_remainder[1:]
    after_lines = after_remainder.splitlines()
    kept_before = (
        before_lines[-context_lines:]
        if len(before_lines) > context_lines
        else before_lines
    )
    kept_after = after_lines[:context_lines]

    body = (
        "\n".join(kept_before)
        + "\n>>> EDITED REGION (old_string) START >>>\n"
        + old_string
        + "\n<<< EDITED REGION (old_string) END <<<\n"
        + "\n".join(kept_after)
    )
    if len(body) > max_chars:
        body = f"...[truncated to {max_chars} chars]...\n" + body[-max_chars:]

    header = (
        "CURRENT FILE CONTENT AROUND THE EDIT (on disk, BEFORE this edit "
        "is applied):"
    )
    return f"\n{header}\n{'=' * 60}\n{body}\n{'=' * 60}\n"


# Issue #153 follow-up (CHANGE 3): shared cap for both diff builders below.
_EDIT_DIFF_MAX_CHARS = 6000

_UNIFIED_DIFF_HEADER = (
    "UNIFIED DIFF (`-` = removed, `+` = added, unmarked = unchanged context):"
)


def _render_unified_diff(
    before_text: str, after_text: str, fromfile: str, tofile: str
) -> str:
    """Issue #153 follow-up (CHANGE 3): shared unified-diff renderer used
    by both ``_build_edit_diff_section`` and ``_build_write_diff_section``.
    Lines are diffed WITHOUT their own trailing newline
    (``str.splitlines()``, keepends=False) and ``lineterm=""`` tells
    ``difflib.unified_diff`` not to append one to its own hunk-header
    lines either -- every yielded line is then newline-free, so joining
    with a single ``"\\n"`` is always correct (no doubled blank lines).
    """
    import difflib

    diff_lines = list(
        difflib.unified_diff(
            before_text.splitlines(),
            after_text.splitlines(),
            fromfile=fromfile,
            tofile=tofile,
            n=3,
            lineterm="",
        )
    )
    return "\n".join(diff_lines)


def _cap_diff_text(rendered: str, max_chars: int) -> str:
    """Issue #153 follow-up (CHANGE 3): cap a rendered diff to its TAIL
    (the most recent/specific hunks), with an explicit truncation marker
    -- mirrors every other #153 cap (``_build_edit_diff_view`` originally,
    ``_build_edit_surrounding_context_section``, ``_build_sibling_edits_section``)."""
    if len(rendered) <= max_chars:
        return rendered
    return (
        f"...[truncated, showing last {max_chars} of {len(rendered)} chars]...\n"
        f"{rendered[-max_chars:]}"
    )


def _build_edit_diff_section(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool,
    max_chars: int = _EDIT_DIFF_MAX_CHARS,
    _deadline: Optional[float] = None,
    _db_path: Optional[str] = None,
    _precomputed: Optional["tuple[Optional[str], Optional[int], str]"] = None,
) -> str:
    """Issue #153 follow-up (CHANGE 3): render Edit's PROPOSED CODE as a
    REAL unified diff of the file's full content BEFORE/AFTER the edit,
    replacing the old OLD/NEW block-pair view (``_build_edit_diff_view``,
    now removed).

    Live evidence: deleting ``get_audit_logs`` (``old_string`` = "<last
    line of the previous method>\\n\\n<the whole get_audit_logs method>",
    ``new_string`` = "<the SAME last line alone>") was misread by haiku as
    "the method was replaced by unrelated code" under the block-pair view,
    because splitting old/new into two separate blocks hid that their
    FIRST line was identical. A real diff makes that shared line appear
    ONCE, unmarked (context), with only the method's real body rendered
    as ``-`` (removed) lines and NO corresponding ``+`` (added) line for
    it at all.

    Reuses the SAME single file read ``_build_edit_surrounding_context_section``
    already performs, via ``_precomputed`` (a caller-supplied
    ``(content, idx, note)`` tuple from ``_read_edit_target_file``) --
    NEVER reads the file a second time when the caller supplies it.
    Calls ``_read_edit_target_file`` itself when ``_precomputed`` is
    omitted (standalone/test use).

    Fallback (content unavailable -- unreadable/too-large/secret-like
    file, or ``old_string`` not-found/ambiguous-without-``replace_all``,
    the EXACT same conditions ``_build_edit_surrounding_context_section``
    already gates on): diffs ``old_string``'s own lines against
    ``new_string``'s own lines directly -- this still surfaces the
    get_audit_logs shared-line problem even when the file itself isn't
    available, since ``difflib`` still renders an identical leading line
    as unmarked context rather than a spurious ``+`` line.

    Capped at ``max_chars`` (the diff's TAIL, the most recent/specific
    hunks, kept) with an explicit truncation marker.

    Untrusted content is diffed via ``difflib.unified_diff`` and joined
    with plain string concatenation only (never PromptLoader
    ``variables=``, issue #150 lesson) -- a forged ``[pace-maker · ...]``
    tag or literal ``{``/``}`` sequence in a diff line is rendered
    harmlessly as inert text once substituted via the caller's
    ``str.format()``.
    """
    old_string = old_string or ""
    new_string = new_string or ""

    if _precomputed is not None:
        content, _idx, _note = _precomputed
    else:
        content, _idx, _note = _read_edit_target_file(
            file_path, old_string, replace_all, _deadline=_deadline, _db_path=_db_path
        )

    if content is not None:
        after = (
            content.replace(old_string, new_string)
            if replace_all
            else content.replace(old_string, new_string, 1)
        )
        rendered = _render_unified_diff(
            content, after, fromfile=file_path, tofile=file_path
        )
    else:
        rendered = _render_unified_diff(
            old_string, new_string, fromfile="old_string", tofile="new_string"
        )

    rendered = _cap_diff_text(rendered, max_chars)
    if not rendered.strip():
        rendered = "(no textual difference detected)"
    return f"{_UNIFIED_DIFF_HEADER}\n{rendered}"


def _build_write_diff_section(
    file_path: str,
    new_content: str,
    max_chars: int = _EDIT_DIFF_MAX_CHARS,
    _deadline: Optional[float] = None,
    _db_path: Optional[str] = None,
) -> "tuple[str, str]":
    """Issue #153 follow-up (CHANGE 3), fixed by issue #154 item 1: when
    Write targets a file that ALREADY EXISTS on disk, render a unified
    diff of the CURRENT on-disk content vs. the new ``content`` about to
    be written, instead of showing the new content in isolation -- mirrors
    the Edit diff's own reasoning (a reviewer verifies DIRECTION/SCOPE far
    more reliably from a diff than from a full blob with no baseline). A
    genuinely NEW file (nothing on disk yet) is unaffected by this
    function -- the caller only invokes it after confirming the file
    exists; ``run_pre_tool_hook`` keeps showing ``content`` in full for a
    brand-new file, exactly as before this change.

    Returns ``(rendered, mode)`` where ``mode`` is ``"diff"`` (the diff
    fits within ``max_chars``) or ``"full_content"`` (the file read was
    unavailable, OR the diff exceeds ``max_chars``) -- the caller
    (``hook.py``) uses ``mode`` to pick the matching NEW FILE WARNING
    wording variant (issue #154 item 1).

    Issue #154 item 1 (MEDIUM, reviewer-probe finding): before this fix,
    an over-cap diff was TRUNCATED to its TAIL (like ``_build_edit_diff_section``'s
    own cap) -- a planted swallowed-exception violation at the TOP of a
    22KB rewrite was then invisible to the reviewer, a real loss of
    review coverage versus the pre-#153 behavior (which always sent the
    full, uncapped ``content``). Now: when the rendered diff exceeds
    ``max_chars``, this function falls back to the FULL new content,
    UNCAPPED -- exactly the old behavior -- rather than truncating the
    diff. Diff mode applies ONLY when the diff actually fits.

    Reuses ``_read_target_file_for_review`` (the SAME fail-safe gating as
    an Edit's surrounding context: deadline/secret-like-path/size/read-
    error/secret-content-match). Fail-safe fallback: if the read is
    unavailable for any of those reasons, returns ``(new_content,
    "full_content")`` (never the short internal note text, and never a
    crash) -- this should rarely be reached in practice since the caller
    already confirmed the file exists, but a secret-like path or a huge
    file are still real possibilities.
    """
    new_content = new_content or ""
    content, _note = _read_target_file_for_review(
        file_path, _deadline=_deadline, _db_path=_db_path
    )
    if content is None:
        return new_content, "full_content"

    rendered = _render_unified_diff(
        content, new_content, fromfile=file_path, tofile=file_path
    )
    if len(rendered) > max_chars:
        # Issue #154 item 1: fall back to the full, UNCAPPED new content
        # rather than truncating the diff's TAIL -- a violation planted
        # at the TOP of a large rewrite must stay visible.
        return new_content, "full_content"
    if not rendered.strip():
        rendered = "(no textual difference detected)"
    return f"{_UNIFIED_DIFF_HEADER}\n{rendered}", "diff"


def _build_write_file_warning_body(write_case: Optional[str]) -> str:
    """Issue #154 item 1: pick the NEW FILE WARNING section's explanatory
    BODY text based on which Write case actually occurred. The header/
    separator lines ("NEW FILE WARNING (Write operations)" and the "="
    rules) stay STATIC and unconditional in both templates -- only this
    inner body varies via the `{write_file_warning_body}` placeholder.
    Keeping the header static preserves the pre-existing
    `_extract_section(..., ["NEW FILE WARNING"])` boundary every M3-lock
    test in tests/test_issue_151_reasoning_summary_intent.py and
    tests/test_issue_153_edit_context.py relies on.

    Before this fix, the section unconditionally said the file "does NOT
    exist on disk yet" and that PROPOSED CODE is its "COMPLETE, final
    content" -- false for a Write over an EXISTING file (shown as a diff,
    or as a full-content fallback per item 1's cap fix above).

    Args:
        write_case: ``None`` or ``"new_file"`` (the default) -> the file
            is genuinely new (nothing on disk yet) -- byte-identical to
            the pre-#154 static text for every existing caller that
            doesn't pass this param. ``"existing_diff"`` -> Write is
            replacing an EXISTING file and PROPOSED CODE is a unified
            diff against it. ``"existing_full_content"`` -> Write is
            replacing an EXISTING file but the diff exceeded the cap (see
            ``_build_write_diff_section``'s fallback), so PROPOSED CODE
            is the complete new content replacing it.
    """
    from .prompt_loader import PromptLoader

    loader = PromptLoader()
    if write_case == "existing_diff":
        name = "write_file_warning_existing_diff.md"
    elif write_case == "existing_full_content":
        name = "write_file_warning_existing_full_content.md"
    else:
        name = "write_file_warning_new_file.md"
    return loader.load_prompt(name, subfolder="common").strip()


def _build_sibling_edits_section(
    sibling_data: Optional[dict], max_chars: int = 4000
) -> str:
    """Issue #153: render the OTHER Write/Edit tool calls in the SAME
    anchored assistant turn as the one being reviewed, so Stage 2 can see
    a sibling edit that completes a fragment the CURRENT edit leaves
    seemingly unfinished (the OIDC split-multi-edit false-BLOCK case: the
    middle fragment of a 3-Edit turn was blocked for "not showing the
    enabled case", when the very next sibling Edit in the SAME message
    adds exactly that case -- the file on disk still has the OLD tail at
    validation time, so on-disk surrounding context alone cannot help
    here; only the sibling turn record can).

    ``sibling_data`` is ``{"position": k, "total": n, "siblings": [...]}``
    as built by ``transcript_reader._build_sibling_edits_data`` -- ``k``
    is the reviewed call's 1-based position among ALL Write/Edit calls in
    the turn, ``n`` is that total count, and ``siblings`` is every OTHER
    Write/Edit call in the turn (the reviewed call itself excluded), each
    carrying its OWN absolute 1-based ``"position"`` key. Falsy
    ``sibling_data``, or a turn with no OTHER Write/Edit calls
    (``total <= 1`` or an empty ``siblings`` list), omits the section
    entirely ("").

    Issue #153 code review MUST-FIX 3: each sibling is labelled with its
    OWN absolute position in the message (``#1``, ``#3``, ...), never
    renumbered 1..len(siblings) -- this is what makes "a LATER sibling
    completes this fragment" (the OIDC case: the reviewed edit is #2, and
    sibling #3 -- not "sibling #2 of 2" -- is the one that finishes it)
    visible to the reviewer at a glance.

    Untrusted ``old_string``/``new_string``/``content`` values are
    rendered via plain string concatenation, never PromptLoader
    ``variables=`` (issue #150 lesson) -- this return value is later
    substituted into the template via plain ``str.format()``, which never
    re-scans substituted values.
    """
    if not sibling_data:
        return ""
    total = sibling_data.get("total") or 0
    siblings = sibling_data.get("siblings") or []
    if total <= 1 or not siblings:
        return ""
    position = sibling_data.get("position", 1)

    blocks = []
    remaining = max_chars
    for sib in siblings:
        if remaining <= 0:
            break
        name = sib.get("name", "unknown")
        sib_position = sib.get("position", "?")
        inp = sib.get("input") or {}
        fp = inp.get("file_path", "(unknown file)")
        if name == "Write":
            body = (
                f"#{sib_position} — [{name}] {fp}\nContent:\n"
                f"{inp.get('content', '')}"
            )
        else:
            body = (
                f"#{sib_position} — [{name}] {fp}\n"
                f"Old:\n{inp.get('old_string', '')}\n"
                f"New:\n{inp.get('new_string', '')}"
            )
        if len(body) > remaining:
            body = body[:remaining] + "\n...[truncated]..."
        blocks.append(body)
        remaining -= len(body)

    header = (
        f"OTHER EDITS IN THE SAME MESSAGE (applied in order; the reviewed "
        f"edit is #{position} of {total}):"
    )
    body_text = "\n\n".join(blocks)
    return f"\n{header}\n{'=' * 60}\n{body_text}\n{'=' * 60}\n"


def _build_stage2_prompt_reasoning_summary(
    intent_text: str,
    code: str,
    file_path: str,
    is_core_path: bool,
    recent_context: Optional[List["tuple[str, str]"]] = None,
    surrounding_context_section: str = "",
    sibling_edits_section: str = "",
    intent_source: Optional[str] = None,
    write_case: Optional[str] = None,
) -> str:
    """Issue #151: build the Stage 2 prompt for the reasoning-summary
    intent exception path (configured models only, e.g. Opus 5.5 at high
    reasoning effort with no formal INTENT: declaration in its own visible
    text -- see resolve_reasoning_summary_intent_source).

    Uses a DEDICATED externalized template (Messi Rule 11) rather than
    reusing stage2_code_review.md, so the reviewer is told the intent came
    from an auto-summarized excerpt, not a formal declaration. Its CHECK
    1/CHECK 3/CLASSIFICATION VALUES sections are kept VERBATIM identical
    to stage2_code_review.md's (issue #151 code review M3, the #94 lesson
    that two independently maintained copies of the same judgement
    criteria drift) -- locked by
    tests/test_issue_151_reasoning_summary_intent.py's
    TestM3TemplateSectionsVerbatim.

    Uses plain str.format() (like _build_stage2_prompt above), NOT
    PromptLoader's ``variables=`` substitution — .format() only scans the
    TEMPLATE for ``{name}`` placeholders once, it never re-scans the
    substituted VALUES for further placeholder-shaped text. Untrusted
    ``intent_text``/``code`` (the model's own reasoning/response, or real
    source code, either of which may legitimately contain literal
    ``{``/``}`` characters) can therefore never trigger the false
    "unreplaced placeholder" crash that PromptLoader's home-grown
    find/replace + rescan is vulnerable to (see build_no_visible_text_notice's
    docstring, issue #150 code-review follow-up item 1, for that hazard).
    This is the exact same safe pattern _build_stage2_prompt already uses
    for ``code``/``file_path`` above.

    ``intent_text`` is capped at 3000 chars (issue #151 code review L6),
    matching the danger-bash gate's own ``current_message[:3000]`` cap.

    ``recent_context`` (issue #151 live-test follow-up, Write/Edit only --
    NEVER threaded into the danger-bash gate or the normal/strict path):
    optional list of ``(visible_text, thinking_text)`` prior-turn tuples,
    oldest to newest, rendered via ``_build_recent_context_section()``
    into a clearly labelled "RECENT CONTEXT" section placed right after
    the INTENT block. ``None``/empty (the default) omits the section
    entirely -- byte-identical to before this follow-up for every caller
    that doesn't supply it.

    ``surrounding_context_section``/``sibling_edits_section`` (issue
    #153, Edit reviews only -- see ``_build_stage2_prompt``'s docstring
    for the identical params on the normal/strict path): pre-rendered
    section strings built by ``_build_edit_surrounding_context_section``/
    ``_build_sibling_edits_section``. "" (the default) omits either
    section entirely -- byte-identical to before this issue for every
    caller that doesn't supply them (Write calls, and every pre-existing
    test/caller).
    """
    module_dir = os.path.dirname(__file__)
    prompt_path = os.path.join(
        module_dir,
        "prompts",
        "pre_tool_use",
        "stage2_code_review_reasoning_summary.md",
    )
    if not os.path.exists(prompt_path):
        raise FileNotFoundError(
            f"Stage 2 reasoning-summary prompt template not found at "
            f"{prompt_path}. This indicates a broken installation. Run "
            "./install.sh to fix."
        )
    template = load_prompt_template(prompt_path)

    from .constants import DEFAULT_CLEAN_CODE_RULES_PATH
    from . import clean_code_rules

    rules = clean_code_rules.load_rules(DEFAULT_CLEAN_CODE_RULES_PATH)
    clean_code_rules_text = clean_code_rules.format_rules_for_validation(rules)

    core_path_note = ""
    if is_core_path:
        note_path = os.path.join(
            module_dir,
            "prompts",
            "pre_tool_use",
            "reasoning_summary_core_path_note.md",
        )
        core_path_note = load_prompt_template(note_path)

    recent_context_section = _build_recent_context_section(recent_context)

    # Issue #151 live-replay follow-up (round 3, CHANGE 2): when the
    # intent came from the PREVIOUS message's own reasoning summary/
    # visible text (the anchor itself had neither), tell the reviewer so
    # explicitly -- otherwise it silently judges "this turn's own intent"
    # against text that was never written in this turn at all. "" (the
    # default, every other intent_source) omits it entirely -- byte-
    # identical to before this change for every pre-existing caller.
    prior_turn_note = ""
    if intent_source == "prior_reasoning_summary":
        note_path = os.path.join(
            module_dir,
            "prompts",
            "pre_tool_use",
            "reasoning_summary_prior_turn_note.md",
        )
        prior_turn_note = load_prompt_template(note_path).strip() + "\n\n"

    return template.format(
        intent_text=intent_text[:3000],
        code=code,
        file_path=file_path,
        clean_code_rules=clean_code_rules_text,
        core_path_note=core_path_note,
        recent_context_section=recent_context_section,
        surrounding_context_section=surrounding_context_section,
        sibling_edits_section=sibling_edits_section,
        prior_turn_note=prior_turn_note,
        write_file_warning_body=_build_write_file_warning_body(write_case),
    )


def _reasoning_summary_intent_text(
    anchor_visible_text: str, anchor_reasoning_summary: str
) -> "tuple[str, Optional[str]]":
    """Issue #151: combine the anchored turn's visible text and reasoning
    summary into a single intent_text, plus the intent_source label used
    for telemetry.

    Returns (intent_text, intent_source):
      - reasoning summary non-empty -> ("reasoning_summary") — the summary
        is included regardless of whether visible text is also present,
        since its presence means the model's OWN plain-text response did
        not (necessarily) carry the full intent.
      - reasoning summary empty, visible text non-empty ->
        ("visible_text").
      - both empty -> ("", None) — nothing to validate against.
    """
    visible = (anchor_visible_text or "").strip()
    summary = (anchor_reasoning_summary or "").strip()
    if summary:
        intent_text = f"{visible}\n\n{summary}" if visible else summary
        return intent_text, "reasoning_summary"
    if visible:
        return visible, "visible_text"
    return "", None


def resolve_reasoning_summary_intent_source(
    reasoning_summary_intent: bool,
    anchor_visible_text: str,
    anchor_reasoning_summary: str,
    recent_context: Optional[List["tuple[str, str]"]] = None,
    allow_prior_turn_fallback: bool = True,
) -> "tuple[Optional[str], Optional[str]]":
    """Issue #151 code review H2/M1: the SHARED decision function used by
    BOTH gates (Write/Edit and danger-bash) for whether an exception-model
    turn takes the STRICT (declaration-required) path or the RELAXED
    (reasoning-summary/visible-text/prior-reasoning-summary) path.

    Args:
        reasoning_summary_intent: True when the caller (hook.py) determined
            the anchored turn's assistant ``message.model`` is in the
            configured (and normalized -- see
            ``hook._normalize_reasoning_summary_intent_models``)
            ``reasoning_summary_intent_models`` list.
        anchor_visible_text: The anchored turn's own visible text
            (transcript_reader's ``anchor_prose_text``), REGARDLESS of
            whether it carries an INTENT: marker.
        anchor_reasoning_summary: The anchored turn's concatenated
            non-empty ``thinking`` block texts.
        recent_context: Issue #151 live-replay follow-up (round 3, CHANGE
            2). The SAME anchor-relative ``(visible_text, thinking_text)``
            prior-turn tuples (oldest to newest) the caller already
            computes for the RECENT CONTEXT prompt section -- no extra
            parse. Consulted ONLY when the anchor's own combined text is
            empty (see the "prior_reasoning_summary" case below); ignored
            entirely otherwise, and ignored for a non-exception model.
        allow_prior_turn_fallback: Issue #154 item 2. Explicit, caller-
            supplied gate on the CHANGE 2 fallback below -- NEVER an
            implicit tool-name check buried inside this function. The
            user's approval for CHANGE 2 was based on Edit evidence only;
            letting it also apply to danger-bash would relax #93's
            deliberate "Bash is anchor-only" tightening and #139's stale-
            path anchor-only rule. The Write/Edit gate passes ``True``
            (the default); the danger-bash gate passes ``False``
            explicitly, keeping its pre-CHANGE-2 behavior for an empty
            anchor (block with the no-visible-text notice) unchanged.

    Returns (intent_source, relaxed_intent_text):
      - ``reasoning_summary_intent`` is False (non-exception model) ->
        ``(None, None)``. The caller must never add "intent_source" to
        telemetry and must always use the strict path unchanged.
      - The anchor's own VISIBLE text ALREADY carries a real INTENT:
        marker (H2) -> ``("declaration", None)``. The model already
        complied with the normal contract -- the caller uses the STRICT
        path UNCHANGED (byte-identical to a non-exception model's strict
        path), tagging the result "declaration" for telemetry. The
        marker check is scoped to VISIBLE text only, never the reasoning
        summary -- a summary that happens to contain the substring
        "INTENT:" must never short-circuit the relaxed exception, since
        the summary is not a formal declaration channel.
      - No visible INTENT: marker, but the combined (visible text +
        reasoning summary) is non-empty -> ``("reasoning_summary" |
        "visible_text", <the combined text>)``. The caller takes the
        RELAXED path.
      - No visible INTENT: marker AND the anchor's own combined text is
        empty, BUT ``recent_context`` is non-empty and its LAST element
        (the turn IMMEDIATELY preceding the anchor) has its own non-empty
        combined text -> ``("prior_reasoning_summary", <that prior turn's
        combined text>)``. Live evidence: 4 live blocks in one day, and 84
        of 1316 historical Opus edits, had an anchored turn with NEITHER
        visible text NOR a reasoning summary, while the immediately
        preceding turn's OWN reasoning summary described the edit (e.g.
        "I'll add a new test in test_elevate_session.py verifying that
        calls missing a session key return missing_session_key..."). Only
        the SINGLE immediately-preceding turn is ever consulted -- an
        earlier prior turn is never used, even if the immediately
        preceding one is itself empty (that falls through to "none"
        below, same as if recent_context had not been supplied at all).
      - No visible INTENT: marker AND the combined text is empty AND
        (``recent_context`` is empty/absent OR its last turn is ALSO
        empty) (M1) -> ``("none", None)``. The caller falls through to
        the STRICT path (exactly as a non-exception model would for an
        empty/absent turn -- including the prose-only n-back rescue,
        #140) instead of hard-blocking with exception-specific wording.
        Re-review follow-up: this is deliberately NOT "declaration" --
        there is no declaration here (the anchor has neither visible
        text nor a summary), and tagging it "declaration" would (a)
        contradict a ``no_visible_text=True`` blockage row saying the
        turn complied, and (b) silently pad any count of "compliant Opus
        turns" with turns that had nothing at all. The key is still
        POPULATED (never omitted) so these turns stay individually
        countable under their own distinct value. If the n-back rescue
        subsequently succeeds (M1's own repro), the result is still
        tagged "none" -- this function cannot know in advance whether the
        rescue will succeed, and retroactively upgrading the tag after
        the fact is out of scope; an accepted, documented tradeoff.
    """
    if not reasoning_summary_intent:
        return None, None
    if INTENT_MARKER_PATTERN.search(anchor_visible_text or ""):
        return "declaration", None
    intent_text, intent_source = _reasoning_summary_intent_text(
        anchor_visible_text, anchor_reasoning_summary
    )
    if intent_text:
        return intent_source, intent_text
    if allow_prior_turn_fallback and recent_context:
        prev_visible, prev_thinking = recent_context[-1]
        prior_intent_text, _prior_source = _reasoning_summary_intent_text(
            prev_visible, prev_thinking
        )
        if prior_intent_text:
            return "prior_reasoning_summary", prior_intent_text
    return "none", None


def _validate_reasoning_summary_path(
    intent_text: str,
    intent_source: str,
    code: str,
    file_path: str,
    hook_model: str,
    _deadline: Optional[float],
    exclusions: list,
    core_path_segments: List[str],
    extensions: List[str],
    recent_context: Optional[List["tuple[str, str]"]] = None,
    edit_surrounding_context_section: str = "",
    edit_sibling_edits_section: str = "",
    _db_path: Optional[str] = None,
    write_case: Optional[str] = None,
    declare_intent_hint: bool = False,
) -> dict:
    """Issue #151: the RELAXED reasoning-summary/visible-text intent path.

    Only ever called when ``resolve_reasoning_summary_intent_source`` has
    already proven ``intent_text`` is non-empty and ``intent_source`` is
    ``"reasoning_summary"`` or ``"visible_text"`` (never ``"declaration"``
    -- that case takes the STRICT path via ``_validate_normal_path``
    instead, see ``validate_intent_and_code``). Stage 1 has NO
    INTENT:/TDD/version-bump regex at all here — it is guaranteed to pass
    by construction (the caller never invokes this function otherwise).
    Stage 2 uses a dedicated externalized prompt (see
    ``_build_stage2_prompt_reasoning_summary``) that frames the intent as
    an auto-summarized excerpt rather than a formal declaration.

    ``recent_context`` (issue #151 live-test follow-up, Write/Edit only):
    optional prior-turn ``(visible_text, thinking_text)`` tuples, passed
    straight through to ``_build_stage2_prompt_reasoning_summary`` -- see
    its docstring. ``None`` (the default) omits the RECENT CONTEXT
    section entirely.

    Every returned dict carries "intent_source" for telemetry.
    """
    log_debug(
        "intent_validator",
        f"Reasoning-summary relaxed path: intent_source={intent_source} "
        f"intent_text_len={len(intent_text)} file={file_path}",
    )

    if not SDK_AVAILABLE and hook_model in ("auto", "sonnet", "opus", "haiku"):
        _raw = _sdk_unavailable_message()
        return {
            "approved": False,
            "feedback": format_tag(_raw, "intent_validation_block"),
            "raw_feedback": _raw,
            "intent_source": intent_source,
        }

    _is_core = _is_core_path(file_path, core_path_segments, exclusions, extensions)
    stage2_prompt = _build_stage2_prompt_reasoning_summary(
        intent_text,
        code,
        file_path,
        _is_core,
        recent_context=recent_context,
        surrounding_context_section=edit_surrounding_context_section,
        sibling_edits_section=edit_sibling_edits_section,
        intent_source=intent_source,
        write_case=write_case,
    )

    _stage2_degradation: Dict[str, Any] = {}
    stage2_feedback, reviewer = _call_stage2_validation(
        stage2_prompt,
        hook_model=hook_model,
        _degradation=_stage2_degradation,
        _deadline=_deadline,
        _db_path=_db_path,
    )

    if verdict_passes(stage2_feedback):
        return {
            "approved": True,
            "reviewer": reviewer,
            "degradation": _stage2_degradation,
            "intent_source": intent_source,
        }
    elif not stage2_feedback:
        _raw = build_reviewer_unavailable_message(_stage2_degradation)
        return {
            "approved": False,
            "reviewer_unavailable_failure": True,
            "feedback": format_tag(_raw, "fail_closed_error"),
            "raw_feedback": _raw,
            "reviewer": reviewer,
            "degradation": _stage2_degradation,
            "intent_source": intent_source,
        }
    else:
        _classification = _parse_stage2_classification(stage2_feedback)
        return {
            "approved": False,
            # Bug #159: this path never carries a tool-declared intent
            # (those take the strict path), so the hint always applies
            # when the tool path is enabled -- appended after the relay
            # segment, reviewer text untouched.
            "feedback": _append_declare_intent_hint(
                format_reviewer_relay(stage2_feedback, reviewer),
                file_path,
                declare_intent_hint,
            ),
            "raw_feedback": stage2_feedback,
            "clean_code_failure": _classification == "clean_code",
            "bug_failure": _classification == "bug",
            # Issue #151 code review L5: a core-path missing-test-coverage
            # rejection is classified as TDD (see
            # reasoning_summary_core_path_note.md's CLASSIFICATION: TDD
            # instruction), consistent with how Stage 1's NO_TDD branch
            # maps to the "intent_validation_tdd" category today.
            "tdd_failure": _classification == "tdd",
            "reviewer": reviewer,
            "intent_source": intent_source,
        }


# Re-review MUST-FIX 1 (HIGH): reviewer-prompt masking must never scale
# with total store size. The real store holds 763 secrets / 5.2M chars
# (29 of them over 50KB, since SECRET_FILE stores whole file contents) --
# building ONE combined regex from every stored secret (the pattern
# `_build_secrets_pattern` compiles) measured ~6.5s in a real hook
# process, on EVERY Write/Edit Stage 2 call and EVERY danger-bash Phase 2
# call. The fix: pre-filter to secrets that actually occur in the prompt
# BEFORE building any pattern at all -- `_build_secrets_pattern`/
# `mask_text` then only ever see the tiny relevant subset. Measured
# after the fix: ~0.0063s, 2 of 763 secrets kept, identical masks.
#
# Re-review MEDIUM: the real store also holds 31 values under 8 chars
# (one 3 chars, one file-type 2 chars) -- two 5-char values matched 8
# times in ordinary `routes.py` code in the live replay, corrupting the
# prompt and creating a NEW false-block risk. Values shorter than this
# are skipped for reviewer-prompt masking specifically (the Langfuse
# sanitizer, `secrets.sanitizer.sanitize_trace()`, is DELIBERATELY left
# alone -- out of scope, a known gap, see CLAUDE.md).
_MIN_REVIEWER_MASK_SECRET_LENGTH = 8


def _mask_reviewer_prompt(prompt: str, db_path: Optional[str] = None) -> str:
    """Issue #153 code review MUST-FIX 1(a): mask ALL stored secrets in a
    prompt at this SINGLE choke point, immediately before it is sent to a
    (possibly hosted) reviewer provider. #153 added ~30 lines of
    UNREVIEWED on-disk file content (surrounding context) and sibling-edit
    tool inputs to the Stage 2 prompt -- neither had ever been checked for
    secrets before reaching a third-party LLM, because Stage 2 prompts
    were NEVER masked at all before this fix (only Langfuse traces were,
    via ``secrets.sanitizer.sanitize_trace()``). A hard-coded key in
    ``settings.py``/``config.js`` shown via the surrounding-context
    section (or, previously, ANY Stage 2 prompt content at all, since this
    fix also covers the pre-existing code/messages text) could otherwise
    leak to a hosted reviewer.

    Uses the same machinery ``secrets.sanitizer.sanitize_trace()`` already
    uses for Langfuse: ``secrets.database.get_all_secrets()`` +
    ``secrets.masking.mask_text()`` -- but see the two re-review fixes
    documented on ``_MIN_REVIEWER_MASK_SECRET_LENGTH`` immediately above:
    (1) the secret list is pre-filtered to values that both meet the
    minimum length AND actually occur in ``prompt`` BEFORE any regex
    pattern is built (performance -- avoids compiling a pattern from
    potentially hundreds of thousands of characters of stored secrets
    when at most a handful are ever relevant to THIS prompt), and (2)
    values shorter than ``_MIN_REVIEWER_MASK_SECRET_LENGTH`` are skipped
    entirely (correctness -- too short to mask safely; a single WARNING
    with the COUNT, never the values, is logged when any are skipped).

    ``db_path=None`` (the default) is a DELIBERATE no-op that never
    touches any database -- see ``_content_matches_stored_secret_file``'s
    docstring for the identical test-isolation rationale. The real
    callers (below, and the danger-bash Phase 2 gate in hook.py) always
    pass hook.py's own ``DEFAULT_DB_PATH`` explicitly.

    Fail-safe (Messi Anti-Fallback): if the secrets store can't be read
    for ANY reason, logs a WARNING and returns the prompt UNMASKED rather
    than raising or blocking -- masking must never crash or block
    validation. The pre-filter below is also what makes this function
    BOUNDED BY CONSTRUCTION regardless of store size -- the danger-bash
    Phase 2 call site (which has no separate deadline check of its own)
    relies on exactly this bound rather than threading `_deadline`
    through a second code path.
    """
    if db_path is None:
        return prompt
    try:
        from .secrets import masking
        from .secrets.database import get_all_secrets

        secrets = get_all_secrets(db_path)
        if not secrets:
            return prompt

        # Only the min-length rule lives here (reviewer prompts only): values
        # too short to mask safely are skipped and counted. The occurs-in-the-
        # payload prefilter + pattern compile is the SHARED helper (bug #157,
        # also used by Langfuse's sanitize_trace) -- never a second copy.
        eligible: List[str] = []
        skipped_short = 0
        for secret in secrets:
            if not secret:
                continue
            if len(secret) < _MIN_REVIEWER_MASK_SECRET_LENGTH:
                skipped_short += 1
                continue
            eligible.append(secret)
        relevant, pattern = masking.build_prefiltered_pattern(eligible, [prompt])

        if skipped_short:
            log_warning(
                "intent_validator",
                f"Skipped {skipped_short} stored secret(s) shorter than "
                f"{_MIN_REVIEWER_MASK_SECRET_LENGTH} chars during "
                "reviewer-prompt masking (too short to mask safely; "
                "values never logged)",
            )

        if not relevant:
            return prompt
        masked, _count = masking.mask_text(prompt, relevant, pattern)
        return masked
    except Exception as e:
        log_warning(
            "intent_validator",
            "Secrets masking failed for a reviewer prompt -- continuing "
            "UNMASKED (fail-safe, never blocks)",
            e,
        )
        return prompt


def _call_stage2_validation(
    prompt: str,
    hook_model: str = "auto",
    _degradation: Optional[dict] = None,
    _deadline: Optional[float] = None,
    _db_path: Optional[str] = None,
) -> "tuple[str, str]":
    """
    Synchronous Stage 2 validation via provider abstraction.

    Args:
        prompt: Stage 2 validation prompt
        hook_model: Model selection - "auto", "sonnet", "opus", "gpt-5.4", "gpt-5.5" (legacy alias: "gpt-5")
        _degradation: optional out-param dict (issue #131), passed straight
            through to resolve_and_call_with_reviewer() — see its docstring.
        _db_path: Issue #153 code review MUST-FIX 1(a). Passed straight
            through to ``_mask_reviewer_prompt`` -- ``None`` (the default)
            skips masking entirely (test-isolation safety); the real
            Write/Edit gate always supplies hook.py's own
            ``DEFAULT_DB_PATH``.

    Returns:
        Tuple of (response_text, reviewer_name) where reviewer_name identifies
        which provider served the request (e.g. "codex-gpt5", "anthropic-sdk").
    """
    from .inference import resolve_and_call_with_reviewer

    masked_prompt = _mask_reviewer_prompt(prompt, db_path=_db_path)

    return resolve_and_call_with_reviewer(
        hook_model=hook_model,
        prompt=masked_prompt,
        system_prompt="You are a strict code validator. Return empty response ONLY if all checks pass. Otherwise return detailed feedback.",
        call_context="stage2_unified",
        max_thinking_tokens=4000,
        _degradation=_degradation,
        _deadline=_deadline,
    )


def _build_stage2_prompt(
    messages: List[str],
    code: str,
    file_path: str,
    tool_name: str,
    surrounding_context_section: str = "",
    sibling_edits_section: str = "",
    write_case: Optional[str] = None,
) -> str:
    """
    Build Stage 2 validation prompt from external template.

    Args:
        messages: Last 4 messages for context
        code: Proposed code
        file_path: Target file path
        tool_name: Write or Edit
        surrounding_context_section: Issue #153. Pre-rendered section (via
            ``_build_edit_surrounding_context_section``) showing the
            CURRENT on-disk file content around an Edit's ``old_string``,
            or "" (the default) to omit it entirely -- byte-identical to
            before this issue for every caller that doesn't supply it
            (Write calls, and every pre-existing test/caller).
        sibling_edits_section: Issue #153. Pre-rendered section (via
            ``_build_sibling_edits_section``) showing the OTHER Write/Edit
            calls in the same anchored turn, or "" (the default) to omit
            it entirely -- same byte-identical-by-default guarantee.

    Returns:
        Stage 2 prompt string with variables replaced

    Uses plain ``str.format()`` (not PromptLoader's ``variables=``) --
    see ``_build_stage2_prompt_reasoning_summary``'s docstring for the
    full #150 rationale. Both new sections are ALREADY fully-rendered
    strings by the time they reach this function (built in code, never
    via a template-substitution mechanism that re-scans values), so
    literal ``{``/``}``/forged ``[pace-maker · ...]`` text inside an
    Edit's ``old_string``/``new_string`` or a sibling's tool input can
    never crash this ``.format()`` call or forge a real pace-maker tag.
    """
    module_dir = os.path.dirname(__file__)
    prompt_path = os.path.join(
        module_dir, "prompts", "pre_tool_use", "stage2_code_review.md"
    )

    if not os.path.exists(prompt_path):
        raise FileNotFoundError(
            f"Stage 2 prompt template not found at {prompt_path}. "
            "This indicates a broken installation. Run ./install.sh to fix."
        )

    template = load_prompt_template(prompt_path)

    # Format messages for template
    messages_text = "\n".join(f"Message {i+1}: {msg}" for i, msg in enumerate(messages))

    # Load clean code rules
    from .constants import DEFAULT_CLEAN_CODE_RULES_PATH
    from . import clean_code_rules

    rules = clean_code_rules.load_rules(DEFAULT_CLEAN_CODE_RULES_PATH)
    clean_code_rules_text = clean_code_rules.format_rules_for_validation(rules)

    return template.format(
        messages=messages_text,
        code=code,
        file_path=file_path,
        clean_code_rules=clean_code_rules_text,
        surrounding_context_section=surrounding_context_section,
        sibling_edits_section=sibling_edits_section,
        write_file_warning_body=_build_write_file_warning_body(write_case),
    )


def generate_validation_prompt(
    messages: List[str],
    code: str,
    file_path: str,
    tool_name: str,
    config: Dict[str, Any] = None,
) -> str:
    """
    Generate validation prompt for pre-tool validation.

    Extracted for testability - generates prompt without calling SDK.

    Args:
        messages: Last 4 assistant messages (current + 3 before)
        code: Proposed code that will be written
        file_path: Target file path
        tool_name: Write or Edit
        config: Optional config dict (for testing). If None, loads from file.

    Returns:
        Formatted validation prompt string
    """
    # Format messages for template
    messages_text = "\n".join(f"Message {i+1}: {msg}" for i, msg in enumerate(messages))

    # Load clean code rules
    from .constants import DEFAULT_CLEAN_CODE_RULES_PATH
    from . import clean_code_rules

    rules = clean_code_rules.load_rules(DEFAULT_CLEAN_CODE_RULES_PATH)
    clean_code_rules_text = clean_code_rules.format_rules_for_validation(rules)

    # Load config to check TDD state
    if config is None:
        from .hook import load_config

        config = load_config()
    intent_validation_enabled = config.get("intent_validation_enabled", False)
    tdd_enabled = config.get("tdd_enabled", True)

    # Determine TDD section content
    # TDD active only when BOTH intent_validation AND tdd_enabled are true
    tdd_section = ""
    if intent_validation_enabled and tdd_enabled:
        # Load TDD section from external file
        module_dir = os.path.dirname(__file__)
        tdd_section_path = os.path.join(
            module_dir, "prompts", "pre_tool_use", "tdd_section.md"
        )
        if os.path.exists(tdd_section_path):
            with open(tdd_section_path, "r", encoding="utf-8") as f:
                tdd_section_template = f.read()

            # Load core paths and format for prompt
            from .constants import DEFAULT_CORE_PATHS_PATH
            from . import core_paths

            paths = core_paths.load_paths(DEFAULT_CORE_PATHS_PATH)
            core_paths_text = core_paths.format_paths_for_prompt(paths)

            # Replace placeholder
            tdd_section = tdd_section_template.replace(
                "{{core_paths}}", core_paths_text
            )
        else:
            # File missing - log warning and continue without TDD section
            log_warning(
                "intent_validator",
                f"TDD section file not found: {tdd_section_path}. TDD enforcement disabled.",
                None,
            )

    # Load template and fill with parameters
    template = get_pre_tool_prompt_template()
    prompt = template.format(
        tool_name=tool_name,
        file_path=file_path,
        messages=messages_text,
        code=code,
        clean_code_rules=clean_code_rules_text,
        tdd_section=tdd_section,
    )

    return prompt


def _parse_stage2_classification(feedback: str) -> str:
    """Parse the structured CLASSIFICATION line from a stage 2 rejection response.

    Returns the classification string: "clean_code", "bug", "intent_mismatch",
    or "tdd".

    Stage 2 IS the code review stage, so the default for any stage 2 rejection
    is "clean_code". Only explicit CLASSIFICATION values override this.

    Expected format in response (anywhere in text):
        CLASSIFICATION: CLEAN_CODE       → "clean_code"
        CLASSIFICATION: BUG              → "bug"
        CLASSIFICATION: INTENT_MISMATCH  → "intent_mismatch"
        CLASSIFICATION: TDD              → "tdd"

    "TDD" (issue #151 code review L5) is emitted ONLY by the
    reasoning-summary exception's core-path note
    (reasoning_summary_core_path_note.md) for a missing-test-coverage
    rejection on a core path -- the NORMAL stage2_code_review.md template
    never instructs a reviewer to emit it, so this addition is purely
    additive for the intended (relaxed-path) caller. Re-review follow-up
    (softened claim): this parser has no way to know WHICH template
    produced the feedback it is parsing, so it does not special-case the
    normal path -- if a normal-path reviewer ever emitted a literal
    "CLASSIFICATION: TDD" line (nothing instructs it to, but this function
    cannot prevent it), this function WOULD still return "tdd" for it.
    That is harmless, not "misclassified": `_validate_normal_path`'s
    Stage-2-rejected branch only ever reads `_classification ==
    "clean_code"`/`"bug"` (it never reads `"tdd"` at all -- only
    `_validate_reasoning_summary_path` does), so such a rejection would
    simply carry `clean_code_failure=False`/`bug_failure=False` and
    `hook.py`'s category chain would fall through to the generic
    `"intent_validation"` category rather than `"intent_validation_tdd"`
    -- a less-specific label, not an incorrect block.

    If the CLASSIFICATION line is absent or contains an unrecognised value,
    defaults to "clean_code" (safe default: all stage 2 rejections are code-review issues).
    """
    for line in feedback.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("CLASSIFICATION:"):
            value = upper[len("CLASSIFICATION:") :].strip()
            if value == "INTENT_MISMATCH":
                return "intent_mismatch"
            if value == "BUG":
                return "bug"
            if value == "TDD":
                return "tdd"
            # CLEAN_CODE or any unknown value → safe default
            return "clean_code"
    # No CLASSIFICATION line found → safe default
    return "clean_code"


def _validate_normal_path(
    messages: List[str],
    code: str,
    file_path: str,
    tool_name: str,
    hook_model: str,
    current_message_override: str,
    _deadline: Optional[float],
    no_visible_text: bool,
    stage1_fallback_messages: Optional[List[str]],
    exclusions: list,
    core_path_segments: List[str],
    extensions: List[str],
    edit_surrounding_context_section: str = "",
    edit_sibling_edits_section: str = "",
    _db_path: Optional[str] = None,
    write_case: Optional[str] = None,
    declare_intent_hint: bool = False,
    tool_declared_tdd: Optional[bool] = None,
    intent_from_tool: bool = False,
) -> dict:
    """The STRICT (declaration-required) Stage 1/2 pipeline -- extracted
    verbatim from validate_intent_and_code (issue #151 code review H2/M1)
    so it can be reused both by non-exception-model calls AND by
    exception-model calls whose anchor turn already carries a real
    INTENT: declaration, or whose combined relaxed intent text is empty
    (see resolve_reasoning_summary_intent_source). No behavior change from
    the pre-#151 code -- same lines, same order, just parameterized
    instead of closing over validate_intent_and_code's locals. Raises on
    unexpected errors; the caller's try/except handles fail-closed.
    """
    # STAGE 1: Fast declaration check (CURRENT message only).
    # Prefer the requestId-anchored current-turn message (Fix 3) when the
    # caller supplied it; fall back to the n-back heuristic otherwise.
    # Issue #140 code-review findings 1-3: the n-back fallback searches
    # stage1_fallback_messages (PROSE ONLY) when supplied, never the
    # possibly-tool-rendered `messages` that Stage 2 needs below.
    _stage1_source_messages = (
        stage1_fallback_messages if stage1_fallback_messages is not None else messages
    )
    current_message = current_message_override or extract_current_assistant_message(
        _stage1_source_messages, file_path=file_path
    )
    log_debug("intent_validator", "=== STAGE 1 VALIDATION START ===")
    log_debug("intent_validator", f"File path: {file_path}")
    log_debug("intent_validator", f"Tool name: {tool_name}")
    log_debug(
        "intent_validator", f"Current message length: {len(current_message)} chars"
    )
    log_debug(
        "intent_validator", f"Current message preview: {current_message[:200]}..."
    )

    # STAGE 1: Fast regex structural check (no LLM call)
    stage1_response_upper = _regex_stage1_check(
        current_message,
        file_path,
        exclusions,
        core_path_segments,
        extensions,
        tool_declared_tdd=tool_declared_tdd,
    )
    log_debug("intent_validator", f"Stage 1 regex response: '{stage1_response_upper}'")

    # Fix 4: always-on (WARNING-level) diagnostic on a Stage-1 REJECTION so
    # false-rejects are diagnosable from the standard log even when DEBUG
    # logging is off. Short prefix only (no full code dumps / secrets).
    # Routed through _log_stage1_rejection with the EXACT `current_message`
    # used to compute the verdict above — never a re-derived/stale value.
    if stage1_response_upper in ("NO", "NO_TDD"):
        _log_stage1_rejection(stage1_response_upper, file_path, current_message)

    if stage1_response_upper == "NO":
        # Intent declaration missing
        _raw = """⛔ Intent declaration required

You must declare your intent BEFORE using Write/Edit tools.

⚠️  CRITICAL: Start with "INTENT:" marker!

Required format - include ALL 3 components IN YOUR CURRENT MESSAGE:
  1. FILE: Which file you're modifying
  2. CHANGES: What specific changes you're making
  3. GOAL: Why you're making these changes

Example (all in same message as Write/Edit):
  "INTENT: Modify src/auth.py to add a validate_input() function
   that checks user input for XSS attacks, to improve security."

Then use your Write/Edit tool in the same message."""
        if declare_intent_hint:
            # Story #155 AC11: prefer the tool, keep the INTENT: fallback
            # (the generic template below). Added BEFORE the no-visible-
            # text notice is prepended, so that notice still LEADS (#150).
            _raw = build_declare_intent_hint(file_path) + "\n\n" + _raw
        if no_visible_text:
            # Issue #150: lead the block reason with the
            # pilot-validated notice (a ready-to-copy INTENT: example
            # naming the ACTUAL file/tool) instead of appending the
            # old generic THINKING_ONLY_NOTICE at the end -- the pilot
            # showed the leading position is what actually changes
            # Opus 5.5 xhigh-effort behavior; the generic template
            # below is kept as supporting detail, not the lead. Code
            # review follow-up (item 3): includes a Test coverage:
            # line when file_path is a core path, so a core-path edit
            # doesn't get blocked a second time for that separately.
            # Second-review follow-up (item 1): _is_core_path() is
            # computed LAZILY here, only when actually needed --
            # calling it unconditionally before Stage 1 wasted ~49ms
            # per validation (Layer 2's marker-file walk) even when
            # no_visible_text is False (the common case) and the
            # result went unused, and crashed on file_path=None
            # before _regex_stage1_check's own None-guard was ever
            # reached.
            _is_core = _is_core_path(
                file_path, core_path_segments, exclusions, extensions
            )
            _example = _write_edit_no_visible_text_example(
                tool_name, file_path, _is_core
            )
            _raw = build_no_visible_text_notice(_example) + "\n\n" + _raw
        return {
            "approved": False,
            "reviewer": "RegEx",
            # "feedback" (tagged) is the Claude-facing block reason;
            # "raw_feedback" (untagged) is for governance/telemetry
            # consumers such as the claude-usage governance feed
            # (Story #101 B2 — never tag the governance feed).
            "feedback": format_tag(_raw, "intent_validation_block"),
            "raw_feedback": _raw,
        }

    elif stage1_response_upper == "NO_TDD":
        # TDD declaration missing for core path
        _raw = f"""⛔ TDD Required for Core Code

You're modifying core code: {file_path}

No test declaration found in your CURRENT message. Before modifying core code, you must either:

1. Declare the corresponding test IN YOUR CURRENT MESSAGE:
   - TEST FILE: Which test file covers this change
   - TEST SCOPE: What behavior the test validates

2. OR quote the user's explicit permission to skip TDD

Example with test declaration (in same message as Write/Edit):
  "INTENT: Modify src/auth.py to add password validation.
   Test coverage: tests/test_auth.py - test_password_validation_rejects_weak_passwords()"

Example citing user permission (in same message as Write/Edit):
  "INTENT: Modify src/auth.py to add password validation.
   User permission to skip TDD: User said 'skip tests for this' in message 3."

CRITICAL: Quote must reference actual user words from recent context."""
        if declare_intent_hint:
            # Story #155 AC11: same hint as the NO branch above.
            _raw = build_declare_intent_hint(file_path) + "\n\n" + _raw
        if no_visible_text:
            # Issue #150: same lead-with-the-fix notice as the NO
            # branch above -- reachable via the n-back rescue (a
            # no-visible-text anchor's empty override falls back to
            # extract_current_assistant_message, which can resolve a
            # PRIOR turn's real visible INTENT with no TDD
            # declaration). The agent still needs to know its OWN
            # current turn had no visible text. This branch only
            # fires for a core path, so the Test coverage: line is
            # always included (via the same shared example builder as
            # the NO branch, Messi Anti-Duplication). Second-review
            # follow-up (item 1): computed lazily, same rationale as
            # the NO branch above.
            _is_core = _is_core_path(
                file_path, core_path_segments, exclusions, extensions
            )
            _example = _write_edit_no_visible_text_example(
                tool_name, file_path, _is_core
            )
            _raw = build_no_visible_text_notice(_example) + "\n\n" + _raw
        return {
            "approved": False,
            "tdd_failure": True,
            "reviewer": "RegEx",
            "feedback": format_tag(_raw, "intent_validation_block"),
            "raw_feedback": _raw,
        }

    # Stage 1 passed - proceed to Stage 2
    log_debug("intent_validator", "=== STAGE 1 PASSED - PROCEEDING TO STAGE 2 ===")

    # SDK availability check — fail closed for Stage 2 LLM call
    if not SDK_AVAILABLE and hook_model in ("auto", "sonnet", "opus", "haiku"):
        _raw = _sdk_unavailable_message()
        return {
            "approved": False,
            "feedback": format_tag(_raw, "intent_validation_block"),
            "raw_feedback": _raw,
        }

    # STAGE 2: Comprehensive code review
    stage2_prompt = _build_stage2_prompt(
        messages,
        code,
        file_path,
        tool_name,
        surrounding_context_section=edit_surrounding_context_section,
        sibling_edits_section=edit_sibling_edits_section,
        write_case=write_case,
    )
    log_debug("intent_validator", f"Stage 2 prompt length: {len(stage2_prompt)} chars")

    _stage2_degradation: Dict[str, Any] = {}
    stage2_feedback, reviewer = _call_stage2_validation(
        stage2_prompt,
        hook_model=hook_model,
        _degradation=_stage2_degradation,
        _deadline=_deadline,
        _db_path=_db_path,
    )
    log_debug(
        "intent_validator",
        f"Stage 2 SDK response length: {len(stage2_feedback)} chars",
    )
    log_debug(
        "intent_validator",
        f"Stage 2 feedback preview: {stage2_feedback[:200] if stage2_feedback else '(empty)'}...",
    )
    log_debug("intent_validator", f"Stage 2 reviewer: {reviewer}")

    if verdict_passes(stage2_feedback):
        # APPROVED (guarded-lenient) response = approved
        log_debug("intent_validator", "=== STAGE 2 APPROVED ===")
        return {
            "approved": True,
            "reviewer": reviewer,
            # Issue #131: surfaces the degraded-review flag from a
            # competitive verifier infra failure or single-model
            # fallback, so the pre-tool gate can record telemetry.
            "degradation": _stage2_degradation,
        }
    elif not stage2_feedback:
        # Issue #142: verdict_passes("") is False, but an empty response
        # is NEVER genuine reviewer feedback — it means EVERY reviewer
        # (competitive zero survivors, or a single provider whose
        # Anthropic fallback also failed) failed to respond at all.
        # Relaying "" through format_reviewer_relay() produced a blank
        # "[expr] " block recorded under "intent_validation_cleancode",
        # indistinguishable from a genuine clean-code rejection. Build a
        # pace-maker-authored explanation instead, tagged as an
        # infrastructure failure (fail_closed_error channel — no
        # reviewer said anything, so reviewer-relay would be wrong),
        # under its own blockage category.
        log_debug("intent_validator", "=== STAGE 2 REVIEWER UNAVAILABLE (empty) ===")
        _raw = build_reviewer_unavailable_message(_stage2_degradation)
        _unavailable_feedback = format_tag(_raw, "fail_closed_error")
        if declare_intent_hint and intent_from_tool:
            # Bug #159 (re-review): the declaration was consumed and the
            # chain ended even though nothing was reviewed -- say so. The
            # unavailable text above and raw_feedback are untouched.
            _unavailable_feedback += "\n\n" + format_tag(
                build_declare_intent_unavailable_consumed_note(),
                "intent_validation_block",
            )
        return {
            "approved": False,
            "reviewer_unavailable_failure": True,
            "feedback": _unavailable_feedback,
            "raw_feedback": _raw,
            "reviewer": reviewer,
            # Issue #142 code-review follow-up (item 4): carries
            # failed_providers/zero_survivors out to the caller so
            # hook.py can attach them to record_blockage()'s details —
            # previously written by run_mechanical()/
            # resolve_and_call_with_reviewer() but never read anywhere.
            "degradation": _stage2_degradation,
        }
    else:
        # Any other response = blocked with feedback
        log_debug("intent_validator", "=== STAGE 2 BLOCKED (has feedback) ===")

        # Parse structured CLASSIFICATION line from stage 2 response.
        # MUST run on the untagged stage2_feedback — the wrapped/tagged
        # variant below is a rendering concern only, never fed back into
        # classification parsing.
        _classification = _parse_stage2_classification(stage2_feedback)

        return {
            "approved": False,
            # Stage 2 feedback is a third-party reviewer's own text
            # relayed verbatim -> reviewer-relay tag (Story #101),
            # never the plain pace-maker tag. "raw_feedback" is the
            # untagged reviewer text for governance/telemetry
            # consumers (Story #101 B2).
            # Bug #159: the declare_intent hint follows the relay segment
            # (never inside it) unless the intent came from the tool.
            "feedback": _append_declare_intent_hint(
                format_reviewer_relay(stage2_feedback, reviewer),
                file_path,
                declare_intent_hint,
                intent_from_tool,
            ),
            "raw_feedback": stage2_feedback,
            "clean_code_failure": _classification == "clean_code",
            "bug_failure": _classification == "bug",
            "reviewer": reviewer,
        }


def validate_intent_and_code(
    messages: List[str],
    code: str,
    file_path: str,
    tool_name: str,
    hook_model: str = "auto",
    current_message_override: str = "",
    _deadline: Optional[float] = None,
    no_visible_text: bool = False,
    stage1_fallback_messages: Optional[List[str]] = None,
    reasoning_summary_relaxed_text: Optional[str] = None,
    reasoning_summary_intent_source: Optional[str] = None,
    reasoning_summary_recent_context: Optional[List["tuple[str, str]"]] = None,
    edit_surrounding_context_section: str = "",
    edit_sibling_edits_section: str = "",
    stage2_db_path: Optional[str] = None,
    write_case: Optional[str] = None,
    declare_intent_hint: bool = False,
    tool_declared_tdd: Optional[bool] = None,
    intent_from_tool: bool = False,
) -> dict:
    """
    Two-stage pre-tool validation with short-circuit logic.

    Stage 1: Fast regex structural check (CURRENT message only)
      - Checks INTENT: marker exists anywhere in message
      - Checks file name is mentioned
      - Checks TDD declaration for core paths
      - Pure regex, no LLM call (~0ms)

    Stage 2: Comprehensive code review (only if Stage 1 passes)
      - Validates intent specificity (not vague)
      - Validates code matches declared intent
      - Checks for clean code violations
      - Uses LLM for quality

    Args:
        messages: Last 4 assistant messages (current + 3 before). Used for
            Stage 2's prompt (``_build_stage2_prompt``), and as Stage 1's
            n-back fallback source when ``stage1_fallback_messages`` is not
            supplied.
        code: Proposed code that will be written
        file_path: Target file path
        tool_name: Write or Edit
        stage1_fallback_messages: Issue #140 code-review findings 1-3.
            Optional PROSE-ONLY n-back message list (each entry is a
            message's structural text -- never rendered with tool
            parameters), used INSTEAD of ``messages`` for Stage 1's
            ``extract_current_assistant_message`` fallback when
            ``current_message_override`` is falsy. The real hook path
            supplies this via ``transcript_reader.
            get_last_n_messages_for_validation(..., _with_prose=True)``, so
            an ``INTENT:``-looking string embedded only in a Write's
            ``content`` or an Edit's ``old_string``/``new_string`` can
            never satisfy Stage 1's marker check, and Stage 1's
            file-mention/TDD-declaration/version-bump checks (which run
            against whatever ``extract_current_assistant_message`` returns)
            can never be satisfied by rendered tool parameters either --
            all three are closed structurally, by never handing Stage 1 any
            tool-rendered content, rather than by post-hoc string-stripping.
            Defaults to ``None``, which falls back to ``messages`` --
            preserves the exact pre-existing behavior for every direct
            caller (tests, etc.) that does not supply this parameter.
        no_visible_text: Issue #141, broadened by issue #148. True when the
            caller (hook.py) determined the anchored turn had NO visible
            text block at all -- whether or not it also had a `thinking`
            block with content, an empty `thinking` block, or no
            `thinking` block whatsoever. In the thinking-present case the
            model wrote its INTENT (and/or test coverage) declaration only
            inside its own (summarized, not-shown-to-the-user) reasoning;
            in the other cases it never wrote a declaration anywhere
            visible at all. When True AND Stage 1 rejects with EITHER "NO"
            (missing INTENT) OR "NO_TDD" (missing test coverage
            declaration -- reachable via the n-back rescue, where a
            no-visible-text anchor's empty override falls back to a prior
            turn's visible INTENT with no TDD declaration), the block
            reason LEADS with build_no_visible_text_notice()'s
            pilot-validated notice (issue #150; superseded the old
            append-at-the-end THINKING_ONLY_NOTICE from issue #141/#148)
            so the agent understands WHY it was blocked instead of
            believing it already declared the missing piece.
        reasoning_summary_relaxed_text: Issue #151 code review H2/M1. The
            caller (hook.py, via
            ``resolve_reasoning_summary_intent_source``) has ALREADY
            decided whether this exception-model turn takes the RELAXED
            path. Non-``None`` means "take the relaxed path with THIS
            combined (visible text + reasoning summary) intent text" --
            Stage 1 has NO INTENT:/TDD/version-bump regex at all for that
            path (see ``_validate_reasoning_summary_path``), and Stage 2
            uses a dedicated prompt template framing the intent as an
            auto-summarized excerpt. ``None`` (the default) means "take
            the STRICT path" -- reached either because this is a
            non-exception model, because the anchor's own visible text
            already carries a real INTENT: declaration (H2), or because
            the combined relaxed text was empty (M1, falls through to the
            SAME n-back rescue a non-exception model gets). Defaults to
            ``None``, which never touches the relaxed branch -- behaviour
            for every non-exception-model call is provably byte-identical
            to before this story (same code path, ``_validate_normal_path``,
            unchanged).
        reasoning_summary_intent_source: Issue #151 code review H2/L1.
            ``None`` for a non-exception model (never tag "intent_source"
            in the result — byte-identical). ``"declaration"``,
            ``"visible_text"``, or ``"reasoning_summary"`` for an
            exception-model turn — tags the result's "intent_source" key
            regardless of which path (strict or relaxed) was ultimately
            taken, so telemetry can distinguish a fully-compliant Opus
            turn (declaration) from a relaxed approval.
        reasoning_summary_recent_context: Issue #151 live-test follow-up.
            Optional prior-turn ``(visible_text, thinking_text)`` tuples,
            oldest to newest, threaded ONLY into the RELAXED path's Stage
            2 prompt (see ``_build_recent_context_section``) -- ignored
            entirely on the strict/declaration path and for non-exception
            models. ``None`` (the default) omits the section.
        edit_surrounding_context_section: Issue #153. Pre-rendered section
            (via ``_build_edit_surrounding_context_section``) showing the
            CURRENT on-disk file content around an Edit's ``old_string``.
            Threaded into BOTH the strict and relaxed paths' Stage 2
            prompts. "" (the default) omits it entirely -- byte-identical
            to before this issue for every caller that doesn't supply it
            (Write calls, and every pre-existing test/caller).
        edit_sibling_edits_section: Issue #153. Pre-rendered section (via
            ``_build_sibling_edits_section``) showing the OTHER Write/Edit
            calls in the same anchored turn. Same threading and
            byte-identical-by-default guarantee as
            ``edit_surrounding_context_section`` above.
        stage2_db_path: Issue #153 code review MUST-FIX 1(a). Threaded to
            ``_call_stage2_validation`` -> ``_mask_reviewer_prompt``,
            which masks ALL stored secrets in the WHOLE Stage 2 prompt
            immediately before it reaches the reviewer provider. ``None``
            (the default) is a DELIBERATE no-op (masking skipped
            entirely) -- test-isolation safety, see
            ``_mask_reviewer_prompt``'s docstring. The real Write/Edit
            gate always supplies hook.py's own ``DEFAULT_DB_PATH``.
        declare_intent_hint: Story #155 AC11. True when the declare_intent
            tool path is enabled: Stage-1 NO / NO_TDD block messages then
            carry one extra sentence pointing at the tool (preferred) with
            the visible ``INTENT:`` line kept as the fallback. ``False``
            (the default) leaves every block message byte-identical to
            pre-#155. Bug #159: Stage 2 (reviewer) rejections of a
            non-tool intent -- relaxed #151 path and strict path alike --
            carry the same hint, appended OUTSIDE the reviewer-relay
            segment as its own pace-maker-tagged block. Reviewer-
            unavailable / SDK-unavailable / internal-error blocks never do
            (infrastructure failures, not declaration problems).
        intent_from_tool: Bug #159. True when the intent being judged came
            from a declare_intent declaration or chain: a Stage 2 rejection
            then gets the "declaration consumed -- declare again" note
            instead of the review hint (the agent already used the tool, but
            the rejection used its declaration up). Stage-1 hints are
            unchanged. ``False`` by default.

    ``tool_declared_tdd`` (Story #155 / review M2): ``None`` for a text
    declaration (unchanged regex scan). For a tool-sourced intent, True iff
    a non-blank structured ``test_coverage`` was declared -- Stage 1 then
    decides TDD ONLY from it (see ``_regex_stage1_check``).

    Note (Story #155): ``reasoning_summary_intent_source`` may also be
    ``"declare_intent"`` / ``"declare_intent_chain"`` -- the caller then
    supplies the synthesized declaration message as
    ``current_message_override`` and ``reasoning_summary_relaxed_text`` is
    ``None``, so the declaration takes the STRICT path (Stage-1 regex incl.
    TDD, then Stage 2) and the result is tagged with that source.

    Returns:
        {"approved": True} if all checks pass
        {"approved": False, "feedback": "..."} if violations found
    """
    try:
        # Load exclusions/core-path-segments/extensions FIRST: both the
        # relaxed reasoning-summary path below and the strict
        # _validate_normal_path need them. This is NOT a "pure read" --
        # core_paths.load_paths_with_migration() can perform a one-time
        # disk WRITE (issue #92's migration) the first time it runs
        # against an existing customized core_paths.yaml missing the 4
        # newer default words. Loading here, before branching, has no
        # OTHER observable side effect and changes nothing for the
        # strict-path's own pre-existing behavior.
        from .constants import (
            DEFAULT_EXCLUDED_PATHS_PATH,
            DEFAULT_CORE_PATHS_PATH,
            DEFAULT_EXTENSION_REGISTRY_PATH,
        )
        from .excluded_paths import load_exclusions
        from . import core_paths
        from . import extension_registry

        exclusions = load_exclusions(DEFAULT_EXCLUDED_PATHS_PATH)
        # Wires core_paths.yaml into the live hook path (issue #92) —
        # running the one-time migration first so an existing customized
        # file picks up the 4 new default words.
        core_path_segments = core_paths.load_paths_with_migration(
            DEFAULT_CORE_PATHS_PATH
        )
        extensions = extension_registry.load_extensions(DEFAULT_EXTENSION_REGISTRY_PATH)

        if reasoning_summary_relaxed_text is not None:
            # Issue #151 code review H2/M1: the caller (hook.py, via
            # resolve_reasoning_summary_intent_source) has ALREADY proven
            # the anchor's own visible text has NO real INTENT: marker and
            # the combined relaxed text is non-empty before setting this
            # parameter -- take the RELAXED path.
            return _validate_reasoning_summary_path(
                intent_text=reasoning_summary_relaxed_text,
                intent_source=reasoning_summary_intent_source,
                code=code,
                file_path=file_path,
                hook_model=hook_model,
                _deadline=_deadline,
                exclusions=exclusions,
                core_path_segments=core_path_segments,
                extensions=extensions,
                recent_context=reasoning_summary_recent_context,
                edit_surrounding_context_section=edit_surrounding_context_section,
                edit_sibling_edits_section=edit_sibling_edits_section,
                _db_path=stage2_db_path,
                write_case=write_case,
                declare_intent_hint=declare_intent_hint,
            )

        # STRICT path -- either a non-exception model, an exception-model
        # turn whose anchor's own visible text already carries a real
        # INTENT: declaration (H2), or an exception-model turn whose
        # relaxed intent text was empty and therefore falls through to
        # this SAME code a non-exception model would take, including the
        # prose-only n-back rescue (M1, #140).
        result = _validate_normal_path(
            messages=messages,
            code=code,
            file_path=file_path,
            tool_name=tool_name,
            hook_model=hook_model,
            current_message_override=current_message_override,
            _deadline=_deadline,
            no_visible_text=no_visible_text,
            stage1_fallback_messages=stage1_fallback_messages,
            exclusions=exclusions,
            core_path_segments=core_path_segments,
            extensions=extensions,
            edit_surrounding_context_section=edit_surrounding_context_section,
            edit_sibling_edits_section=edit_sibling_edits_section,
            _db_path=stage2_db_path,
            write_case=write_case,
            declare_intent_hint=declare_intent_hint,
            tool_declared_tdd=tool_declared_tdd,
            intent_from_tool=intent_from_tool,
        )
        if reasoning_summary_intent_source is not None:
            # Issue #151 code review H2/M1/L1: an exception-model turn
            # that used the STRICT path -- tag it so telemetry can still
            # distinguish it from a non-exception-model call, which never
            # sets this key at all (byte-identical, L1).
            result["intent_source"] = reasoning_summary_intent_source
        return result

    except Exception as e:
        log_warning("intent_validator", "Two-stage validation failed", e)
        log_debug("intent_validator", f"=== VALIDATION EXCEPTION: {str(e)} ===")
        # Fail closed on unexpected errors
        _raw = f"""⛔ Intent Validation System Error

An unexpected error occurred during intent validation: {str(e)}

Failing closed to prevent bypassing validation requirements.
Please retry your operation or report this issue."""
        return {
            "approved": False,
            "feedback": format_tag(_raw, "intent_validation_block"),
            "raw_feedback": _raw,
        }
