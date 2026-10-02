#!/usr/bin/env python3
"""
Shared constants for Pace Maker.

Centralizes default configuration values to eliminate duplication
and ensure consistency across modules.
"""

from pathlib import Path
from typing import Dict, Any

# Default configuration values
DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "base_delay": 5,
    "max_delay": 350,
    "threshold_percent": 0,
    "poll_interval": 300,
    "safety_buffer_pct": 95.0,
    "preload_hours": 12.0,
    "api_timeout_seconds": 10,
    "cleanup_interval_hours": 24,
    "retention_days": 60,
    "weekly_limit_enabled": True,
    "tempo_mode": "auto",  # Changed from tempo_enabled boolean to tempo_mode string
    "auto_tempo_threshold_minutes": 10,  # Inactivity threshold for auto mode
    "conversation_context_size": 5,
    "user_message_max_length": 4096,
    "intent_validation_enabled": False,
    "tdd_enabled": True,
    "stop_hook_token_budget": 16000,
    "stop_hook_first_n_pairs": 10,
    "log_level": 2,  # Default: WARNING level
    "preferred_subagent_model": "auto",  # Model preference: "opus", "sonnet", "haiku", "auto"
    "hook_model": "auto",  # Hook inference model: "auto", "sonnet", "opus", "gpt-5.4", "gpt-5.5" (legacy alias: "gpt-5"), "gemini-flash", "gemini-pro"
    "min_claude_version": "2.1.39",  # Minimum supported Claude Code version (Story #66 / issue #96)
    "reasoning_summary_intent_models": [
        "claude-opus-5-5"
    ],  # Issue #151: models whose anchored turn's reasoning summary (plus any visible text) is accepted as intent, bypassing the INTENT:/TDD/version-bump regex. Empty list restores strict behavior for every model.
    "intent_declaration_tool_enabled": True,  # Story #155: kill switch for the declare_intent MCP tool path. False = the PostToolUse recorder stores nothing, the Write/Edit gate skips the declaration/chain lookup, and guidance/block messages are byte-identical to pre-#155.
}

# Story #155 -- tool-first intent declaration (declare_intent MCP tool).
#
# The MCP server is named "pace-maker", so a user-scope registration
# (install.sh) yields the tool name mcp__pace-maker__declare_intent. When
# pace-maker is installed as a Claude Code PLUGIN, Claude Code prefixes the
# server with the plugin name: mcp__plugin_<plugin>_<server>__<tool>
# (precedent: mcp__plugin_atlassian_atlassian__authenticate for the
# "atlassian" plugin's "atlassian" server). BOTH names are recognized, from
# this ONE constant set -- never re-spelled at a call site.
INTENT_MCP_SERVER_NAME = "pace-maker"
INTENT_MCP_PLUGIN_NAME = "claude-pace-maker"
DECLARE_INTENT_TOOL = "declare_intent"
DECLARE_INTENT_TOOL_NAMES = frozenset(
    {
        f"mcp__{INTENT_MCP_SERVER_NAME}__{DECLARE_INTENT_TOOL}",
        f"mcp__plugin_{INTENT_MCP_PLUGIN_NAME}_{INTENT_MCP_SERVER_NAME}__"
        f"{DECLARE_INTENT_TOOL}",
    }
)

# Declarations and chains older than this are deleted on EVERY store access
# (record and gate). 60 minutes also comfortably covers a review that runs up
# to PRE_TOOL_HOOK_TIMEOUT_SECONDS (5 minutes since #165).
INTENT_DECLARATION_TTL_SECONDS = 60 * 60

# Bug #163: when a rejection deletes an agent's declaration/chain for a file,
# a marker (session, agent, file) is kept this long so sibling edits of the
# same batch that are then blocked for having no declaration can be told to
# re-declare. Deliberately short: a batch of parallel edits arrives within
# seconds, and a stale marker would mislabel an unrelated later block.
REJECTED_DECLARATION_MARKER_TTL_SECONDS = 2 * 60

# Caps on declared text (review L4). The transcript path bounds the assistant
# message it reads to transcript_reader.MAX_MESSAGE_LENGTH (10000 chars); the
# synthesized declaration message gets the SAME ceiling (a test pins the two
# equal), and each free-text field (change / goal / test_coverage) is
# truncated to DECLARATION_MAX_FIELD_CHARS at record time so the store and
# the message stay bounded. Over-long text is TRUNCATED (ending in "…"), not
# rejected.
DECLARATION_MAX_MESSAGE_LENGTH = 10000
DECLARATION_MAX_FIELD_CHARS = 3000

# Bug #157 -- guaranteed subagent guidance delivery. The per-(session, agent)
# "SubagentStart completed / guidance delivered" record is purged after this
# long (on every store access), so the table stays bounded. Deliberately much
# longer than INTENT_DECLARATION_TTL_SECONDS: the record is consulted on a
# subagent's FIRST tool call, but a subagent can live for hours -- a purged
# record would make its later tool calls look like "never started" and repeat
# the late delivery.
SUBAGENT_GUIDANCE_TTL_SECONDS = 24 * 60 * 60

# Issue #151 live-replay follow-up (round 3, CHANGE 1): RECENT CONTEXT is
# shown in the relaxed Stage 2 prompt ONLY when the current turn's own
# combined intent text is "terse" -- see
# intent_validator._should_include_recent_context(). Three live false
# blocks showed a DETAILED, file-naming current intent being judged
# against an EARLIER turn's stale plan surfaced via RECENT CONTEXT
# ("Reverting A" judged against "create variant A", "Restoring B, then
# variant C" judged against "remove ... variant B", a detailed
# docstring-update intent judged against "delete get_audit_logs").
RECENT_CONTEXT_TERSE_MAX_CHARS = 150

# Issue #154 live-replay follow-up: an earlier revision of this feature
# ALSO withheld RECENT CONTEXT whenever the current intent contained an
# explicit direction word (remove/revert/restore/etc.), on the theory that
# such an intent already states its own self-contained goal. Live replay
# showed the opposite: terse restore intents like "Reverting A..." and
# "Restoring B, then variant C" only make sense with the EARLIER turns
# that define what "A"/"B" even refer to -- dropping RECENT CONTEXT for
# them made a weak reviewer (haiku) block both at CHECK 0 as "too vague".
# The direction-MISJUDGMENT risk that clause existed to prevent is now
# handled by CHANGE 3's unified diff (explicit `-`/`+` markers), so the
# clause and its `RECENT_CONTEXT_DIRECTION_WORD_STEMS` constant were
# removed (Messi Anti-Orphan-Code) rather than kept as dead weight.

# Default file paths
DEFAULT_DB_PATH = str(Path.home() / ".claude-pace-maker" / "usage.db")
DEFAULT_CONFIG_PATH = str(Path.home() / ".claude-pace-maker" / "config.json")
DEFAULT_STATE_PATH = str(Path.home() / ".claude-pace-maker" / "state.json")
DEFAULT_EXTENSION_REGISTRY_PATH = str(
    Path.home() / ".claude-pace-maker" / "source_code_extensions.json"
)
DEFAULT_LOG_PATH = str(Path.home() / ".claude-pace-maker" / "pace-maker.log")
# Log rotation settings
DEFAULT_LOG_DIR = str(Path.home() / ".claude-pace-maker")
LOG_FILE_PREFIX = "pace-maker-"
LOG_FILE_SUFFIX = ".log"
LOG_RETENTION_DAYS = 15
DEFAULT_CLEAN_CODE_RULES_PATH = str(
    Path.home() / ".claude-pace-maker" / "clean_code_rules.yaml"
)
DEFAULT_CORE_PATHS_PATH = str(Path.home() / ".claude-pace-maker" / "core_paths.yaml")
DEFAULT_EXCLUDED_PATHS_PATH = str(
    Path.home() / ".claude-pace-maker" / "excluded_paths.yaml"
)
DEFAULT_DANGER_RULES_PATH = str(
    Path.home() / ".claude-pace-maker" / "danger_bash_rules.yaml"
)

# Log level constants
LOG_LEVEL_OFF = 0
LOG_LEVEL_ERROR = 1
LOG_LEVEL_WARNING = 2
LOG_LEVEL_INFO = 3
LOG_LEVEL_DEBUG = 4

# Throttling thresholds
PROMPT_INJECTION_THRESHOLD_SECONDS = 30
MAX_DELAY_SECONDS = 350  # 360s timeout - 10s safety margin

# PreToolUse budget (issue #108) — SINGLE SOURCE OF TRUTH.
#
# A killed PreToolUse hook is an UNVALIDATED TOOL CALL, not a block: the
# harness simply proceeds. So the gate must always return a verdict before the
# harness kills it, even if that verdict is degraded.
#
# This value previously existed only as a literal in install.sh, while the
# review budgets lived in inference/competitive.py, with nothing connecting
# them. They drifted until the worst case (60s reviewer wait + 30s synthesis,
# plus a 30s transcript-anchor wait) was double the allowance.
#
# install.sh and hooks/hooks.json both read this when registering the hook,
# and tests assert the internal budgets still fit inside it and that both
# registration paths match it (issue #152: a live replay against real
# codex-beast traffic showed the single-model review path -- 120s codex
# subprocess timeout, then the Anthropic SDK fallback on top of it, with no
# deadline awareness -- regularly exceeding the previous 120s allowance,
# producing silently unvalidated tool calls when the harness killed the
# hook). Raised 120 -> 180 so REVIEWER_WAIT_TIMEOUT_SEC/SYNTHESIS_TIMEOUT_SEC
# (competitive.py) and the new deadline-aware single-model clamp
# (inference/registry.py) all have real room.
#
# Issue #165: raised 180 -> 300 together with the reviewer CLI ceiling
# (REVIEWER_CLI_TIMEOUT_SECONDS, 120 -> 240). Requests to the beast queue up
# and some time out at 120s. With a 180s hook the #152 deadline clamp would
# have cut codex to ~160s; at 300s codex gets its full 240s and the Anthropic
# SDK fallback (MIN_SDK_FALLBACK_BUDGET_SECONDS, 15s) still fits behind it.
#
# FOUR places must agree on this number: this constant, install.sh,
# hooks/hooks.json and the live ~/.claude/settings.json (written by
# install.sh). tests/unit/test_pretool_budget.py and
# tests/unit/test_issue_165_reviewer_timeout.py check the first three.
PRE_TOOL_HOOK_TIMEOUT_SECONDS = 300

# Issue #165: ceiling for ONE reviewer CLI subprocess (codex, agy, gemini).
# Shared by the three providers so they cannot drift apart. Callers' deadlines
# (#152) can only shrink it, never raise it.
REVIEWER_CLI_TIMEOUT_SECONDS = 240

# Issue #165: Stop hook timeout, registered by install.sh and
# hooks/hooks.json (parity-tested). Raised 120 -> 300 so a 240s reviewer is
# not killed by the harness before it answers. The Stop review is bounded by
# STOP_REVIEW_BUDGET_SECONDS (below), threaded as a deadline from the very
# start of run_stop_hook down to the providers.
STOP_HOOK_TIMEOUT_SECONDS = 300

# Reserved for telemetry writes and emitting the block response.
PRE_TOOL_SAFETY_MARGIN_SECONDS = 10

# Ceiling on the transcript-anchor wait, which runs BEFORE the review and is
# sequential with it. Previously this lived only as a default argument in
# transcript_reader and was invisible to the budget, so the real chain
# (anchor -> reviewers -> synthesis) could reach 90s against a 60s allowance.
# On the common path the anchor resolves in milliseconds; this cap only binds
# when the transcript genuinely lags.
PRE_TOOL_ANCHOR_CAP_SECONDS = 30.0

# Wall-clock budget available to the whole review phase.
PRE_TOOL_REVIEW_BUDGET_SECONDS = (
    PRE_TOOL_HOOK_TIMEOUT_SECONDS - PRE_TOOL_SAFETY_MARGIN_SECONDS
)

# Issue #165: the Stop review deadline. run_stop_hook takes its clock at entry
# (so the unbounded Langfuse finalize that runs first counts) and the review
# must finish STOP_HOOK_SAFETY_MARGIN_SECONDS before the harness kills the
# hook: a killed Stop hook fails open but throws the verdict away. Mirrors the
# PreToolUse margin (telemetry writes and emitting the response).
STOP_HOOK_SAFETY_MARGIN_SECONDS = PRE_TOOL_SAFETY_MARGIN_SECONDS
STOP_REVIEW_BUDGET_SECONDS = STOP_HOOK_TIMEOUT_SECONDS - STOP_HOOK_SAFETY_MARGIN_SECONDS

# Blockage telemetry categories (Story #21)
# Used for tracking and categorizing hook blockages
BLOCKAGE_CATEGORIES = (
    "intent_validation",  # Missing/vague INTENT: marker
    "intent_validation_tdd",  # TDD declaration missing for core code
    "intent_validation_cleancode",  # Clean code rule violation
    "intent_validation_bug",  # Clear logic bug detected in proposed code
    "intent_validation_dangerbash",  # Danger bash command intent mismatch
    "intent_validation_deferred",  # Transcript not yet flushed — fails CLOSED + re-issue (v2.33.2)
    "intent_validation_reviewer_unavailable",  # Zero survivors — no reviewer responded at all (issue #142)
    "pacing_tempo",  # Tempo validation blocked
    "pacing_quota",  # Throttle delay applied
    "other",  # Catch-all for unexpected blockages
)

# Human-readable labels for blockage categories (Story #22)
# Used for CLI status command display
BLOCKAGE_CATEGORY_LABELS: Dict[str, str] = {
    "intent_validation": "Intent Validation",
    "intent_validation_tdd": "Intent TDD",
    "intent_validation_cleancode": "Clean Code",
    "intent_validation_bug": "Bug Detected",
    "intent_validation_dangerbash": "Danger Bash",
    "intent_validation_deferred": "IV Deferred",
    "intent_validation_reviewer_unavailable": "Reviewer Unavailable",
    "pacing_tempo": "Pacing Tempo",
    "pacing_quota": "Pacing Quota",
    "other": "Other",
}
