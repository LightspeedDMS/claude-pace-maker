#!/bin/bash
#
# Claude Pace Maker - Plugin Hook Script
#
# Single entry point for all hook events.
# Usage: hook.sh <hook_type>
# Where hook_type is: pre_tool_use, post_tool_use, user_prompt_submit,
#                     session_start, stop, subagent_start, subagent_stop
#

set -e

HOOK_TYPE="${1:?Hook type argument required}"

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

PYTHON_CMD=$(find_python)

# Resolve plugin root (CLAUDE_PLUGIN_ROOT is set by Claude Code for plugins)
# Fall back to script's own location for direct testing
if [ -n "$CLAUDE_PLUGIN_ROOT" ]; then
    PLUGIN_ROOT="$CLAUDE_PLUGIN_ROOT"
else
    PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fi

# Set PYTHONPATH to include the plugin's source tree
export PYTHONPATH="$PLUGIN_ROOT/src:$PYTHONPATH"

# Run the hook
$PYTHON_CMD -m pacemaker.hook "$HOOK_TYPE" 2>> "$PACEMAKER_DIR/hook_debug.log"
