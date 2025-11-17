#!/bin/bash
#
# Claude Pace Maker - Installation Script
# Supports both global and local project installation
#
# Usage:
#   ./install.sh              Install globally in ~/.claude/settings.json
#   ./install.sh PROJECT_DIR  Install locally in PROJECT_DIR/.claude/settings.json
#   ./install.sh --help       Show this help message
#

set -e

# Color codes for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Parse command line arguments
show_help() {
  cat <<EOF
Usage: install.sh [PROJECT_DIR]

Install Claude Pace Maker hooks and configuration.

Arguments:
  PROJECT_DIR   Optional path to project directory for local installation.
                If omitted, installs globally in ~/.claude/

Installation Modes:
  Global:  Hooks registered in ~/.claude/settings.json
  Local:   Hooks registered in PROJECT_DIR/.claude/settings.json

Examples:
  install.sh                    # Global installation
  install.sh ~/my-project       # Local installation for specific project
  install.sh --help             # Show this help

Notes:
  - Hook scripts are always installed in ~/.claude/hooks/ (shared)
  - State directory is always ~/.claude-pace-maker/ (global)
  - Local mode merges with existing project settings if present
EOF
  exit 0
}

# Check for help flags
if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
  show_help
fi

# Determine installation mode
INSTALL_MODE="global"
PROJECT_DIR=""

if [ -n "$1" ]; then
  INSTALL_MODE="local"

  # Convert relative path to absolute
  if [[ "$1" = /* ]]; then
    PROJECT_DIR="$1"
  else
    PROJECT_DIR="$(cd "$1" 2>/dev/null && pwd || echo "")"
  fi

  # Validate project directory
  if [ -z "$PROJECT_DIR" ] || [ ! -d "$PROJECT_DIR" ]; then
    echo -e "${RED}Error: Directory does not exist: $1${NC}" >&2
    exit 1
  fi

  # Ensure it's a directory, not a file
  if [ -f "$PROJECT_DIR" ]; then
    echo -e "${RED}Error: Path is a file, not a directory: $PROJECT_DIR${NC}" >&2
    exit 1
  fi
fi

# Set paths based on installation mode
CLAUDE_DIR="$HOME/.claude"
HOOKS_DIR="$CLAUDE_DIR/hooks"
PACEMAKER_DIR="$HOME/.claude-pace-maker"

if [ "$INSTALL_MODE" = "local" ]; then
  SETTINGS_FILE="$PROJECT_DIR/.claude/settings.json"
  SETTINGS_DIR="$PROJECT_DIR/.claude"
else
  SETTINGS_FILE="$CLAUDE_DIR/settings.json"
  SETTINGS_DIR="$CLAUDE_DIR"
fi

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Claude Pace Maker - Installation"
echo "================================"
if [ "$INSTALL_MODE" = "local" ]; then
  echo "Mode: Local (Project: $PROJECT_DIR)"
else
  echo "Mode: Global"
fi
echo ""

# Check dependencies
check_dependencies() {
  local missing=()

  command -v jq >/dev/null 2>&1 || missing+=("jq")
  command -v curl >/dev/null 2>&1 || missing+=("curl")
  command -v python3 >/dev/null 2>&1 || missing+=("python3")

  if [ ${#missing[@]} -ne 0 ]; then
    echo -e "${RED}Error: Missing required dependencies: ${missing[*]}${NC}"
    echo "Please install them and try again."
    exit 1
  fi
}

# Create directories
create_directories() {
  echo "Creating directories..."
  mkdir -p "$HOOKS_DIR"
  mkdir -p "$PACEMAKER_DIR"
  mkdir -p "$SETTINGS_DIR"
  echo -e "${GREEN}✓ Directories created${NC}"
}

# Install hook scripts
install_hooks() {
  echo "Installing hook scripts..."

  # Copy hooks from source
  cp "$SCRIPT_DIR/src/hooks/stop.sh" "$HOOKS_DIR/"
  cp "$SCRIPT_DIR/src/hooks/post-tool-use.sh" "$HOOKS_DIR/"
  cp "$SCRIPT_DIR/src/hooks/user-prompt-submit.sh" "$HOOKS_DIR/"
  cp "$SCRIPT_DIR/src/hooks/session-start.sh" "$HOOKS_DIR/"

  # Set executable permissions
  chmod +x "$HOOKS_DIR"/*.sh

  echo -e "${GREEN}✓ Hook scripts installed${NC}"
}

# Create default configuration
create_config() {
  echo "Creating configuration..."

  # Only create if doesn't exist (idempotency)
  if [ ! -f "$PACEMAKER_DIR/config.json" ]; then
    cat > "$PACEMAKER_DIR/config.json" <<'EOF'
{
  "enabled": true,
  "base_delay": 5,
  "max_delay": 120,
  "threshold_percent": 0,
  "poll_interval": 60
}
EOF
    echo -e "${GREEN}✓ Configuration created${NC}"
  else
    echo -e "${YELLOW}✓ Configuration already exists (preserved)${NC}"
  fi
}

# Initialize database
init_database() {
  echo "Initializing database..."

  # Create or update database schema (idempotent) using Python
  python3 - <<EOF
import sqlite3

conn = sqlite3.connect("$PACEMAKER_DIR/usage.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS usage_snapshots (
  timestamp INTEGER PRIMARY KEY,
  five_hour_util REAL,
  five_hour_resets_at TEXT,
  seven_day_util REAL,
  seven_day_resets_at TEXT,
  session_id TEXT
)
""")

cursor.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON usage_snapshots(timestamp)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_session ON usage_snapshots(session_id)")

conn.commit()
conn.close()
EOF

  echo -e "${GREEN}✓ Database initialized${NC}"
}

# Check for hook conflicts between global and project settings
check_hook_conflicts() {
  local opposite_file=""
  local has_pacemaker_hooks=0

  # Determine which settings file to check based on current mode
  if [ "$INSTALL_MODE" = "local" ]; then
    # Installing locally - check global settings
    opposite_file="$HOME/.claude/settings.json"

    # If opposite file doesn't exist, no conflict
    [ ! -f "$opposite_file" ] && return 1

    # Check if global settings has pace-maker hooks
    has_pacemaker_hooks=$(jq -r '
      [
        ((.hooks.SessionStart // [])[] | select(.hooks[0].command | contains("session-start.sh"))),
        ((.hooks.UserPromptSubmit // [])[] | select(.hooks[0].command | contains("user-prompt-submit.sh"))),
        ((.hooks.PostToolUse // [])[] | select(.hooks[0].command | contains("post-tool-use.sh"))),
        ((.hooks.Stop // [])[] | select(.hooks[0].command | contains("stop.sh")))
      ] | length
    ' "$opposite_file" 2>/dev/null || echo "0")

    if [ "$has_pacemaker_hooks" -gt 0 ]; then
      echo "$opposite_file"
      return 0
    fi
  else
    # Installing globally - check current working directory for local settings
    # This handles the case where user runs global install from within a project
    local cwd_settings="$PWD/.claude/settings.json"
    local global_settings="$HOME/.claude/settings.json"

    # Only check if CWD settings is different from the global settings we're installing to
    # This avoids false positives when reinstalling globally
    if [ -f "$cwd_settings" ] && [ "$(readlink -f "$cwd_settings")" != "$(readlink -f "$global_settings")" ]; then
      has_pacemaker_hooks=$(jq -r '
        [
          ((.hooks.SessionStart // [])[] | select(.hooks[0].command | contains("session-start.sh"))),
          ((.hooks.UserPromptSubmit // [])[] | select(.hooks[0].command | contains("user-prompt-submit.sh"))),
          ((.hooks.PostToolUse // [])[] | select(.hooks[0].command | contains("post-tool-use.sh"))),
          ((.hooks.Stop // [])[] | select(.hooks[0].command | contains("stop.sh")))
        ] | length
      ' "$cwd_settings" 2>/dev/null || echo "0")

      if [ "$has_pacemaker_hooks" -gt 0 ]; then
        echo "$cwd_settings"
        return 0
      fi
    fi

    # Also check common project parent directories if we can find them
    # Look for any .claude/settings.json files with pace-maker hooks in subdirectories
    # But this is expensive, so we skip it for now
    # The main protection is when installing locally (which is most common)
  fi

  # No conflict
  return 1
}

# Display conflict warning and get user confirmation
warn_about_conflict() {
  local conflicting_file="$1"

  echo ""
  echo -e "${YELLOW}⚠ WARNING: Hook Conflict Detected${NC}"
  echo ""
  echo "Claude Code merges settings from both global and project-local files."
  echo "Pace-maker hooks found in: $conflicting_file"
  echo ""
  echo "If you continue, hooks will be registered in both locations and will"
  echo "FIRE TWICE on each trigger, causing duplicate behavior."
  echo ""
  echo -e "${YELLOW}Recommendation:${NC}"
  echo "  - For project-specific installation: Remove hooks from ~/.claude/settings.json"
  echo "  - For global installation: Remove hooks from project .claude/settings.json"
  echo ""
  echo -n "Do you want to proceed anyway? [y/N]: "

  # Read user input with timeout for non-interactive environments
  local answer
  if read -r -t 60 answer 2>/dev/null; then
    case "$answer" in
      [Yy]|[Yy][Ee][Ss])
        echo ""
        echo -e "${YELLOW}Proceeding with installation despite conflict...${NC}"
        echo ""
        return 0
        ;;
      *)
        echo ""
        echo -e "${RED}Installation cancelled by user.${NC}"
        echo "Please remove pace-maker hooks from one location and try again."
        return 1
        ;;
    esac
  else
    # Non-interactive or timeout - default to cancelling
    echo ""
    echo -e "${RED}No response received. Installation cancelled.${NC}"
    return 1
  fi
}

# Register hooks in settings.json
register_hooks() {
  echo "Registering hooks..."

  # Check for conflicts BEFORE making any changes
  local conflicting_file
  if conflicting_file=$(check_hook_conflicts); then
    if ! warn_about_conflict "$conflicting_file"; then
      # User cancelled or non-interactive mode
      exit 1
    fi
  fi

  # Full paths for hooks
  HOOKS_DIR="$HOME/.claude/hooks"
  SESSION_START_HOOK="$HOOKS_DIR/session-start.sh"
  USER_PROMPT_HOOK="$HOOKS_DIR/user-prompt-submit.sh"
  POST_HOOK="$HOOKS_DIR/post-tool-use.sh"
  STOP_HOOK="$HOOKS_DIR/stop.sh"

  # Handle settings file - backup if has content, initialize if empty or missing
  if [ -f "$SETTINGS_FILE" ]; then
    if [ -s "$SETTINGS_FILE" ]; then
      # File has content - back it up with timestamp
      BACKUP_FILE="$SETTINGS_FILE.backup.$(date +%Y%m%d_%H%M%S)"
      cp "$SETTINGS_FILE" "$BACKUP_FILE"
      echo -e "${YELLOW}Created backup: $BACKUP_FILE${NC}"
    else
      # File exists but is empty - initialize it
      echo "{}" > "$SETTINGS_FILE"
    fi
  else
    # File doesn't exist - create it
    echo "{}" > "$SETTINGS_FILE"
  fi

  TEMP_FILE=$(mktemp)

  # Remove ALL pace-maker hooks, then add them back
  # This ensures no duplicates and preserves other hooks like tdd-guard
  jq --arg session_start "$SESSION_START_HOOK" \
     --arg user_prompt "$USER_PROMPT_HOOK" \
     --arg post_hook "$POST_HOOK" \
     --arg stop_hook "$STOP_HOOK" \
    '
     # Helper function to check if a hook entry contains pace-maker hooks
     def has_pacemaker_hook(pattern):
       [.hooks[]? | select(.command? // "" | test(pattern))] | length > 0;

     # Remove all pace-maker hooks (handles both ~ and full paths)
     # This preserves non-pace-maker hooks by checking if ANY command in the hooks array matches
     .hooks.SessionStart = [(.hooks.SessionStart // [])[] |
       select(has_pacemaker_hook("\\.claude/hooks/session-start\\.sh") | not)] |
     .hooks.UserPromptSubmit = [(.hooks.UserPromptSubmit // [])[] |
       select(has_pacemaker_hook("\\.claude/hooks/user-prompt-submit\\.sh") | not)] |
     .hooks.PostToolUse = [(.hooks.PostToolUse // [])[] |
       select(has_pacemaker_hook("\\.claude/hooks/post-tool-use\\.sh") | not)] |
     .hooks.Stop = [(.hooks.Stop // [])[] |
       select(has_pacemaker_hook("\\.claude/hooks/stop\\.sh") | not)] |

     # Add them back with full paths
     .hooks.SessionStart += [{"hooks": [{"type": "command", "command": $session_start}]}] |
     .hooks.UserPromptSubmit += [{"hooks": [{"type": "command", "command": $user_prompt}]}] |
     .hooks.PostToolUse += [{"hooks": [{"type": "command", "command": $post_hook, "timeout": 360}]}] |
     .hooks.Stop += [{"hooks": [{"type": "command", "command": $stop_hook}]}]
    ' "$SETTINGS_FILE" > "$TEMP_FILE"

  if [ $? -ne 0 ]; then
    echo -e "${RED}✗ Failed to register hooks - jq command failed${NC}"
    rm -f "$TEMP_FILE"
    return 1
  fi

  # Validate the result
  if ! jq -e '.hooks' "$TEMP_FILE" > /dev/null 2>&1; then
    echo -e "${RED}✗ Failed to register hooks - invalid JSON structure${NC}"
    rm -f "$TEMP_FILE"
    return 1
  fi

  mv "$TEMP_FILE" "$SETTINGS_FILE"
  echo -e "${GREEN}✓ Hooks registered${NC}"
}

# Verify installation
verify_installation() {
  echo ""
  echo "Verifying installation..."

  local errors=0

  # Check files exist
  [ -f "$HOOKS_DIR/stop.sh" ] || { echo -e "${RED}✗ stop.sh missing${NC}"; ((errors++)); }
  [ -f "$HOOKS_DIR/post-tool-use.sh" ] || { echo -e "${RED}✗ post-tool-use.sh missing${NC}"; ((errors++)); }
  [ -f "$HOOKS_DIR/user-prompt-submit.sh" ] || { echo -e "${RED}✗ user-prompt-submit.sh missing${NC}"; ((errors++)); }
  [ -f "$HOOKS_DIR/session-start.sh" ] || { echo -e "${RED}✗ session-start.sh missing${NC}"; ((errors++)); }
  [ -f "$PACEMAKER_DIR/config.json" ] || { echo -e "${RED}✗ config.json missing${NC}"; ((errors++)); }
  [ -f "$PACEMAKER_DIR/usage.db" ] || { echo -e "${RED}✗ usage.db missing${NC}"; ((errors++)); }

  # Check permissions
  [ -x "$HOOKS_DIR/stop.sh" ] || { echo -e "${RED}✗ stop.sh not executable${NC}"; ((errors++)); }
  [ -x "$HOOKS_DIR/post-tool-use.sh" ] || { echo -e "${RED}✗ post-tool-use.sh not executable${NC}"; ((errors++)); }
  [ -x "$HOOKS_DIR/user-prompt-submit.sh" ] || { echo -e "${RED}✗ user-prompt-submit.sh not executable${NC}"; ((errors++)); }
  [ -x "$HOOKS_DIR/session-start.sh" ] || { echo -e "${RED}✗ session-start.sh not executable${NC}"; ((errors++)); }

  # Check database schema using Python
  local table_exists=$(python3 - <<EOF
import sqlite3
try:
    conn = sqlite3.connect("$PACEMAKER_DIR/usage.db")
    cursor = conn.cursor()
    cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='usage_snapshots'")
    count = cursor.fetchone()[0]
    conn.close()
    print(count)
except Exception:
    print(0)
EOF
)
  if [ "$table_exists" != "1" ]; then
    echo -e "${RED}✗ Database schema not initialized${NC}"
    ((errors++))
  fi

  # Check settings.json for new hook format
  if ! jq -e '.hooks.PostToolUse' "$SETTINGS_FILE" >/dev/null 2>&1; then
    echo -e "${RED}✗ Hooks not registered in settings.json${NC}"
    ((errors++))
  fi

  if [ $errors -eq 0 ]; then
    echo -e "${GREEN}✓ All checks passed${NC}"
    return 0
  else
    echo -e "${RED}✗ Installation verification failed with $errors error(s)${NC}"
    return 1
  fi
}

# Main installation flow
main() {
  check_dependencies
  create_directories
  install_hooks
  create_config
  init_database
  register_hooks

  if verify_installation; then
    echo ""
    echo -e "${GREEN}Installation completed successfully!${NC}"
    echo ""
    if [ "$INSTALL_MODE" = "local" ]; then
      echo "Claude Pace Maker is now active for project: $PROJECT_DIR"
      echo "Settings: $SETTINGS_FILE"
    else
      echo "Claude Pace Maker is now active globally."
      echo "Settings: $SETTINGS_FILE"
    fi
    echo "Configuration: $PACEMAKER_DIR/config.json"
    echo "Database: $PACEMAKER_DIR/usage.db"
    echo ""
    echo "The pace maker will automatically monitor your usage and"
    echo "introduce delays when approaching rate limits."
  else
    echo ""
    echo -e "${RED}Installation completed with errors.${NC}"
    echo "Please review the error messages above and try again."
    exit 1
  fi
}

main "$@"
