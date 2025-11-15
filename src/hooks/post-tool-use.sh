#!/bin/bash
#
# Claude Code Hook: post-tool-use
# Triggers after each tool use to capture usage snapshots
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

# Use Python hook module to handle the snapshot and pacing
INSTALL_MARKER="$PACEMAKER_DIR/install_source"

if [ -f "$INSTALL_MARKER" ]; then
    # Use install source directory
    SOURCE_DIR=$(cat "$INSTALL_MARKER")
    # Run pacing (sleeps silently, no output)
    PYTHONPATH="$SOURCE_DIR/src:$PYTHONPATH" python3 -m pacemaker.hook post_tool_use 2>> "$PACEMAKER_DIR/hook_debug.log"
    # Get reminder message for steering
    REMINDER_MSG=$(PYTHONPATH="$SOURCE_DIR/src:$PYTHONPATH" python3 -c "from pacemaker.hooks.post_tool import run_post_tool_hook; print(run_post_tool_hook())")
elif command -v python3 -c "import pacemaker" 2>/dev/null; then
    # Installed mode - use installed package
    # Run pacing (sleeps silently, no output)
    python3 -m pacemaker.hook post_tool_use 2>> "$PACEMAKER_DIR/hook_debug.log"
    # Get reminder message for steering
    REMINDER_MSG=$(python3 -c "from pacemaker.hooks.post_tool import run_post_tool_hook; print(run_post_tool_hook())")
fi

# Only return the reminder message (steering), not pacing messages
if [ -n "$REMINDER_MSG" ]; then
    # Escape for JSON and output
    ESCAPED_MSG=$(echo "$REMINDER_MSG" | sed 's/"/\\"/g' | sed ':a;N;$!ba;s/\n/\\n/g')
else
    ESCAPED_MSG=""
fi

# Always return valid JSON
cat <<EOF
{
  "decision": "allow",
  "reason": "$ESCAPED_MSG"
}
EOF
