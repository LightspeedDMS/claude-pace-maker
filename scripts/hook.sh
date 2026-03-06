#!/bin/bash
# Claude Pace Maker - Plugin Hook Entry Point
#
# Single entry point for all 7 hook types when installed as a Claude Code plugin.
# Invoked as: bash ${CLAUDE_PLUGIN_ROOT}/scripts/hook.sh <hook_type>
#
# Implements lazy-init: bootstraps ~/.claude-pace-maker/ on first run.

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

# ---------------------------------------------------------------------------
# Lazy-init: bootstrap ~/.claude-pace-maker/ on first plugin run
# ---------------------------------------------------------------------------
lazy_init() {
    # Create config directory if it doesn't exist
    mkdir -p "$PACEMAKER_DIR"

    # Copy config.defaults.json -> config.json if config doesn't exist
    if [ ! -f "$CONFIG_FILE" ]; then
        local defaults_src="$PLUGIN_ROOT/config/config.defaults.json"
        if [ -f "$defaults_src" ]; then
            cp "$defaults_src" "$CONFIG_FILE"
        else
            # Fallback: write minimal defaults inline
            cat > "$CONFIG_FILE" <<'EOF'
{
  "enabled": true,
  "log_level": 2,
  "langfuse_enabled": false,
  "intent_validation_enabled": true,
  "tdd_enabled": true,
  "tempo_mode": "auto",
  "five_hour_limit_enabled": true,
  "weekly_limit_enabled": false
}
EOF
        fi
    fi

    # Copy source_code_extensions.json if it doesn't exist
    local extensions_dst="$PACEMAKER_DIR/source_code_extensions.json"
    if [ ! -f "$extensions_dst" ]; then
        local extensions_src="$PLUGIN_ROOT/config/source_code_extensions.json"
        if [ -f "$extensions_src" ]; then
            cp "$extensions_src" "$extensions_dst"
        fi
    fi

    # Create/update CLI symlink in ~/.local/bin/
    local local_bin="$HOME/.local/bin"
    mkdir -p "$local_bin"
    local cli_target="$PLUGIN_ROOT/scripts/pace-maker"
    local cli_symlink="$local_bin/pace-maker"
    # Always update symlink to point to current plugin root (handles plugin upgrades and legacy file-based installs)
    if [ -L "$cli_symlink" ] || [ ! -e "$cli_symlink" ] || [ -f "$cli_symlink" ]; then
        ln -sf "$cli_target" "$cli_symlink"
    fi
}

# Run lazy-init unconditionally (idempotent - only creates missing files)
lazy_init

# ---------------------------------------------------------------------------
# Check if pace-maker is enabled
# ---------------------------------------------------------------------------
ENABLED=$(jq -r '.enabled // true' "$CONFIG_FILE" 2>/dev/null || echo "true")
if [ "$ENABLED" != "true" ]; then
    exit 0
fi

# ---------------------------------------------------------------------------
# Find best available Python 3.10+
# ---------------------------------------------------------------------------
find_python() {
    for py in python3.11 python3.10 python3; do
        if command -v "$py" >/dev/null 2>&1; then
            echo "$py"
            return 0
        fi
    done
    echo "python3"
}

# ---------------------------------------------------------------------------
# Resolve Python command and PYTHONPATH
# ---------------------------------------------------------------------------
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
    # Log the failure but exit 0 (graceful) to avoid blocking Claude Code
    echo "[hook.sh] pacemaker.hook $HOOK_TYPE failed - check $DEBUG_LOG" >>"$DEBUG_LOG"
fi

exit 0
