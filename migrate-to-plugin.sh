#!/bin/bash
# Claude Pace Maker - Migrate Legacy Installation to Plugin Mode
#
# Removes legacy hook registrations from ~/.claude/settings.json and
# removes legacy hook scripts from ~/.claude/hooks/.
#
# Safe to run multiple times (idempotent).
# Preserves all non-pace-maker hooks intact.

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

CLAUDE_DIR="$HOME/.claude"
HOOKS_DIR="$CLAUDE_DIR/hooks"
SETTINGS_FILE="$CLAUDE_DIR/settings.json"

echo "Claude Pace Maker - Migration to Plugin Mode"
echo "============================================="
echo ""

# ---------------------------------------------------------------------------
# Step 1: Remove legacy hook entries from settings.json
# ---------------------------------------------------------------------------
if [ -f "$SETTINGS_FILE" ]; then
    echo "Removing pace-maker hooks from $SETTINGS_FILE..."

    TEMP_FILE="$CLAUDE_DIR/.settings.migrate.tmp.$$"

    # Remove hook entries whose command contains the legacy hook script paths.
    # Legacy hook commands contain "/.claude/hooks/" followed by a pace-maker script name.
    # Strategy: for each hook array, filter out entries where ANY sub-hook command
    # matches the legacy pattern. Preserve all other entries.
    jq '
      def is_pacemaker_cmd:
        test("\\.claude/hooks/(pre-tool-use|post-tool-use|stop|user-prompt-submit|session-start|subagent-start|subagent-stop)\\.sh")
        or test("pacemaker\\.hook")
        or test("/hooks/pacemaker/");

      def remove_pacemaker_hooks:
        if type == "array" then
          [
            .[] |
            if .hooks then
              .hooks = [.hooks[] | select(.command? | (is_pacemaker_cmd | not))]
            else
              .
            end |
            select(.hooks and (.hooks | length) > 0)
          ]
        else
          .
        end;

      if .hooks then
        .hooks |= with_entries(.value |= remove_pacemaker_hooks)
        | .hooks |= with_entries(select(.value | length > 0))
      else
        .
      end
    ' "$SETTINGS_FILE" > "$TEMP_FILE"

    if [ $? -eq 0 ] && jq -e '.' "$TEMP_FILE" >/dev/null 2>&1; then
        mv "$TEMP_FILE" "$SETTINGS_FILE"
        echo -e "${GREEN}✓ pace-maker hooks removed from settings.json${NC}"
    else
        rm -f "$TEMP_FILE"
        echo -e "${RED}✗ Failed to process settings.json${NC}" >&2
        exit 1
    fi
else
    echo -e "${YELLOW}No settings.json found at $SETTINGS_FILE, skipping${NC}"
fi

# ---------------------------------------------------------------------------
# Step 2: Remove legacy hook scripts from ~/.claude/hooks/
# ---------------------------------------------------------------------------
LEGACY_SCRIPTS=(
    "pre-tool-use.sh"
    "post-tool-use.sh"
    "stop.sh"
    "user-prompt-submit.sh"
    "session-start.sh"
    "subagent-start.sh"
    "subagent-stop.sh"
)

if [ -d "$HOOKS_DIR" ]; then
    echo "Removing legacy hook scripts from $HOOKS_DIR..."
    for script in "${LEGACY_SCRIPTS[@]}"; do
        script_path="$HOOKS_DIR/$script"
        if [ -f "$script_path" ]; then
            rm -f "$script_path"
            echo "  Removed $script"
        fi
    done
    echo -e "${GREEN}✓ Legacy hook scripts removed${NC}"
else
    echo -e "${YELLOW}No hooks directory found at $HOOKS_DIR, skipping${NC}"
fi

# ---------------------------------------------------------------------------
# Step 2b: Remove the user-scope declare_intent MCP registration (Story #155)
#
# install.sh registered it at user scope; in plugin mode the plugin manifest
# declares the same server itself, so the legacy registration would only
# duplicate it. Must run BEFORE step 3: the helper lives in the installed
# snapshot that step 3 deletes. Non-fatal -- a failure only leaves a stale
# (harmless) registration behind.
# ---------------------------------------------------------------------------
PACEMAKER_HOOKS_DIR="$HOOKS_DIR/pacemaker"
if [ -f "$PACEMAKER_HOOKS_DIR/intent_mcp/registration.py" ] && command -v claude >/dev/null 2>&1; then
    echo "Removing the user-scope declare_intent MCP registration..."
    MIGRATE_PYTHON="$(command -v python3.11 || command -v python3.10 || command -v python3 || true)"
    if [ -n "$MIGRATE_PYTHON" ] && PYTHONPATH="$HOOKS_DIR" PYTHONSAFEPATH=1 "$MIGRATE_PYTHON" \
            -m pacemaker.intent_mcp.registration remove; then
        echo -e "${GREEN}✓ declare_intent MCP registration removed${NC}"
    else
        echo -e "${YELLOW}⚠ Could not remove the declare_intent MCP registration; run: claude mcp remove --scope user pace-maker${NC}"
    fi
else
    echo -e "${YELLOW}No installed snapshot or claude CLI found, skipping MCP registration removal${NC}"
fi

# The permission install.sh added for that tool (Story #155 review M3) goes
# with it. Same ordering constraint (helper lives in the snapshot) and the same
# non-fatal policy. NOTE: a plugin cannot ship this permission itself -- users
# of the plugin add mcp__plugin_claude-pace-maker_pace-maker__declare_intent to
# their own permissions.allow (see CLAUDE.md, "Story #155").
if [ -f "$PACEMAKER_HOOKS_DIR/intent_mcp/permissions.py" ] && [ -f "$SETTINGS_FILE" ]; then
    MIGRATE_PYTHON="${MIGRATE_PYTHON:-$(command -v python3.11 || command -v python3.10 || command -v python3 || true)}"
    if [ -n "$MIGRATE_PYTHON" ] && PYTHONPATH="$HOOKS_DIR" PYTHONSAFEPATH=1 "$MIGRATE_PYTHON" \
            -m pacemaker.intent_mcp.permissions remove --settings-file "$SETTINGS_FILE"; then
        echo -e "${GREEN}✓ declare_intent permission removed from settings.json${NC}"
    else
        echo -e "${YELLOW}⚠ Could not remove the declare_intent permission from $SETTINGS_FILE; remove \"mcp__pace-maker__declare_intent\" from permissions.allow yourself${NC}"
    fi
fi

# ---------------------------------------------------------------------------
# Step 3: Remove ~/.claude/hooks/pacemaker/ directory
# ---------------------------------------------------------------------------
if [ -d "$PACEMAKER_HOOKS_DIR" ]; then
    echo "Removing $PACEMAKER_HOOKS_DIR..."
    rm -rf "$PACEMAKER_HOOKS_DIR"
    echo -e "${GREEN}✓ pacemaker directory removed${NC}"
else
    echo -e "${YELLOW}No pacemaker directory found at $PACEMAKER_HOOKS_DIR, skipping${NC}"
fi

echo ""
echo -e "${GREEN}Migration complete.${NC}"
echo ""
echo "pace-maker is now configured for plugin mode."
echo "Legacy hooks have been removed from settings.json and ~/.claude/hooks/."
echo ""
echo "If pace-maker is installed as a Claude Code plugin, hooks will fire"
echo "automatically via the plugin system."
