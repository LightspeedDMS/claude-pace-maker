#!/bin/bash
#
# Claude Code Hook: post-tool-use
# Triggers after each tool use to capture usage snapshots
#

set -e

PACEMAKER_DIR="$HOME/.claude-pace-maker"
CONFIG_FILE="$PACEMAKER_DIR/config.json"

# Debug logging
echo "[DEBUG] post-tool-use hook called at $(date)" >> "$PACEMAKER_DIR/hook_debug.log"

# Check if pace maker is enabled
if [ ! -f "$CONFIG_FILE" ]; then
    exit 0
fi

ENABLED=$(jq -r '.enabled // true' "$CONFIG_FILE" 2>/dev/null || echo "true")
if [ "$ENABLED" != "true" ]; then
    exit 0
fi

# Use Python hook module to handle the snapshot
INSTALL_MARKER="$PACEMAKER_DIR/install_source"

if [ -f "$INSTALL_MARKER" ]; then
    # Use install source directory
    SOURCE_DIR=$(cat "$INSTALL_MARKER")
    echo "[DEBUG] Calling Python hook from $SOURCE_DIR" >> "$PACEMAKER_DIR/hook_debug.log"
    PYTHONPATH="$SOURCE_DIR/src:$PYTHONPATH" python3 -m pacemaker.hook post_tool_use 2>> "$PACEMAKER_DIR/hook_debug.log"
    echo "[DEBUG] Python hook returned: $?" >> "$PACEMAKER_DIR/hook_debug.log"
elif command -v python3 -c "import pacemaker" 2>/dev/null; then
    # Installed mode - use installed package
    echo "[DEBUG] Using installed package" >> "$PACEMAKER_DIR/hook_debug.log"
    python3 -m pacemaker.hook post_tool_use 2>> "$PACEMAKER_DIR/hook_debug.log"
fi
