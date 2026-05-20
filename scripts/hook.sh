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
DEPS_MARKER="$PACEMAKER_DIR/.python_deps_installed"

# ---------------------------------------------------------------------------
# Find best available Python 3.10+ (shared by lazy-init and hook execution)
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

# Install runtime deps for the interpreter used by scripts/pace-maker (#!/usr/bin/env python3)
# and by hook execution (find_python). Idempotent: import check + marker file.
install_python_deps_for() {
    local python_cmd="$1"
    if [ -z "$python_cmd" ] || ! command -v "$python_cmd" >/dev/null 2>&1; then
        return 0
    fi

    if [ -f "$DEPS_MARKER" ] && [ "$(cat "$DEPS_MARKER" 2>/dev/null)" = "$python_cmd" ]; then
        if "$python_cmd" -c "import requests, yaml" 2>/dev/null; then
            return 0
        fi
    fi

    local packages=()
    "$python_cmd" -c "import requests" 2>/dev/null || packages+=("requests")
    "$python_cmd" -c "import yaml" 2>/dev/null || packages+=("pyyaml")
    "$python_cmd" -c "import claude_agent_sdk" 2>/dev/null || packages+=("claude-agent-sdk")

    if [ ${#packages[@]} -eq 0 ]; then
        echo "$python_cmd" >"$DEPS_MARKER"
        return 0
    fi

    if "$python_cmd" -m pip install --user "${packages[@]}" >/dev/null 2>&1; then
        echo "$python_cmd" >"$DEPS_MARKER"
        return 0
    fi
    if "$python_cmd" -m pip install --break-system-packages "${packages[@]}" >/dev/null 2>&1; then
        echo "$python_cmd" >"$DEPS_MARKER"
        return 0
    fi

    echo "[hook.sh] Warning: Could not install Python packages (${packages[*]}) for $python_cmd" >>"$DEBUG_LOG"
    return 0
}

install_plugin_python_deps() {
    local cli_py hook_py
    cli_py=$(command -v python3 2>/dev/null || true)
    hook_py=$(find_python)
    install_python_deps_for "$cli_py"
    if [ -n "$hook_py" ] && [ "$hook_py" != "$cli_py" ]; then
        install_python_deps_for "$hook_py"
    fi
}

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

    # Install requests/pyyaml/claude-agent-sdk for CLI (python3) and hook interpreters
    install_plugin_python_deps
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
