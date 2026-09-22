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

ENABLED=$(jq -r 'if has("enabled") then .enabled else true end' "$CONFIG_FILE" 2>/dev/null || echo "true")
if [ "$ENABLED" != "true" ]; then
    exit 0
fi

# Determine which Python to use and how to invoke pacemaker
INSTALL_MARKER="$PACEMAKER_DIR/install_source"

# Find best Python version (3.11+ for SDK support, fallback to 3.10+).
# Prefer an interpreter where claude_agent_sdk actually imports (issue #89:
# existence-only selection can silently pick a Python lacking the SDK,
# degrading Anthropic-SDK-based verifiers with no visible warning). If no
# candidate has the SDK (e.g. a plain non-SDK hook_model like codex), fall
# back to the original existence-only preference order.
find_python() {
    for py in python3.11 python3.10 python3; do
        if command -v "$py" >/dev/null 2>&1; then
            if "$py" -c "import claude_agent_sdk" >/dev/null 2>&1; then
                echo "$py"
                return 0
            fi
        fi
    done
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
            # Fallback to best available Python
            PYTHON_CMD=$(find_python)
        fi
    else
        # Installed snapshot - import pacemaker from the copy that lives
        # next to this script (deployed by ./install.sh's
        # install_hook_modules(), which mirrors $SOURCE_DIR/src/pacemaker
        # here as pacemaker/). This is what makes an explicit ./install.sh
        # run the ONLY action that makes code changes live (issue #146) --
        # a half-finished/uncommitted edit in $SOURCE_DIR/src no longer
        # crashes every hook on the machine.
        #
        # Opt-in escape hatch for live development: set
        # PACEMAKER_DEV_LIVE_SRC=1 to import directly from $SOURCE_DIR/src
        # instead. This is NOT the default.
        PYTHON_CMD=$(find_python)
        if [ "${PACEMAKER_DEV_LIVE_SRC:-}" = "1" ]; then
            export PYTHONPATH="$SOURCE_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
        else
            # Resolve this script's own real directory, following symlinks,
            # so this works whether the deployed script is a plain copy
            # (the normal case) or a symlink into the install location.
            HOOK_SELF_PATH="$(readlink -f "$0" 2>/dev/null || echo "$0")"
            HOOK_SCRIPT_DIR="$(cd "$(dirname "$HOOK_SELF_PATH")" && pwd)"

            # Fail-open guard: if the installed snapshot's pacemaker/
            # package is missing (install.sh never ran here, or a
            # stale/broken deploy), do NOT silently fall through to
            # whatever `pacemaker` a leftover editable install or a cwd
            # decoy resolves to -- that would quietly re-open exactly the
            # live-Dev-tree hazard this fix exists to close (review
            # follow-up on issue #146). Log loudly and skip this hook run,
            # exactly like the enabled-guard above.
            if [ ! -f "$HOOK_SCRIPT_DIR/pacemaker/__init__.py" ]; then
                {
                    echo "[$(date -Iseconds 2>/dev/null || date)] $(basename "$0"): installed pacemaker snapshot missing at $HOOK_SCRIPT_DIR/pacemaker/__init__.py -- run ./install.sh to deploy. Skipping this hook run (fail-open)."
                } >> "$PACEMAKER_DIR/hook_debug.log" 2>/dev/null
                exit 0
            fi

            export PYTHONPATH="$HOOK_SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"
        fi
    fi
else
    # No marker - find best Python
    PYTHON_CMD=$(find_python)
fi

# Prevent `python -m` from prepending the current working directory (or ''
# for -c) to sys.path AHEAD of PYTHONPATH (Python 3.11+; harmless no-op on
# 3.10, where the flag/env var doesn't exist). Without this, invoking a hook
# from a cwd that happens to contain its own pacemaker/ package -- e.g. this
# repo's own src/ directory -- silently shadows whatever PYTHONPATH was just
# built above, in ANY branch (review follow-up on issue #146).
export PYTHONSAFEPATH=1

# Determine hook type from script name
HOOK_TYPE="stop"

# Run the hook
$PYTHON_CMD -m pacemaker.hook $HOOK_TYPE 2>> "$PACEMAKER_DIR/hook_debug.log"
