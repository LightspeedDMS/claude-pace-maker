#!/bin/bash
# Claude Pace Maker - Plugin bootstrap (filesystem wiring + managed venv).
#
# Usage (sourced or executed):
#   bootstrap-plugin.sh --light   # config, symlinks, install_source only
#   bootstrap-plugin.sh --full    # --light + venv deps + .bootstrap_ok marker
#
# Requires PLUGIN_ROOT (or CLAUDE_PLUGIN_ROOT) when executed directly.

set -euo pipefail

PACEMAKER_DIR="${PACEMAKER_DIR:-$HOME/.claude-pace-maker}"
VENV_DIR="$PACEMAKER_DIR/venv"
VENV_PYTHON="$VENV_DIR/bin/python3"
VENV_STAMP="$PACEMAKER_DIR/.venv_stamp"
VENV_LOCK_FILE="$PACEMAKER_DIR/.venv.lock"
VENV_LOCK_LINK="$PACEMAKER_DIR/.venv.lock.link"
VENV_FAILED_MARKER="$PACEMAKER_DIR/.venv.failed"
BOOTSTRAP_OK_MARKER="$PACEMAKER_DIR/.bootstrap_ok"
DEBUG_LOG="$PACEMAKER_DIR/hook_debug.log"
BOOTSTRAP_VERBOSE="${BOOTSTRAP_VERBOSE:-0}"

# Pinned dependencies are loaded lazily from requirements.txt (single
# source of truth). DEPS_SIGNATURE is the sha256 of that file so any
# edit — version bump, comment change, additional dep — invalidates
# .venv_stamp and triggers a re-install on next bootstrap_full.
REQUIREMENTS_FILE=""
DEPS_SIGNATURE=""
PINNED_PACKAGES=()
_PINNED_DEPS_LOADED=0

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

# Compute sha256 of a file using whichever digest tool is available on
# the host (sha256sum on Linux, shasum on macOS, openssl as fallback).
_sha256_of_file() {
    local file="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$file" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$file" | awk '{print $1}'
    elif command -v openssl >/dev/null 2>&1; then
        openssl dgst -sha256 "$file" | awk '{print $NF}'
    else
        return 1
    fi
}

# Parse requirements.txt into PINNED_PACKAGES and derive DEPS_SIGNATURE
# from its sha256. Idempotent — guarded by _PINNED_DEPS_LOADED so
# sourcing this file repeatedly is cheap. Must be called after
# _bootstrap_resolve_plugin_root.
_ensure_pinned_deps_loaded() {
    [ "$_PINNED_DEPS_LOADED" = "1" ] && return 0
    _bootstrap_resolve_plugin_root
    REQUIREMENTS_FILE="$PLUGIN_ROOT/requirements.txt"
    if [ ! -f "$REQUIREMENTS_FILE" ]; then
        _bootstrap_user_error "requirements.txt not found at $REQUIREMENTS_FILE"
        return 1
    fi
    PINNED_PACKAGES=()
    local raw spec
    while IFS= read -r raw || [ -n "$raw" ]; do
        # Strip trailing inline comment, then trim whitespace.
        spec="${raw%%#*}"
        spec="${spec#"${spec%%[![:space:]]*}"}"
        spec="${spec%"${spec##*[![:space:]]}"}"
        [ -z "$spec" ] && continue
        PINNED_PACKAGES+=("$spec")
    done <"$REQUIREMENTS_FILE"

    if [ ${#PINNED_PACKAGES[@]} -eq 0 ]; then
        _bootstrap_user_error "requirements.txt at $REQUIREMENTS_FILE is empty"
        return 1
    fi

    DEPS_SIGNATURE="$(_sha256_of_file "$REQUIREMENTS_FILE")"
    if [ -z "$DEPS_SIGNATURE" ]; then
        _bootstrap_user_error "no sha256 tool available (need sha256sum, shasum, or openssl)"
        return 1
    fi
    _PINNED_DEPS_LOADED=1
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

# Canonical runtime: managed venv Python when stamp matches (no python fork),
# else empty. The deep import + version-pin check runs on session_start via
# bootstrap_full → _ensure_venv_and_deps; per-hook calls trust the stamp.
resolve_runtime_python() {
    if [ -x "$VENV_PYTHON" ] && [ -f "$VENV_STAMP" ]; then
        _ensure_pinned_deps_loaded || return 1
        local stamp
        stamp="$(_read_venv_stamp)"
        case "$stamp" in
            *":${DEPS_SIGNATURE}")
                echo "$VENV_PYTHON"
                return 0
                ;;
        esac
    fi
    return 1
}

# Verify the interpreter has every pinned dep installed at the exact
# pinned version. Drives resolve_runtime_python and the venv-ready
# check inside _create_or_repair_venv_locked, so any drift (manual
# upgrade, partial install) triggers a repair.
_deps_imports_ok() {
    local py="$1"
    _ensure_pinned_deps_loaded || return 1
    # Comma-joined list of `name==version` specs; Python parses and
    # asserts each one via importlib.metadata.
    local pinned_csv
    pinned_csv="$(IFS=,; echo "${PINNED_PACKAGES[*]}")"
    PACEMAKER_PINNED_DEPS="$pinned_csv" \
    "$py" -c '
import os, sys
from importlib.metadata import version
try:
    # Soundness: top-level imports we actually use at runtime.
    import requests, yaml, claude_agent_sdk
    # Pin assertion driven by requirements.txt.
    for spec in os.environ["PACEMAKER_PINNED_DEPS"].split(","):
        name, _, pinned = spec.partition("==")
        if not name or not pinned:
            sys.exit(1)
        if version(name) != pinned:
            sys.exit(1)
except Exception:
    sys.exit(1)
' 2>/dev/null
}

_read_venv_stamp() {
    if [ -f "$VENV_STAMP" ]; then
        cat "$VENV_STAMP" 2>/dev/null || true
    fi
}

_write_venv_stamp() {
    local base_py="$1"
    _ensure_pinned_deps_loaded || return 1
    echo "${base_py}:${DEPS_SIGNATURE}" >"$VENV_STAMP"
}

# Record a ready venv only after imports succeed (never on venv create alone).
_mark_venv_ready() {
    local base_py="$1"
    rm -f "$VENV_FAILED_MARKER"
    _write_venv_stamp "$base_py"
}

# Clear a symlink lock left by a crashed bootstrap process. The symlink's
# target string IS the holder's pid — set atomically at symlink(2) time —
# so there is no "acquired but pid not yet written" intermediate state to
# defend against. Just read the pid; if the process is gone, drop the
# lock.
_clear_stale_venv_lock_symlink() {
    [ -L "$VENV_LOCK_LINK" ] || return 0
    local pid
    pid="$(readlink "$VENV_LOCK_LINK" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        return 0
    fi
    _bootstrap_log "clearing orphaned venv install lock (pid ${pid:-?} not running)"
    rm -f "$VENV_LOCK_LINK"
}

# Atomically acquire the lock. `symlink(2)` is the POSIX-atomic creation
# primitive for this use case — the lock identity and the holder's pid
# are bound into a single observable name, set in one syscall. There is
# no torn-write window: either the symlink exists with a populated
# target, or it doesn't exist at all. `readlink` is the canonical way to
# inspect the holder.
_try_acquire_venv_install_lock() {
    ln -s "$$" "$VENV_LOCK_LINK" 2>/dev/null
}

# Portable lock (flock on Linux; symlink on macOS where flock is often
# absent).
_with_venv_install_lock() {
    # Always clean stale symlink locks, even when flock is the active mechanism.
    _clear_stale_venv_lock_symlink
    if command -v flock >/dev/null 2>&1; then
        (
            flock -w 120 -x 9
            "$@"
        ) 9>"$VENV_LOCK_FILE"
        return $?
    fi

    local waited=0
    while ! _try_acquire_venv_install_lock; do
        _clear_stale_venv_lock_symlink
        if _try_acquire_venv_install_lock; then
            break
        fi
        sleep 0.2
        waited=$((waited + 1))
        if [ "$waited" -ge 600 ]; then
            _bootstrap_user_error "Timed out waiting for venv install lock at $VENV_LOCK_LINK"
            _bootstrap_user_error "If no other pace-maker command is running, remove the lock and retry:"
            _bootstrap_user_error "  rm -f \"$VENV_LOCK_LINK\""
            return 1
        fi
    done
    local rc=0
    "$@" || rc=$?
    rm -f "$VENV_LOCK_LINK"
    return "$rc"
}

_venv_needs_recreate() {
    local base_py="$1"
    if [ ! -d "$VENV_DIR" ] || [ ! -x "$VENV_PYTHON" ]; then
        return 0
    fi
    _ensure_pinned_deps_loaded || return 0
    local stamp expected
    stamp="$(_read_venv_stamp)"
    expected="${base_py}:${DEPS_SIGNATURE}"
    if [ "$stamp" != "$expected" ]; then
        return 0
    fi
    return 1
}

# Fast path (no lock): venv exists, stamp matches, deps importable.
# Slow path (under lock): any venv mutation — rm/recreate/pip install.
_ensure_venv_and_deps() {
    local base_py="$1"
    local stamp expected

    if [ -z "$base_py" ] || [ ! -x "$base_py" ]; then
        return 1
    fi

    mkdir -p "$PACEMAKER_DIR"

    if [ -f "$VENV_FAILED_MARKER" ]; then
        _bootstrap_log "skipping venv setup (previous failure)"
        return 1
    fi

    _ensure_pinned_deps_loaded || return 1

    expected="${base_py}:${DEPS_SIGNATURE}"
    if [ -x "$VENV_PYTHON" ] && _deps_imports_ok "$VENV_PYTHON"; then
        stamp="$(_read_venv_stamp)"
        if [ "$stamp" = "$expected" ]; then
            return 0
        fi
    fi

    _with_venv_install_lock _create_or_repair_venv_locked "$base_py"
    return $?
}

# Called under _with_venv_install_lock. Owns ALL venv mutations:
# rm -rf, python -m venv, pip install. Re-checks state under lock so a
# concurrent process that already finished the work is detected and no-op'd.
_create_or_repair_venv_locked() {
    local base_py="$1"
    local stamp expected
    _ensure_pinned_deps_loaded || return 1
    expected="${base_py}:${DEPS_SIGNATURE}"

    # Double-checked locking: another process may have just finished bootstrap.
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
    fi

    if _deps_imports_ok "$VENV_PYTHON"; then
        _mark_venv_ready "$base_py"
        return 0
    fi

    _pip_install_into_venv "$base_py"
}

# Always called under the install lock from _create_or_repair_venv_locked.
# Idempotent: `pip install -r requirements.txt` is a no-op when every
# pinned spec is already satisfied, installs missing packages, and
# upgrades/downgrades drifted ones to match the pin.
_pip_install_into_venv() {
    local base_py="$1"
    _ensure_pinned_deps_loaded || return 1

    if "$VENV_PYTHON" -m pip install --no-input -r "$REQUIREMENTS_FILE" >>"$DEBUG_LOG" 2>&1; then
        if _deps_imports_ok "$VENV_PYTHON"; then
            _mark_venv_ready "$base_py"
            return 0
        fi
    fi
    echo "[bootstrap-plugin] pip install failed in venv for $REQUIREMENTS_FILE" >>"$DEBUG_LOG"
    touch "$VENV_FAILED_MARKER"
    _bootstrap_user_error "Could not install Python packages from $REQUIREMENTS_FILE into $VENV_DIR."
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
    trap 'unset PACEMAKER_BOOTSTRAPPING' RETURN

    bootstrap_light

    local base_py
    base_py="$(resolve_python)" || {
        _bootstrap_user_error "Python 3.10+ not found. Install python3.10+ and run pace-maker doctor."
        return 1
    }

    # Allow retry on each session_start — transient failures (network timeout)
    # should not block permanently. If venv setup fails again, the marker is
    # re-written by _pip_install_into_venv / _create_or_repair_venv_locked.
    rm -f "$VENV_FAILED_MARKER"

    if ! _ensure_venv_and_deps "$base_py"; then
        return 1
    fi

    # Legacy per-interpreter markers from pre-venv bootstrap (safe to remove).
    rm -rf "${PACEMAKER_DIR}/.python_deps" "${PACEMAKER_DIR}/.python_deps.lock" 2>/dev/null || true

    if ! bootstrap_verify; then
        return 1
    fi

    date -u +"%Y-%m-%dT%H:%M:%SZ" >"$BOOTSTRAP_OK_MARKER"
    # Reset hook.sh's throttle so a future fallback re-warns the user.
    rm -f "${PACEMAKER_DIR}/.python_fallback_warn" 2>/dev/null || true
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

# Cheap check intended for per-hook invocation: only stat() + file read +
# a single sha256 of requirements.txt (no python fork). Returns 0 (needs
# full) when state is missing or the stamp's suffix doesn't match the
# current sha256 of requirements.txt. The deep import + version-pin check
# happens on session_start via bootstrap_full -> _ensure_venv_and_deps
# which always validates under the install lock.
bootstrap_needs_full() {
    [ -f "$BOOTSTRAP_OK_MARKER" ] || return 0
    [ -x "$VENV_PYTHON" ] || return 0
    [ -f "$VENV_STAMP" ] || return 0
    _ensure_pinned_deps_loaded || return 0
    local stamp
    stamp="$(_read_venv_stamp)"
    case "$stamp" in
        *":${DEPS_SIGNATURE}") return 1 ;;
        *) return 0 ;;
    esac
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
