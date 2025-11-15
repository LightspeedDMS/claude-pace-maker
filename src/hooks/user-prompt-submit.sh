#!/bin/bash
#
# Claude Code Hook: user-prompt-submit
# Triggers when user submits a prompt
#

set -e

PACEMAKER_DIR="$HOME/.claude-pace-maker"
CONFIG_FILE="$PACEMAKER_DIR/config.json"

# Read stdin once
INPUT=$(cat)

# Check if pace maker is enabled
if [ ! -f "$CONFIG_FILE" ]; then
    exit 0
fi

ENABLED=$(jq -r '.enabled // true' "$CONFIG_FILE" 2>/dev/null || echo "true")
if [ "$ENABLED" != "true" ]; then
    echo "$INPUT"
    exit 0
fi

# Use Python hook module to handle user prompt
INSTALL_MARKER="$PACEMAKER_DIR/install_source"

if [ -f "$INSTALL_MARKER" ]; then
    # Use install source directory
    SOURCE_DIR=$(cat "$INSTALL_MARKER")
    echo "$INPUT" | PYTHONPATH="$SOURCE_DIR/src:$PYTHONPATH" python3 -m pacemaker.hook user_prompt_submit 2>> "$PACEMAKER_DIR/hook_debug.log"
elif command -v python3 -c "import pacemaker" 2>/dev/null; then
    # Installed mode - use installed package
    echo "$INPUT" | python3 -m pacemaker.hook user_prompt_submit 2>> "$PACEMAKER_DIR/hook_debug.log"
fi
