#!/bin/bash
# Claude Pace Maker - Plugin bootstrap (filesystem wiring + managed venv).
#
# Usage (sourced or executed):
#   bootstrap-plugin.sh --light   # config, symlinks, install_source only
#   bootstrap-plugin.sh --full    # --light + venv deps + .bootstrap_ok marker
#
# Requires PLUGIN_ROOT (or CLAUDE_PLUGIN_ROOT) when executed directly.

set -euo pipefail

DEPS_SIGNATURE="requests:pyyaml:claude-agent-sdk"
PACEMAKER_DIR="${PACEMAKER_DIR:-$HOME/.claude-pace-maker}"
VENV_DIR="$PACEMAKER_DIR/venv"
VENV_PYTHON="$VENV_DIR/bin/python3"
VENV_STAMP="$PACEMAKER_DIR/.venv_stamp"
VENV_LOCK_FILE="$PACEMAKER_DIR/.venv.lock"
VENV_LOCK_DIR="$PACEMAKER_DIR/.venv.lock.d"
VENV_FAILED_MARKER="$PACEMAKER_DIR/.venv.failed"
BOOTSTRAP_OK_MARKER="$PACEMAKER_DIR/.bootstrap_ok"
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

# Find Python 3.10+ for venv creation; prefer the newest available interpreter.
resolve_python() {
    local py resolved
    for py in python3.13 python3.12 python3.11 python3.10 python3; do
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

# Canonical runtime: managed venv Python when ready, else empty.
resolve_runtime_python() {
    if [ -x "$VENV_PYTHON" ] && _deps_imports_ok "$VENV_PYTHON"; then
        echo "$VENV_PYTHON"
        return 0
    fi
    return 1
}

_deps_imports_ok() {
    local py="$1"
    "$py" -c "import requests, yaml, claude_agent_sdk" 2>/dev/null
}

_read_venv_stamp() {
    if [ -f "$VENV_STAMP" ]; then
        cat "$VENV_STAMP" 2>/dev/null || true
    fi
}

_write_venv_stamp() {
    local base_py="$1"
    echo "${base_py}:${DEPS_SIGNATURE}" >"$VENV_STAMP"
}

# Clear mkdir-based lock left behind when a prior bootstrap was interrupted.
_clear_stale_venv_lock_dir() {
    if [ ! -d "$VENV_LOCK_DIR" ]; then
        return 0
    fi
    local lock_pid=""
    if [ -f "$VENV_LOCK_DIR/pid" ]; then
        lock_pid="$(cat "$VENV_LOCK_DIR/pid" 2>/dev/null || true)"
        if [ -n "$lock_pid" ] && kill -0 "$lock_pid" 2>/dev/null; then
            return 0
        fi
        _bootstrap_log "clearing orphaned venv install lock (pid ${lock_pid:-?} not running)"
        rm -rf "$VENV_LOCK_DIR"
        return 0
    fi
    if find "$VENV_LOCK_DIR" -maxdepth 0 -mmin +2 2>/dev/null | grep -q .; then
        _bootstrap_log "clearing stale venv install lock at $VENV_LOCK_DIR"
        rm -rf "$VENV_LOCK_DIR"
    fi
}

# Portable lock (flock on Linux; mkdir + pid on macOS where flock is often absent).
_with_venv_install_lock() {
    if command -v flock >/dev/null 2>&1; then
        (
            flock -x 9
            "$@"
        ) 9>"$VENV_LOCK_FILE"
        return $?
    fi

    local waited=0
    while ! mkdir "$VENV_LOCK_DIR" 2>/dev/null; do
        _clear_stale_venv_lock_dir
        if mkdir "$VENV_LOCK_DIR" 2>/dev/null; then
            break
        fi
        sleep 0.2
        waited=$((waited + 1))
        if [ "$waited" -ge 150 ]; then
            _bootstrap_user_error "Timed out waiting for venv install lock at $VENV_LOCK_DIR"
            _bootstrap_user_error "If no other pace-maker command is running, remove the lock and retry:"
            _bootstrap_user_error "  rm -rf \"$VENV_LOCK_DIR\""
            return 1
        fi
    done
    echo "$$" >"$VENV_LOCK_DIR/pid"
    "$@"
    local rc=$?
    rm -rf "$VENV_LOCK_DIR"
    return "$rc"
}

_venv_needs_recreate() {
    local base_py="$1"
    if [ ! -d "$VENV_DIR" ] || [ ! -x "$VENV_PYTHON" ]; then
        return 0
    fi
    local stamp expected
    stamp="$(_read_venv_stamp)"
    expected="${base_py}:${DEPS_SIGNATURE}"
    if [ "$stamp" != "$expected" ]; then
        return 0
    fi
    return 1
}

_ensure_venv_and_deps() {
    local base_py="$1"
    local stamp expected packages

    if [ -z "$base_py" ] || [ ! -x "$base_py" ]; then
        return 1
    fi

    mkdir -p "$PACEMAKER_DIR"

    if [ -f "$VENV_FAILED_MARKER" ]; then
        _bootstrap_log "skipping venv setup (previous failure)"
        return 1
    fi

    expected="${base_py}:${DEPS_SIGNATURE}"
    if [ -x "$VENV_PYTHON" ] && _deps_imports_ok "$VENV_PYTHON"; then
        stamp="$(_read_venv_stamp)"
        if [ "$stamp" = "$expected" ]; then
            return 0
        fi
    fi

    if _venv_needs_recreate "$base_py"; then
        if [ -d "$VENV_DIR" ]; then
            _bootstrap_log "recreating venv at $VENV_DIR (base or deps changed)"
            rm -rf "$VENV_DIR"
        fi
        if ! "$base_py" -m venv "$VENV_DIR" >>"$DEBUG_LOG" 2>&1; then
            echo "[bootstrap-plugin] venv creation failed for $base_py at $VENV_DIR" >>"$DEBUG_LOG"
            touch "$VENV_FAILED_MARKER"
            _bootstrap_user_error "Could not create Python virtual environment at $VENV_DIR."
            _bootstrap_user_error "Ensure the venv module is available (e.g. brew install python@3.12 or apt install python3-venv)."
            _bootstrap_user_error "Run: pace-maker doctor   (or: bash \"\$PLUGIN_ROOT/scripts/doctor.sh\")"
            return 1
        fi
        _write_venv_stamp "$base_py"
    fi

    if _deps_imports_ok "$VENV_PYTHON"; then
        rm -f "$VENV_FAILED_MARKER"
        _write_venv_stamp "$base_py"
        return 0
    fi

    packages=()
    "$VENV_PYTHON" -c "import requests" 2>/dev/null || packages+=("requests")
    "$VENV_PYTHON" -c "import yaml" 2>/dev/null || packages+=("pyyaml")
    "$VENV_PYTHON" -c "import claude_agent_sdk" 2>/dev/null || packages+=("claude-agent-sdk")

    if [ ${#packages[@]} -eq 0 ]; then
        rm -f "$VENV_FAILED_MARKER"
        _write_venv_stamp "$base_py"
        return 0
    fi

    _with_venv_install_lock _pip_install_into_venv "$base_py" "${packages[@]}"
    return $?
}

_pip_install_into_venv() {
    local base_py="$1"
    shift
    local packages=("$@")
    local stamp expected
    expected="${base_py}:${DEPS_SIGNATURE}"

    if [ -x "$VENV_PYTHON" ] && _deps_imports_ok "$VENV_PYTHON"; then
        stamp="$(_read_venv_stamp)"
        if [ "$stamp" = "$expected" ]; then
            return 0
        fi
    fi
    if "$VENV_PYTHON" -m pip install "${packages[@]}" >>"$DEBUG_LOG" 2>&1; then
        if _deps_imports_ok "$VENV_PYTHON"; then
            rm -f "$VENV_FAILED_MARKER"
            _write_venv_stamp "$base_py"
            return 0
        fi
    fi
    echo "[bootstrap-plugin] pip install failed in venv for ${packages[*]}" >>"$DEBUG_LOG"
    touch "$VENV_FAILED_MARKER"
    _bootstrap_user_error "Could not install Python packages (${packages[*]}) in $VENV_DIR."
    _bootstrap_user_error "Run: pace-maker doctor   (or: bash \"\$PLUGIN_ROOT/scripts/doctor.sh\")"
    return 1
}

# Cheap filesystem bootstrap (no venv/pip).
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
    export PACEMAKER_BOOTSTRAPPING=1
    bootstrap_light

    local base_py
    base_py="$(resolve_python)" || {
        _bootstrap_user_error "Python 3.10+ not found. Install python3.10+ and run pace-maker doctor."
        unset PACEMAKER_BOOTSTRAPPING
        return 1
    }

    if ! _ensure_venv_and_deps "$base_py"; then
        unset PACEMAKER_BOOTSTRAPPING
        return 1
    fi

    # Legacy per-interpreter markers from pre-venv bootstrap (safe to remove).
    rm -rf "${PACEMAKER_DIR}/.python_deps" "${PACEMAKER_DIR}/.python_deps.lock" 2>/dev/null || true

    if ! bootstrap_verify; then
        unset PACEMAKER_BOOTSTRAPPING
        return 1
    fi

    date -u +"%Y-%m-%dT%H:%M:%SZ" >"$BOOTSTRAP_OK_MARKER"
    unset PACEMAKER_BOOTSTRAPPING
    return 0
}

_bootstrap_pythonpath() {
    local install_marker="${PACEMAKER_DIR}/install_source"
    if [ -f "$install_marker" ]; then
        local source_dir
        source_dir="$(cat "$install_marker")"
        if [[ "$source_dir" != *"pipx"* ]]; then
            export PYTHONPATH="${source_dir}/src${PYTHONPATH:+:${PYTHONPATH}}"
            return 0
        fi
    fi
    if [ -n "${PLUGIN_ROOT:-}" ]; then
        export PYTHONPATH="${PLUGIN_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
    fi
}

bootstrap_verify() {
    local runtime_py
    runtime_py="$(resolve_runtime_python)" || {
        _bootstrap_user_error "Python dependency check failed (managed venv at $VENV_DIR)"
        return 1
    }
    if ! _deps_imports_ok "$runtime_py"; then
        _bootstrap_user_error "Python dependency check failed for $runtime_py"
        return 1
    fi

    # Smoke-test via venv Python directly — never call the pace-maker CLI here (that
    # would re-enter bootstrap_full before .bootstrap_ok exists and recurse/hang).
    _bootstrap_resolve_plugin_root
    _bootstrap_pythonpath
    if ! "$runtime_py" -m pacemaker.user_commands status >/dev/null 2>&1; then
        _bootstrap_log "pacemaker.user_commands status smoke test failed (non-fatal for hooks)"
    fi
    return 0
}

bootstrap_needs_full() {
    if [ ! -f "$BOOTSTRAP_OK_MARKER" ]; then
        return 0
    fi
    if ! resolve_runtime_python >/dev/null 2>&1; then
        return 0
    fi
    return 1
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
