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
}

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
# install.sh reads this when registering the hook, and a test asserts the
# internal budgets still fit inside it.
PRE_TOOL_HOOK_TIMEOUT_SECONDS = 60

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

# Blockage telemetry categories (Story #21)
# Used for tracking and categorizing hook blockages
BLOCKAGE_CATEGORIES = (
    "intent_validation",  # Missing/vague INTENT: marker
    "intent_validation_tdd",  # TDD declaration missing for core code
    "intent_validation_cleancode",  # Clean code rule violation
    "intent_validation_bug",  # Clear logic bug detected in proposed code
    "intent_validation_dangerbash",  # Danger bash command intent mismatch
    "intent_validation_deferred",  # Transcript not yet flushed — fails CLOSED + re-issue (v2.33.2)
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
    "pacing_tempo": "Pacing Tempo",
    "pacing_quota": "Pacing Quota",
    "other": "Other",
}
