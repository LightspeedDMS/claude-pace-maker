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

# Check Python version and return version string if 3.10+, empty otherwise
check_python_version() {
  local python_cmd="$1"

  if ! command -v "$python_cmd" >/dev/null 2>&1; then
    return 1
  fi

  local version=$("$python_cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")' 2>/dev/null)

  if [ -z "$version" ]; then
    return 1
  fi

  local major=$(echo "$version" | cut -d. -f1)
  local minor=$(echo "$version" | cut -d. -f2)

  if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
    echo "$version"
    return 0
  fi

  return 1
}

# Find best available Python 3.10+ command
find_python_command() {
  # Try python3.11, python3.10, python3 in order
  for cmd in python3.11 python3.10 python3; do
    if version=$(check_python_version "$cmd"); then
      echo "$cmd"
      return 0
    fi
  done
  return 1
}

# Upgrade Python to 3.11 if needed
upgrade_python() {
  local pkg_manager="$1"

  echo "Checking if Python upgrade is needed..."

  # Show current Python version
  if command -v python3 >/dev/null 2>&1; then
    local current_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")' 2>/dev/null || echo "unknown")
    echo "Current Python version: $current_version"
  else
    echo "Current Python version: not found"
  fi

  echo "Target Python version: 3.11"
  echo "Using package manager: $pkg_manager"
  echo "Upgrading Python to 3.11..."

  case "$pkg_manager" in
    dnf)
      # Rocky Linux/RHEL/Fedora
      if sudo dnf install -y python3.11 python3.11-pip >/dev/null 2>&1; then
        echo -e "${GREEN}✓ Python 3.11 installed${NC}"
        return 0
      else
        echo -e "${RED}✗ Failed to install Python 3.11${NC}"
        return 1
      fi
      ;;
    yum)
      # Older RHEL/CentOS
      if sudo yum install -y python3.11 python3.11-pip >/dev/null 2>&1; then
        echo -e "${GREEN}✓ Python 3.11 installed${NC}"
        return 0
      else
        echo -e "${RED}✗ Failed to install Python 3.11${NC}"
        return 1
      fi
      ;;
    apt)
      # Ubuntu/Debian
      if sudo apt-get update >/dev/null 2>&1 && sudo apt-get install -y python3.11 python3.11-pip python3.11-venv >/dev/null 2>&1; then
        echo -e "${GREEN}✓ Python 3.11 installed${NC}"
        return 0
      else
        echo -e "${RED}✗ Failed to install Python 3.11${NC}"
        return 1
      fi
      ;;
    brew)
      # macOS
      if brew install python@3.11 >/dev/null 2>&1; then
        echo -e "${GREEN}✓ Python 3.11 installed${NC}"
        return 0
      else
        echo -e "${RED}✗ Failed to install Python 3.11${NC}"
        return 1
      fi
      ;;
    *)
      echo -e "${RED}Error: Unsupported package manager for Python upgrade${NC}"
      return 1
      ;;
  esac
}

# Check and auto-install dependencies
check_dependencies() {
  local missing=()
  local pkg_manager=""
  local python_needs_upgrade=0

  # Check which dependencies are missing
  echo "Checking for jq..."
  if command -v jq >/dev/null 2>&1; then
    echo -e "${GREEN}✓ jq found${NC}"
  else
    echo -e "${YELLOW}⚠ jq not found${NC}"
    missing+=("jq")
  fi

  echo "Checking for curl..."
  if command -v curl >/dev/null 2>&1; then
    echo -e "${GREEN}✓ curl found${NC}"
  else
    echo -e "${YELLOW}⚠ curl not found${NC}"
    missing+=("curl")
  fi

  # Check if any python3 exists (we'll handle version separately)
  echo "Checking for python3..."
  if ! command -v python3 >/dev/null 2>&1; then
    echo -e "${YELLOW}⚠ python3 not found${NC}"
    missing+=("python3")
  else
    echo -e "${GREEN}✓ python3 found${NC}"
  fi

  # Detect available package manager first (needed for Python upgrade too)
  if command -v brew >/dev/null 2>&1; then
    pkg_manager="brew"
  elif command -v apt >/dev/null 2>&1; then
    pkg_manager="apt"
  elif command -v dnf >/dev/null 2>&1; then
    pkg_manager="dnf"
  elif command -v yum >/dev/null 2>&1; then
    pkg_manager="yum"
  else
    pkg_manager=""
  fi

  # Check Python version (independent of whether python3 is in missing array)
  echo "Checking Python version..."
  if python_cmd=$(find_python_command); then
    python_version=$(check_python_version "$python_cmd")
    echo -e "${GREEN}✓ Python $python_version found${NC}"
  else
    # Python exists but version is too old, or doesn't exist
    if command -v python3 >/dev/null 2>&1; then
      current_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")' 2>/dev/null || echo "unknown")
      echo -e "${YELLOW}⚠ Python $current_version found, but 3.10+ is required${NC}"
      python_needs_upgrade=1
    fi
  fi

  # If all dependencies are present and Python version is OK, return success
  if [ ${#missing[@]} -eq 0 ] && [ $python_needs_upgrade -eq 0 ]; then
    return 0
  fi

  # Need package manager for installation/upgrade
  if [ -z "$pkg_manager" ]; then
    echo -e "${RED}Error: No supported package manager found (brew, apt, dnf, yum)${NC}"
    if [ ${#missing[@]} -gt 0 ]; then
      echo -e "${RED}Missing dependencies: ${missing[*]}${NC}"
    fi
    if [ $python_needs_upgrade -eq 1 ]; then
      echo -e "${RED}Python upgrade needed: 3.10+ required${NC}"
    fi
    echo "Please install/upgrade manually and try again."
    return 1
  fi

  # Build prompt message
  local prompt_parts=()
  if [ ${#missing[@]} -gt 0 ]; then
    prompt_parts+=("missing dependencies: ${missing[*]}")
  fi
  if [ $python_needs_upgrade -eq 1 ]; then
    prompt_parts+=("Python upgrade to 3.11")
  fi

  # Prompt user for confirmation
  echo ""
  echo -e "${YELLOW}The following changes are needed:${NC}"
  for part in "${prompt_parts[@]}"; do
    echo "  - $part"
  done
  echo ""
  echo -n "Would you like to proceed using $pkg_manager? [Y/n]: "

  local answer
  # Use timeout for non-interactive environments (e.g., CI/CD)
  if [ -t 0 ]; then
    # Interactive mode - read user input
    read -r answer
  else
    # Non-interactive mode - default to yes
    answer="y"
    echo "y (non-interactive mode, defaulting to yes)"
  fi

  case "$answer" in
    [Nn]|[Nn][Oo])
      echo -e "${RED}Installation cancelled by user.${NC}"
      if [ ${#missing[@]} -gt 0 ]; then
        echo "Please install the following dependencies manually: ${missing[*]}"
      fi
      if [ $python_needs_upgrade -eq 1 ]; then
        echo "Please upgrade Python to 3.10+ manually."
      fi
      return 1
      ;;
    *)
      # Proceed with installation
      ;;
  esac

  # Install missing dependencies first
  local failed=()

  if [ ${#missing[@]} -gt 0 ]; then
    echo ""
    echo "Installing dependencies..."

    # Run apt-get update once before the loop to avoid race conditions
    if [ "$pkg_manager" = "apt" ]; then
      sudo apt-get update >/dev/null 2>&1
    fi

    for dep in "${missing[@]}"; do
      echo -n "Installing $dep... "

      case "$pkg_manager" in
        brew)
          if brew install "$dep" >/dev/null 2>&1; then
            echo -e "${GREEN}✓${NC}"
          else
            echo -e "${RED}✗${NC}"
            failed+=("$dep")
          fi
          ;;
        apt)
          if sudo apt-get install -y "$dep" >/dev/null 2>&1; then
            echo -e "${GREEN}✓${NC}"
          else
            echo -e "${RED}✗${NC}"
            failed+=("$dep")
          fi
          ;;
        dnf)
          if sudo dnf install -y "$dep" >/dev/null 2>&1; then
            echo -e "${GREEN}✓${NC}"
          else
            echo -e "${RED}✗${NC}"
            failed+=("$dep")
          fi
          ;;
        yum)
          if sudo yum install -y "$dep" >/dev/null 2>&1; then
            echo -e "${GREEN}✓${NC}"
          else
            echo -e "${RED}✗${NC}"
            failed+=("$dep")
          fi
          ;;
      esac
    done

    if [ ${#failed[@]} -ne 0 ]; then
      echo -e "${RED}Error: Failed to install: ${failed[*]}${NC}"
      echo "Please install them manually and try again."
      return 1
    fi

    echo -e "${GREEN}✓ All dependencies installed successfully${NC}"
  fi

  # Upgrade Python if needed
  if [ $python_needs_upgrade -eq 1 ]; then
    echo ""
    if ! upgrade_python "$pkg_manager"; then
      echo -e "${YELLOW}⚠ Warning: Python upgrade failed${NC}"
      echo -e "${YELLOW}  Some features may not work without Python 3.10+${NC}"
      echo -e "${YELLOW}  Continuing with existing Python version...${NC}"
      # Don't return 1 - allow installation to continue with warning
    fi
  fi

  return 0
}

# Install Python dependencies
install_python_deps() {
  echo ""
  echo "Installing Python dependencies..."

  # Find best Python command (3.10+)
  local python_cmd
  if python_cmd=$(find_python_command); then
    echo "Using $python_cmd for package installation"
  else
    # Fallback to python3 if no 3.10+ found
    echo -e "${YELLOW}⚠ Python 3.10+ not found, using python3${NC}"
    python_cmd="python3"
  fi

  # Check if packages are already installed (idempotency)
  echo "Checking Python package: requests..."
  local requests_installed=$("$python_cmd" -c "import requests" 2>/dev/null && echo "1" || echo "0")
  if [ "$requests_installed" = "1" ]; then
    local requests_version=$("$python_cmd" -c "import requests; print(requests.__version__)" 2>/dev/null || echo "unknown")
    echo -e "${GREEN}✓ requests already installed (version $requests_version)${NC}"
  else
    echo -e "${YELLOW}⚠ requests not found${NC}"
  fi

  echo "Checking Python package: claude-agent-sdk..."
  local sdk_installed=$("$python_cmd" -c "import claude_agent_sdk" 2>/dev/null && echo "1" || echo "0")
  if [ "$sdk_installed" = "1" ]; then
    local sdk_version=$("$python_cmd" -c "import claude_agent_sdk; print(claude_agent_sdk.__version__)" 2>/dev/null || echo "unknown")
    echo -e "${GREEN}✓ claude-agent-sdk already installed (version $sdk_version)${NC}"
  else
    echo -e "${YELLOW}⚠ claude-agent-sdk not found${NC}"
  fi

  local needs_install=0
  local packages=()

  if [ "$requests_installed" = "0" ]; then
    packages+=("requests")
    needs_install=1
  fi

  if [ "$sdk_installed" = "0" ]; then
    packages+=("claude-agent-sdk")
    needs_install=1
  fi

  if [ $needs_install -eq 0 ]; then
    echo -e "${GREEN}✓ All Python packages already installed${NC}"
    return 0
  fi

  # Install missing packages
  echo "Installing missing packages: ${packages[*]}"

  # Try with --user first, fall back to --break-system-packages on macOS
  if "$python_cmd" -m pip install --user "${packages[@]}" >/dev/null 2>&1; then
    echo -e "${GREEN}✓ Python dependencies installed${NC}"
  elif "$python_cmd" -m pip install --break-system-packages "${packages[@]}" >/dev/null 2>&1; then
    echo -e "${GREEN}✓ Python dependencies installed${NC}"
  else
    echo -e "${YELLOW}⚠ Warning: Could not install Python dependencies${NC}"
    echo -e "${YELLOW}  You may need to run: $python_cmd -m pip install --break-system-packages ${packages[*]}${NC}"
    echo -e "${YELLOW}  Note: Claude Agent SDK is required for validation features${NC}"
  fi
}

# Create directories
create_directories() {
  echo "Creating directories..."

  echo "Creating $HOOKS_DIR..."
  mkdir -p "$HOOKS_DIR"

  echo "Creating $PACEMAKER_DIR..."
  mkdir -p "$PACEMAKER_DIR"

  echo "Creating $SETTINGS_DIR..."
  mkdir -p "$SETTINGS_DIR"

  echo -e "${GREEN}✓ Directories created${NC}"
}

# Install hook scripts
install_hooks() {
  echo "Installing hook scripts..."

  # Detect hooks source directory (dev: src/hooks/, pipx: hooks/)
  if [ -d "$SCRIPT_DIR/src/hooks" ]; then
    HOOKS_SOURCE_DIR="$SCRIPT_DIR/src/hooks"
  elif [ -d "$SCRIPT_DIR/hooks" ]; then
    HOOKS_SOURCE_DIR="$SCRIPT_DIR/hooks"
  else
    echo -e "${RED}Error: Hook scripts not found${NC}"
    exit 1
  fi

  # Copy required hooks from source
  for hook in stop.sh post-tool-use.sh pre-tool-use.sh user-prompt-submit.sh session-start.sh subagent-start.sh subagent-stop.sh; do
    if [ -f "$HOOKS_SOURCE_DIR/$hook" ]; then
      echo "Installing $hook..."
      cp "$HOOKS_SOURCE_DIR/$hook" "$HOOKS_DIR/"
    else
      echo -e "${YELLOW}⚠ Warning: $hook not found, skipping${NC}"
    fi
  done

  # Session-start hook is now used again for resetting in_subagent flag
  # This prevents state corruption from cancelled subagents
  if [ -f "$HOOKS_DIR/session-start.sh.backup" ]; then
    echo "Note: session-start.sh is now active again..."
    rm -f "$HOOKS_DIR/session-start.sh.backup"
  fi

  # Set executable permissions
  echo "Setting executable permissions..."
  chmod +x "$HOOKS_DIR"/*.sh 2>/dev/null || true

  # Create install_source marker pointing to source directory
  echo "$SCRIPT_DIR" > "$PACEMAKER_DIR/install_source"

  echo -e "${GREEN}✓ Hook scripts installed${NC}"
}

# Install CLI command
install_cli() {
  echo "Installing pace-maker CLI..."

  # Ensure ~/.local/bin exists
  mkdir -p "$HOME/.local/bin"

  # Detect CLI source (dev: bin/pace-maker, pipx: pace-maker)
  if [ -f "$SCRIPT_DIR/bin/pace-maker" ]; then
    CLI_SOURCE="$SCRIPT_DIR/bin/pace-maker"
  elif [ -f "$SCRIPT_DIR/pace-maker" ]; then
    CLI_SOURCE="$SCRIPT_DIR/pace-maker"
  else
    echo -e "${YELLOW}⚠ Warning: CLI script not found, skipping${NC}"
    return 0
  fi

  # Copy CLI to ~/.local/bin
  echo "Installing pace-maker command to ~/.local/bin/pace-maker..."
  cp "$CLI_SOURCE" "$HOME/.local/bin/pace-maker"
  chmod +x "$HOME/.local/bin/pace-maker"

  # Copy Python modules to support CLI (dev mode only - pipx handles this)
  if [ -d "$SCRIPT_DIR/src/pacemaker" ]; then
    echo "Installing Python modules to $PACEMAKER_DIR/pacemaker..."
    mkdir -p "$PACEMAKER_DIR/pacemaker"
    cp -r "$SCRIPT_DIR/src/pacemaker"/* "$PACEMAKER_DIR/pacemaker/"

    # Update CLI wrapper to use installed modules
    cat > "$HOME/.local/bin/pace-maker" <<'EOF'
#!/usr/bin/env python3
"""
pace-maker CLI wrapper.

This script provides the user-facing CLI for Claude Pace Maker.
It executes the main() function from the user_commands module.
"""

import sys
import os

# Add the installed pacemaker package to the Python path
PACEMAKER_LIB = os.path.expanduser("~/.claude-pace-maker")
if os.path.exists(PACEMAKER_LIB):
    sys.path.insert(0, PACEMAKER_LIB)

try:
    from pacemaker.user_commands import main
    main()
except ImportError as e:
    print(f"Error: Could not import pace-maker modules: {e}", file=sys.stderr)
    print("Please ensure pace-maker is properly installed.", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
    chmod +x "$HOME/.local/bin/pace-maker"
  fi

  # Check if ~/.local/bin is in PATH
  if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
    echo -e "${YELLOW}⚠ Warning: ~/.local/bin is not in your PATH${NC}"
    echo "  Add this line to your ~/.bashrc or ~/.zshrc:"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
  fi

  echo -e "${GREEN}✓ CLI installed${NC}"
}

# Create default configuration
create_config() {
  echo "Creating configuration..."

  # Only create if doesn't exist (idempotency)
  if [ ! -f "$PACEMAKER_DIR/config.json" ]; then
    echo "Creating configuration file at $PACEMAKER_DIR/config.json..."
    cat > "$PACEMAKER_DIR/config.json" <<'EOF'
{
  "enabled": true,
  "base_delay": 5,
  "max_delay": 120,
  "threshold_percent": 0,
  "poll_interval": 60,
  "subagent_reminder_enabled": true,
  "subagent_reminder_frequency": 5,
  "subagent_reminder_message": "💡 Consider using the Task tool to delegate work to specialized subagents (per your guidelines)"
}
EOF
    echo -e "${GREEN}✓ Configuration created${NC}"
  else
    echo "Configuration file exists at $PACEMAKER_DIR/config.json, preserving..."
    echo -e "${YELLOW}✓ Configuration already exists (preserved)${NC}"
  fi

  # Copy extension registry config if doesn't exist
  if [ ! -f "$PACEMAKER_DIR/source_code_extensions.json" ]; then
    if [ -f "$SCRIPT_DIR/config/source_code_extensions.json" ]; then
      echo "Creating extension registry at $PACEMAKER_DIR/source_code_extensions.json..."
      cp "$SCRIPT_DIR/config/source_code_extensions.json" "$PACEMAKER_DIR/source_code_extensions.json"
      echo -e "${GREEN}✓ Extension registry created${NC}"
    else
      echo -e "${YELLOW}⚠ Warning: source_code_extensions.json not found in source, skipping${NC}"
    fi
  else
    echo "Extension registry exists at $PACEMAKER_DIR/source_code_extensions.json, preserving..."
    echo -e "${YELLOW}✓ Extension registry already exists (preserved)${NC}"
  fi
}

# Initialize database
init_database() {
  echo "Initializing database..."

  echo "Creating database schema..."
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

  echo "Adding database indexes..."
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
  USER_PROMPT_HOOK="$HOOKS_DIR/user-prompt-submit.sh"
  POST_HOOK="$HOOKS_DIR/post-tool-use.sh"
  PRE_TOOL_HOOK="$HOOKS_DIR/pre-tool-use.sh"
  STOP_HOOK="$HOOKS_DIR/stop.sh"
  SESSION_START_HOOK="$HOOKS_DIR/session-start.sh"
  SUBAGENT_START_HOOK="$HOOKS_DIR/subagent-start.sh"
  SUBAGENT_STOP_HOOK="$HOOKS_DIR/subagent-stop.sh"

  echo "Reading current settings from $SETTINGS_FILE..."

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

  # Use a temp file in a location accessible by snap-confined jq
  # mktemp creates files in /tmp which snap apps cannot access
  TEMP_FILE="$SETTINGS_DIR/.settings.tmp.$$"

  echo "Registering UserPromptSubmit hook..."
  echo "Registering PostToolUse hook..."
  echo "Registering PreToolUse hook..."
  echo "Registering Stop hook..."
  echo "Registering SessionStart hook..."
  echo "Registering SubagentStart hook..."
  echo "Registering SubagentStop hook..."

  # Remove ALL pace-maker hooks, then add them back
  # This ensures no duplicates and preserves other hooks like tdd-guard
  # Strategy: Remove pace-maker commands from within hook entries (not entire entries)
  jq --arg user_prompt "$USER_PROMPT_HOOK" \
     --arg post_hook "$POST_HOOK" \
     --arg pre_tool_hook "$PRE_TOOL_HOOK" \
     --arg stop_hook "$STOP_HOOK" \
     --arg session_start_hook "$SESSION_START_HOOK" \
     --arg subagent_start_hook "$SUBAGENT_START_HOOK" \
     --arg subagent_stop_hook "$SUBAGENT_STOP_HOOK" \
    '
     # Ensure .hooks exists as an object
     if .hooks == null then .hooks = {} else . end |

     # Helper function to remove pace-maker commands from a hook entry
     def remove_pacemaker_commands(pattern):
       if .hooks then
         .hooks = [.hooks[] | select(.command? // "" | test(pattern) | not)]
       else
         .
       end;

     # Remove pace-maker commands from within each hook entry
     .hooks.SessionStart = [
       (.hooks.SessionStart // [])[] |
       remove_pacemaker_commands("\\.claude/hooks/session-start\\.sh")
     ] |
     .hooks.UserPromptSubmit = [
       (.hooks.UserPromptSubmit // [])[] |
       remove_pacemaker_commands("\\.claude/hooks/user-prompt-submit\\.sh")
     ] |
     .hooks.PostToolUse = [
       (.hooks.PostToolUse // [])[] |
       remove_pacemaker_commands("\\.claude/hooks/post-tool-use\\.sh")
     ] |
     .hooks.PreToolUse = [
       (.hooks.PreToolUse // [])[] |
       remove_pacemaker_commands("\\.claude/hooks/pre-tool-use\\.sh")
     ] |
     .hooks.Stop = [
       (.hooks.Stop // [])[] |
       remove_pacemaker_commands("\\.claude/hooks/stop\\.sh")
     ] |
     .hooks.SubagentStart = [
       (.hooks.SubagentStart // [])[] |
       remove_pacemaker_commands("\\.claude/hooks/subagent-start\\.sh")
     ] |
     .hooks.SubagentStop = [
       (.hooks.SubagentStop // [])[] |
       remove_pacemaker_commands("\\.claude/hooks/subagent-stop\\.sh")
     ] |

     # Remove entries that have no hooks left after pace-maker removal
     .hooks.SessionStart = [.hooks.SessionStart[] | select(.hooks | length > 0)] |
     .hooks.UserPromptSubmit = [.hooks.UserPromptSubmit[] | select(.hooks | length > 0)] |
     .hooks.PostToolUse = [.hooks.PostToolUse[] | select(.hooks | length > 0)] |
     .hooks.PreToolUse = [.hooks.PreToolUse[] | select(.hooks | length > 0)] |
     .hooks.Stop = [.hooks.Stop[] | select(.hooks | length > 0)] |
     .hooks.SubagentStart = [.hooks.SubagentStart[] | select(.hooks | length > 0)] |
     .hooks.SubagentStop = [.hooks.SubagentStop[] | select(.hooks | length > 0)] |

     # Add pace-maker hooks back as separate entries with full paths
     .hooks.UserPromptSubmit += [{"hooks": [{"type": "command", "command": $user_prompt}]}] |
     .hooks.PostToolUse += [{"hooks": [{"type": "command", "command": $post_hook, "timeout": 360}]}] |
     .hooks.PreToolUse += [{
       "matcher": "Write|Edit",
       "hooks": [
         {"type": "command", "command": $pre_tool_hook, "timeout": 60}
       ]
     }] |
     .hooks.Stop += [{"hooks": [{"type": "command", "command": $stop_hook, "timeout": 120}]}] |
     .hooks.SessionStart += [{"hooks": [{"type": "command", "command": $session_start_hook, "timeout": 10}]}] |
     .hooks.SubagentStart += [{"hooks": [{"type": "command", "command": $subagent_start_hook, "timeout": 10}]}] |
     .hooks.SubagentStop += [{"hooks": [{"type": "command", "command": $subagent_stop_hook, "timeout": 10}]}]
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
  install_python_deps
  create_directories
  install_hooks
  install_cli
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
    echo "CLI command: ~/.local/bin/pace-maker"
    echo ""
    echo "The pace maker will automatically monitor your usage and"
    echo "introduce delays when approaching rate limits."
    echo ""
    echo "Try running: pace-maker status"
  else
    echo ""
    echo -e "${RED}Installation completed with errors.${NC}"
    echo "Please review the error messages above and try again."
    exit 1
  fi
}

main "$@"
