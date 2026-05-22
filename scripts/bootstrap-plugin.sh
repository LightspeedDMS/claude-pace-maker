#!/bin/bash
# Claude Pace Maker - Plugin bootstrap (filesystem wiring + optional Python deps).
#
# Usage (sourced or executed):
#   bootstrap-plugin.sh --light   # config, symlinks, install_source only
#   bootstrap-plugin.sh --full    # --light + pip deps + .bootstrap_ok marker
#
# Requires PLUGIN_ROOT (or CLAUDE_PLUGIN_ROOT) when executed directly.

set -euo pipefail

DEPS_SIGNATURE="requests:pyyaml:claude-agent-sdk"
PACEMAKER_DIR="${PACEMAKER_DIR:-$HOME/.claude-pace-maker}"
BOOTSTRAP_OK_MARKER="$PACEMAKER_DIR/.bootstrap_ok"
DEPS_DIR="$PACEMAKER_DIR/.python_deps"
DEPS_LOCK="$PACEMAKER_DIR/.python_deps.lock"
DEBUG_LOG="$PACEMAKER_DIR/hook_debug.log"
BOOTSTRAP_VERBOSE="${BOOTSTRAP_VERBOSE:-0}"

_bootstrap_log() {
    if [ "$BOOTSTRAP_VERBOSE" = "1" ]; then
        echo "[bootstrap-plugin] $*" >&2
    fi
}

_bootstrap_user_error() {
    echo "[pace-maker] $*" >&2
}

# Resolve PLUGIN_ROOT from env or script location.
_bootstrap_resolve_plugin_root() {
    if [ -n "${PLUGIN_ROOT:-}" ]; then
        return 0
    fi
    if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ]; then
        PLUGIN_ROOT="$CLAUDE_PLUGIN_ROOT"
        return 0
    fi
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PLUGIN_ROOT="$(cd "$script_dir/.." && pwd)"
}

# Find Python 3.10+; print absolute path.
resolve_python() {
    local py resolved
    for py in python3.11 python3.10 python3; do
        if command -v "$py" >/dev/null 2>&1; then
            if "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
                resolved="$(command -v "$py" 2>/dev/null || true)"
                if [ -n "$resolved" ]; then
                    echo "$resolved"
                    return 0
                fi
            fi
        fi
    done
    return 1
}

# Resolve python3 used by CLI shebang (#!/usr/bin/env python3).
resolve_cli_python() {
    local resolved
    resolved="$(command -v python3 2>/dev/null || true)"
    if [ -z "$resolved" ]; then
        return 1
    fi
    if "$resolved" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        echo "$resolved"
        return 0
    fi
    return 1
}

_deps_marker_path() {
    local abs_py="$1"
    local hash
    hash="$(printf '%s' "$abs_py" | sha256sum 2>/dev/null | awk '{print $1}' | cut -c1-16)"
    echo "$DEPS_DIR/${hash}"
}

_deps_imports_ok() {
    local py="$1"
    "$py" -c "import requests, yaml, claude_agent_sdk" 2>/dev/null
}

_ensure_python_deps_for() {
    local abs_py="$1"
    local marker failed_marker packages

    if [ -z "$abs_py" ] || [ ! -x "$abs_py" ]; then
        return 0
    fi

    mkdir -p "$DEPS_DIR"
    marker="$(_deps_marker_path "$abs_py")"
    failed_marker="${marker}.failed"

    if [ -f "$failed_marker" ]; then
        _bootstrap_log "skipping pip for $abs_py (previous failure)"
        return 1
    fi

    if [ -f "$marker" ]; then
        local line expected
        line="$(cat "$marker" 2>/dev/null || true)"
        expected="${abs_py}:${DEPS_SIGNATURE}"
        if [ "$line" = "$expected" ]; then
            return 0
        fi
    fi

    if _deps_imports_ok "$abs_py"; then
        echo "${abs_py}:${DEPS_SIGNATURE}" >"$marker"
        rm -f "$failed_marker"
        return 0
    fi

    packages=()
    "$abs_py" -c "import requests" 2>/dev/null || packages+=("requests")
    "$abs_py" -c "import yaml" 2>/dev/null || packages+=("pyyaml")
    "$abs_py" -c "import claude_agent_sdk" 2>/dev/null || packages+=("claude-agent-sdk")

    if [ ${#packages[@]} -eq 0 ]; then
        echo "${abs_py}:${DEPS_SIGNATURE}" >"$marker"
        rm -f "$failed_marker"
        return 0
    fi

    (
        flock -x 9
        if [ -f "$marker" ]; then
            line="$(cat "$marker" 2>/dev/null || true)"
            if [ "$line" = "${abs_py}:${DEPS_SIGNATURE}" ] && _deps_imports_ok "$abs_py"; then
                exit 0
            fi
        fi
        if "$abs_py" -m pip install --user "${packages[@]}" >>"$DEBUG_LOG" 2>&1; then
            if _deps_imports_ok "$abs_py"; then
                echo "${abs_py}:${DEPS_SIGNATURE}" >"$marker"
                rm -f "$failed_marker"
                exit 0
            fi
        fi
        echo "[bootstrap-plugin] pip install failed for $abs_py (${packages[*]})" >>"$DEBUG_LOG"
        touch "$failed_marker"
        _bootstrap_user_error "Could not install Python packages (${packages[*]}) for $abs_py."
        _bootstrap_user_error "Run: pace-maker doctor   (or: bash \"\$PLUGIN_ROOT/scripts/doctor.sh\")"
        exit 1
    ) 9>"$DEPS_LOCK"
    return $?
}

# Cheap filesystem bootstrap (no pip).
bootstrap_light() {
    _bootstrap_resolve_plugin_root

    local config_file="$PACEMAKER_DIR/config.json"
    local pacemaker_pkg="$PACEMAKER_DIR/pacemaker"
    local local_bin="$HOME/.local/bin"
    local cli_target="$PLUGIN_ROOT/scripts/pace-maker"
    local cli_symlink="$local_bin/pace-maker"
    local extensions_dst="$PACEMAKER_DIR/source_code_extensions.json"

    mkdir -p "$PACEMAKER_DIR"

    if [ ! -f "$config_file" ]; then
        local defaults_src="$PLUGIN_ROOT/config/config.defaults.json"
        if [ -f "$defaults_src" ]; then
            cp "$defaults_src" "$config_file"
        else
            cat >"$config_file" <<'EOF'
{
  "enabled": true,
  "log_level": 2,
  "langfuse_enabled": false,
  "intent_validation_enabled": true,
  "tdd_enabled": true,
  "tempo_mode": "auto",
  "five_hour_limit_enabled": true,
  "weekly_limit_enabled": false
}
EOF
        fi
    fi

    if [ ! -f "$extensions_dst" ]; then
        local extensions_src="$PLUGIN_ROOT/config/source_code_extensions.json"
        if [ -f "$extensions_src" ]; then
            cp "$extensions_src" "$extensions_dst"
        fi
    fi

    mkdir -p "$local_bin"
    if [ -L "$cli_symlink" ] || [ ! -e "$cli_symlink" ] || [ -f "$cli_symlink" ]; then
        ln -sf "$cli_target" "$cli_symlink"
    fi

    local src_pacemaker="$PLUGIN_ROOT/src/pacemaker"
    if [ -d "$src_pacemaker" ] && [ -f "$src_pacemaker/user_commands.py" ]; then
        if [ -d "$pacemaker_pkg" ] && [ ! -L "$pacemaker_pkg" ]; then
            if [ -f "$pacemaker_pkg/user_commands.py" ]; then
                _bootstrap_log "keeping existing pacemaker directory at $pacemaker_pkg"
            else
                ln -sfn "$src_pacemaker" "$pacemaker_pkg"
            fi
        else
            ln -sfn "$src_pacemaker" "$pacemaker_pkg"
        fi
        printf '%s\n' "$PLUGIN_ROOT" >"$PACEMAKER_DIR/install_source"
    fi
}

bootstrap_full() {
    bootstrap_light

    local hook_py cli_py
    hook_py="$(resolve_python)" || {
        _bootstrap_user_error "Python 3.10+ not found. Install python3.10+ and run pace-maker doctor."
        return 1
    }

    if ! _ensure_python_deps_for "$hook_py"; then
        return 1
    fi

    cli_py="$(resolve_cli_python 2>/dev/null || true)"
    if [ -n "${cli_py:-}" ] && [ "$cli_py" != "$hook_py" ]; then
        if ! _ensure_python_deps_for "$cli_py"; then
            return 1
        fi
    fi

    if ! bootstrap_verify; then
        return 1
    fi

    date -u +"%Y-%m-%dT%H:%M:%SZ" >"$BOOTSTRAP_OK_MARKER"
    return 0
}

bootstrap_verify() {
    local hook_py
    hook_py="$(resolve_python)" || return 1
    if ! _deps_imports_ok "$hook_py"; then
        _bootstrap_user_error "Python dependency check failed for $hook_py"
        return 1
    fi

    local cli="$HOME/.local/bin/pace-maker"
    if [ -x "$cli" ] || [ -L "$cli" ]; then
        if ! "$cli" status >/dev/null 2>&1; then
            _bootstrap_log "pace-maker status smoke test failed (non-fatal for hooks)"
        fi
    fi
    return 0
}

bootstrap_needs_full() {
    [ ! -f "$BOOTSTRAP_OK_MARKER" ]
}

# Entry when executed directly.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    mode="--light"
    for arg in "$@"; do
        case "$arg" in
            --full | --light) mode="$arg" ;;
        esac
    done
    _bootstrap_resolve_plugin_root
    case "$mode" in
        --full)
            if bootstrap_full; then
                exit 0
            fi
            exit 1
            ;;
        *)
            bootstrap_light
            exit 0
            ;;
    esac
fi
