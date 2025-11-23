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

# Find best Python version (3.11+ for SDK support, fallback to 3.10+)
find_python() {
    for py in python3.11 python3.10 python3; do
        if command -v "$py" >/dev/null 2>&1; then
            echo "$py"
            return 0
        fi
    done
    echo "python3"
}

if [ -f "$INSTALL_MARKER" ]; then
    SOURCE_DIR=$(cat "$INSTALL_MARKER")
    
    # Check if this is a pipx installation (has pipx in path)
    if [[ "$SOURCE_DIR" == *"pipx"* ]]; then
        # Pipx installation - Python is in venv/bin
        # SOURCE_DIR is .../venvs/claude-pace-maker/share/claude-pace-maker
        # We need .../venvs/claude-pace-maker/bin/python3
        VENV_PYTHON=$(echo "$SOURCE_DIR" | sed 's|/share/claude-pace-maker|/bin/python3|')
        if [ -x "$VENV_PYTHON" ]; then
            # Use pipx venv Python which has pacemaker installed
            PYTHON_CMD="$VENV_PYTHON"
        else
            # Fallback to system Python
            PYTHON_CMD=$(find_python)
        fi
    else
        # Development installation - use PYTHONPATH
        PYTHON_CMD=$(find_python)
        export PYTHONPATH="$SOURCE_DIR/src:$PYTHONPATH"
    fi
else
    # No marker - try system Python
    PYTHON_CMD=$(find_python)
fi

# Determine hook type from script name
HOOK_TYPE="user_prompt_submit"

# Run the hook
$PYTHON_CMD -m pacemaker.hook $HOOK_TYPE 2>> "$PACEMAKER_DIR/hook_debug.log"
