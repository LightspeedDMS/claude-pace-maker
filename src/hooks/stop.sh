#!/bin/bash
#
# Claude Code Hook
#

set -e

PACEMAKER_DIR="$HOME/.claude-pace-maker"
CONFIG_FILE="$PACEMAKER_DIR/config.json"

# Check if pace maker is enabled
if [ ! -f "$CONFIG_FILE" ]; then
    exit 0
fi

ENABLED=$(jq -r '.enabled // true' "$CONFIG_FILE" 2>/dev/null || echo "true")
if [ "$ENABLED" != "true" ]; then
    exit 0
fi

# Determine which Python to use and how to invoke pacemaker
INSTALL_MARKER="$PACEMAKER_DIR/install_source"

if [ -f "$INSTALL_MARKER" ]; then
    SOURCE_DIR=$(cat "$INSTALL_MARKER")
    
    # Check if this is a pipx installation (has pipx in path)
    if [[ "$SOURCE_DIR" == *"pipx"* ]]; then
        # Pipx installation - use pipx's Python interpreter
        VENV_PYTHON=$(find "$SOURCE_DIR" -path "*/bin/python3" -o -path "*/bin/python" | head -1)
        if [ -n "$VENV_PYTHON" ]; then
            # Use pipx venv Python which has pacemaker installed
            PYTHON_CMD="$VENV_PYTHON"
        else
            # Fallback to system Python with pipx inject
            PYTHON_CMD="python3"
        fi
    else
        # Development installation - use PYTHONPATH
        PYTHON_CMD="python3"
        export PYTHONPATH="$SOURCE_DIR/src:$PYTHONPATH"
    fi
else
    # No marker - try system Python
    PYTHON_CMD="python3"
fi

# Determine hook type from script name
HOOK_TYPE="stop"

# Run the hook
$PYTHON_CMD -m pacemaker.hook $HOOK_TYPE 2>> "$PACEMAKER_DIR/hook_debug.log"
