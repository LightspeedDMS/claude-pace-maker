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
# Resolve Python command and PYTHONPATH (managed venv is canonical).
#
# When the managed venv isn't ready, fall back to system python — but log
# loudly so the user (or anyone reading hook_debug.log) sees that the hook
# is running on an interpreter that doesn't have requests/pyyaml/sdk
# installed and the pacemaker.hook invocation below will likely fail.
# Throttled to once per hour via a marker file to keep the log readable.
# ---------------------------------------------------------------------------
_log_python_fallback() {
    local using="$1" reason="$2"
    local marker="$PACEMAKER_DIR/.python_fallback_warn"
    local now mtime age=999999
    now=$(date +%s 2>/dev/null || echo 0)
    if [ -f "$marker" ]; then
        mtime=$(stat -f %m "$marker" 2>/dev/null || stat -c %Y "$marker" 2>/dev/null || echo 0)
        age=$(( now - mtime ))
    fi
    if [ "$age" -lt 3600 ]; then
        return 0
    fi
    touch "$marker" 2>/dev/null || true
    {
        echo "[hook.sh] WARNING: managed venv at $VENV_DIR is unavailable ($reason)."
        echo "[hook.sh]          Falling back to: $using"
        echo "[hook.sh]          Hook will likely fail until you run: pace-maker doctor"
    } >>"$DEBUG_LOG"
}

resolve_hook_python() {
    local venv_py system_py
    if venv_py=$(resolve_runtime_python 2>/dev/null); then
        echo "$venv_py"
        return 0
    fi
    if system_py=$(resolve_python 2>/dev/null); then
        _log_python_fallback "$system_py" "venv missing or deps not importable"
        echo "$system_py"
        return 0
    fi
    _log_python_fallback "python3" "no Python 3.10+ found"
    echo "python3"
}

INSTALL_MARKER="$PACEMAKER_DIR/install_source"
if [ -f "$INSTALL_MARKER" ]; then
    SOURCE_DIR=$(cat "$INSTALL_MARKER")
    if [[ "$SOURCE_DIR" == *"pipx"* ]]; then
        PIPX_PYTHON=$(echo "$SOURCE_DIR" | sed 's|/share/claude-pace-maker|/bin/python3|')
        if [ -x "$PIPX_PYTHON" ]; then
            PYTHON_CMD="$PIPX_PYTHON"
        else
            PYTHON_CMD=$(resolve_hook_python)
        fi
    else
        PYTHON_CMD=$(resolve_hook_python)
        export PYTHONPATH="${SOURCE_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"
    fi
else
    PYTHON_CMD=$(resolve_hook_python)
    export PYTHONPATH="${PLUGIN_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
fi

# ---------------------------------------------------------------------------
# Execute pacemaker hook - graceful degradation on Python failure
# ---------------------------------------------------------------------------
if ! $PYTHON_CMD -m pacemaker.hook "$HOOK_TYPE" 2>>"$DEBUG_LOG"; then
    echo "[hook.sh] pacemaker.hook $HOOK_TYPE failed - check $DEBUG_LOG" >>"$DEBUG_LOG"
fi

exit 0
