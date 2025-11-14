#!/bin/bash
#
# Claude Code Hook: stop
# Triggers when Claude Code session stops
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

# Use Python hook module to handle session end
INSTALL_MARKER="$PACEMAKER_DIR/install_source"

if [ -f "$INSTALL_MARKER" ]; then
    # Use install source directory
    SOURCE_DIR=$(cat "$INSTALL_MARKER")
    PYTHONPATH="$SOURCE_DIR/src:$PYTHONPATH" python3 -m pacemaker.hook stop
elif command -v python3 -c "import pacemaker" 2>/dev/null; then
    # Installed mode - use installed package
    python3 -m pacemaker.hook stop
fi
