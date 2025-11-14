#!/bin/bash
#
# Claude Pace Maker - Installation Script
# Sets up global installation in user's home directory
#

set -e

CLAUDE_DIR="$HOME/.claude"
HOOKS_DIR="$CLAUDE_DIR/hooks"
PACEMAKER_DIR="$HOME/.claude-pace-maker"
SETTINGS_FILE="$CLAUDE_DIR/settings.json"

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo "Claude Pace Maker - Installation"
echo "================================"
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
  echo -e "${GREEN}✓ Directories created${NC}"
}

# Install hook scripts
install_hooks() {
  echo "Installing hook scripts..."

  # Copy hooks from source
  cp "$SCRIPT_DIR/src/hooks/post-tool-use.sh" "$HOOKS_DIR/"
  cp "$SCRIPT_DIR/src/hooks/stop.sh" "$HOOKS_DIR/"
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
  "threshold_percent": 10,
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

  # Create settings file if doesn't exist
  if [ ! -f "$SETTINGS_FILE" ]; then
    echo "{}" > "$SETTINGS_FILE"
  fi

  # Update settings.json with hook registrations using the new array-based format
  # Use a temp file to avoid issues with jq in-place editing
  TEMP_FILE=$(mktemp)
  jq '.hooks.PostToolUse = [{"hooks": [{"type": "command", "command": "~/.claude/hooks/post-tool-use.sh"}]}] |
      .hooks.Stop = [{"hooks": [{"type": "command", "command": "~/.claude/hooks/stop.sh"}]}] |
      .hooks.UserPromptSubmit = [{"hooks": [{"type": "command", "command": "~/.claude/hooks/user-prompt-submit.sh"}]}]' \
      "$SETTINGS_FILE" > "$TEMP_FILE"

  mv "$TEMP_FILE" "$SETTINGS_FILE"

  echo -e "${GREEN}✓ Hooks registered${NC}"
}

# Verify installation
verify_installation() {
  echo ""
  echo "Verifying installation..."

  local errors=0

  # Check files exist
  [ -f "$HOOKS_DIR/post-tool-use.sh" ] || { echo -e "${RED}✗ post-tool-use.sh missing${NC}"; ((errors++)); }
  [ -f "$HOOKS_DIR/stop.sh" ] || { echo -e "${RED}✗ stop.sh missing${NC}"; ((errors++)); }
  [ -f "$HOOKS_DIR/user-prompt-submit.sh" ] || { echo -e "${RED}✗ user-prompt-submit.sh missing${NC}"; ((errors++)); }
  [ -f "$PACEMAKER_DIR/config.json" ] || { echo -e "${RED}✗ config.json missing${NC}"; ((errors++)); }
  [ -f "$PACEMAKER_DIR/usage.db" ] || { echo -e "${RED}✗ usage.db missing${NC}"; ((errors++)); }

  # Check permissions
  [ -x "$HOOKS_DIR/post-tool-use.sh" ] || { echo -e "${RED}✗ post-tool-use.sh not executable${NC}"; ((errors++)); }
  [ -x "$HOOKS_DIR/stop.sh" ] || { echo -e "${RED}✗ stop.sh not executable${NC}"; ((errors++)); }
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
    echo "Claude Pace Maker is now active globally."
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
