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

# Register hooks in settings.json
register_hooks() {
  echo "Registering hooks..."

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

  # Update settings.json with hook registrations using merge logic
  # Use a temp file to avoid issues with jq in-place editing
  TEMP_FILE=$(mktemp)
  trap 'rm -f "$TEMP_FILE"' EXIT ERR

  HOOK_DIR="$HOME/.claude/hooks"

  jq --arg stop_hook "$HOOK_DIR/stop.sh" \
     --arg post_hook "$HOOK_DIR/post-tool-use.sh" \
     --arg prompt_hook "$HOOK_DIR/user-prompt-submit.sh" \
     '
     .hooks.Stop = (
       (.hooks.Stop // []) |
       if type != "array" then [] else . end |
       if ([.[] | .hooks[]? | select(.command == $stop_hook)] | length > 0) then .
       else . + [{"hooks": [{"type": "command", "command": $stop_hook}]}]
       end
     ) |
     .hooks.PostToolUse = (
       (.hooks.PostToolUse // []) |
       if type != "array" then [] else . end |
       if ([.[] | .hooks[]? | select(.command == $post_hook)] | length > 0) then .
       else . + [{"hooks": [{"type": "command", "command": $post_hook, "timeout": 360}]}]
       end
     ) |
     .hooks.UserPromptSubmit = (
       (.hooks.UserPromptSubmit // []) |
       if type != "array" then [] else . end |
       if ([.[] | .hooks[]? | select(.command == $prompt_hook)] | length > 0) then .
       else . + [{"hooks": [{"type": "command", "command": $prompt_hook}]}]
       end
     )
     ' "$SETTINGS_FILE" > "$TEMP_FILE"

  if [ $? -ne 0 ]; then
    echo -e "${RED}✗ Failed to register hooks - jq command failed${NC}"
    return 1
  fi

  # Validate the result
  if ! jq -e '.hooks' "$TEMP_FILE" > /dev/null 2>&1; then
    echo -e "${RED}✗ Failed to register hooks - invalid JSON structure${NC}"
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
  [ -f "$PACEMAKER_DIR/config.json" ] || { echo -e "${RED}✗ config.json missing${NC}"; ((errors++)); }
  [ -f "$PACEMAKER_DIR/usage.db" ] || { echo -e "${RED}✗ usage.db missing${NC}"; ((errors++)); }

  # Check permissions
  [ -x "$HOOKS_DIR/stop.sh" ] || { echo -e "${RED}✗ stop.sh not executable${NC}"; ((errors++)); }
  [ -x "$HOOKS_DIR/post-tool-use.sh" ] || { echo -e "${RED}✗ post-tool-use.sh not executable${NC}"; ((errors++)); }
  [ -x "$HOOKS_DIR/user-prompt-submit.sh" ] || { echo -e "${RED}✗ user-prompt-submit.sh not executable${NC}"; ((errors++)); }

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
