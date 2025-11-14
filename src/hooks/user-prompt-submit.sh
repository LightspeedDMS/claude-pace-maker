#!/bin/bash
#
# Claude Code Hook: user-prompt-submit
# Triggers when user submits a prompt
#

set -e

PACEMAKER_DIR="$HOME/.claude-pace-maker"
CONFIG_FILE="$PACEMAKER_DIR/config.json"

# Debug logging
echo "[DEBUG] user-prompt-submit hook called at $(date)" >> "$PACEMAKER_DIR/hook_debug.log"

# Read stdin once
INPUT=$(cat)
echo "[DEBUG] Input received: $INPUT" >> "$PACEMAKER_DIR/hook_debug.log"

# Check if pace maker is enabled
if [ ! -f "$CONFIG_FILE" ]; then
    echo "[DEBUG] Config file not found" >> "$PACEMAKER_DIR/hook_debug.log"
    exit 0
fi

ENABLED=$(jq -r '.enabled // true' "$CONFIG_FILE" 2>/dev/null || echo "true")
echo "[DEBUG] Enabled: $ENABLED" >> "$PACEMAKER_DIR/hook_debug.log"
if [ "$ENABLED" != "true" ]; then
    echo "$INPUT"
    exit 0
fi

# Use Python hook module to handle user prompt
INSTALL_MARKER="$PACEMAKER_DIR/install_source"

if [ -f "$INSTALL_MARKER" ]; then
    # Use install source directory
    SOURCE_DIR=$(cat "$INSTALL_MARKER")
    echo "[DEBUG] Using source dir: $SOURCE_DIR" >> "$PACEMAKER_DIR/hook_debug.log"
    echo "[DEBUG] About to call Python" >> "$PACEMAKER_DIR/hook_debug.log"
    echo "$INPUT" | PYTHONPATH="$SOURCE_DIR/src:$PYTHONPATH" python3 -m pacemaker.hook user_prompt_submit 2>> "$PACEMAKER_DIR/hook_debug.log"
    echo "[DEBUG] Python returned with exit code: $?" >> "$PACEMAKER_DIR/hook_debug.log"
elif command -v python3 -c "import pacemaker" 2>/dev/null; then
    # Installed mode - use installed package
    echo "[DEBUG] Using installed package" >> "$PACEMAKER_DIR/hook_debug.log"
    echo "$INPUT" | python3 -m pacemaker.hook user_prompt_submit 2>> "$PACEMAKER_DIR/hook_debug.log"
fi
