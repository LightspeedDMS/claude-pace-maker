#!/bin/bash
# Claude Pace Maker - Plugin Hook Entry Point
#
# Single entry point for all 7 hook types when installed as a Claude Code plugin.
# Invoked as: bash ${CLAUDE_PLUGIN_ROOT}/scripts/hook.sh <hook_type>
#
# Filesystem bootstrap runs on every hook (--light). Python deps install only on
# SessionStart or when .bootstrap_ok is missing (--full).

set -e

HOOK_TYPE="${1:?Hook type argument required}"

# Resolve plugin root: prefer CLAUDE_PLUGIN_ROOT env var (set by Claude Code plugin system),
# fall back to relative path from this script's location.
if [ -n "$CLAUDE_PLUGIN_ROOT" ]; then
    PLUGIN_ROOT="$CLAUDE_PLUGIN_ROOT"
else
    PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fi

PACEMAKER_DIR="$HOME/.claude-pace-maker"
CONFIG_FILE="$PACEMAKER_DIR/config.json"
DEBUG_LOG="$PACEMAKER_DIR/hook_debug.log"

# shellcheck source=scripts/bootstrap-plugin.sh
source "$(dirname "$0")/bootstrap-plugin.sh"

# Cheap filesystem wiring on every hook (no pip).
bootstrap_light

# ---------------------------------------------------------------------------
# Check if pace-maker is enabled (before deps install and hook execution)
# ---------------------------------------------------------------------------
ENABLED=$(jq -r '.enabled // true' "$CONFIG_FILE" 2>/dev/null || echo "true")
if [ "$ENABLED" != "true" ]; then
    exit 0
fi

# Full bootstrap: SessionStart or first run before .bootstrap_ok exists.
if [ "$HOOK_TYPE" = "session_start" ] || bootstrap_needs_full; then
    bootstrap_full || true
fi

# ---------------------------------------------------------------------------
# Resolve Python command and PYTHONPATH
# ---------------------------------------------------------------------------
find_python() {
    resolve_python 2>/dev/null || echo "python3"
}

INSTALL_MARKER="$PACEMAKER_DIR/install_source"
if [ -f "$INSTALL_MARKER" ]; then
    SOURCE_DIR=$(cat "$INSTALL_MARKER")
    if [[ "$SOURCE_DIR" == *"pipx"* ]]; then
        VENV_PYTHON=$(echo "$SOURCE_DIR" | sed 's|/share/claude-pace-maker|/bin/python3|')
        if [ -x "$VENV_PYTHON" ]; then
            PYTHON_CMD="$VENV_PYTHON"
        else
            PYTHON_CMD=$(find_python)
        fi
    else
        PYTHON_CMD=$(find_python)
        export PYTHONPATH="${SOURCE_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"
    fi
else
    PYTHON_CMD=$(find_python)
    export PYTHONPATH="${PLUGIN_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
fi

# ---------------------------------------------------------------------------
# Execute pacemaker hook - graceful degradation on Python failure
# ---------------------------------------------------------------------------
if ! $PYTHON_CMD -m pacemaker.hook "$HOOK_TYPE" 2>>"$DEBUG_LOG"; then
    echo "[hook.sh] pacemaker.hook $HOOK_TYPE failed - check $DEBUG_LOG" >>"$DEBUG_LOG"
fi

exit 0
