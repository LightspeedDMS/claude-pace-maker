#!/bin/bash
# Claude Pace Maker - diagnose and repair plugin bootstrap (CLI + deps + pacemaker package).
#
# Callable without importing pacemaker. Safe to run after `claude plugin install`.

set -euo pipefail

PACEMAKER_DIR="${HOME}/.claude-pace-maker"
BOOTSTRAP_VERBOSE=1
export BOOTSTRAP_VERBOSE

log() {
    echo "[pace-maker doctor] $*" >&2
}

find_plugin_root() {
    # CLAUDE_PLUGIN_ROOT is only honored when it actually points at a
    # claude-pace-maker plugin root (contains scripts/bootstrap-plugin.sh).
    # The doctor is occasionally invoked from another plugin's hook context
    # where the inherited CLAUDE_PLUGIN_ROOT points at the caller plugin,
    # not pace-maker — using that path would run bootstrap_full against the
    # wrong tree.
    if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] \
        && [ -f "${CLAUDE_PLUGIN_ROOT}/scripts/bootstrap-plugin.sh" ]; then
        printf '%s' "$CLAUDE_PLUGIN_ROOT"
        return 0
    fi
    local script_dir bootstrap
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [ -f "$script_dir/bootstrap-plugin.sh" ]; then
        printf '%s' "$(cd "$script_dir/.." && pwd)"
        return 0
    fi
    bootstrap="$(
        find "${HOME}/.claude" \( \
            -path '*/claude-pace-maker/*/scripts/bootstrap-plugin.sh' -o \
            -path '*/claude-pace-maker/scripts/bootstrap-plugin.sh' \
        \) -type f 2>/dev/null | sort -V | tail -1 || true
    )"
    if [ -n "$bootstrap" ]; then
        printf '%s' "$(cd "$(dirname "$bootstrap")/.." && pwd)"
        return 0
    fi
    return 1
}

print_diagnostics() {
    local plugin_root="$1"
    local cli="${HOME}/.local/bin/pace-maker"
    local pkg="${PACEMAKER_DIR}/pacemaker"

    log "plugin root: ${plugin_root}"
    log "pacemaker home: ${PACEMAKER_DIR}"

    if [ -L "$pkg" ]; then
        log "pacemaker package: symlink -> $(readlink "$pkg" 2>/dev/null || echo '?')"
    elif [ -d "$pkg" ]; then
        log "pacemaker package: directory at $pkg"
    else
        log "pacemaker package: MISSING at $pkg"
    fi

    if [ -e "$cli" ]; then
        log "CLI: $cli"
        if [ -L "$cli" ]; then
            log "CLI target: $(readlink "$cli" 2>/dev/null || echo '?')"
        fi
    else
        log "CLI: not found at $cli"
    fi

    if ! echo "${PATH:-}" | grep -q "${HOME}/.local/bin"; then
        log "WARNING: ~/.local/bin is not in PATH"
        log "  Add to shell profile: export PATH=\"\$HOME/.local/bin:\$PATH\""
    fi

    if [ -d "${PACEMAKER_DIR}/venv" ]; then
        log "managed venv: ${PACEMAKER_DIR}/venv"
        if [ -f "${PACEMAKER_DIR}/.venv_stamp" ]; then
            log "venv stamp: $(cat "${PACEMAKER_DIR}/.venv_stamp" 2>/dev/null || echo '?')"
        fi
        if [ -f "${PACEMAKER_DIR}/.venv.failed" ]; then
            log "venv setup: FAILED (see hook_debug.log)"
        fi
    else
        log "managed venv: not present"
    fi

    if [ -f "${PACEMAKER_DIR}/.bootstrap_ok" ]; then
        log "bootstrap_ok: $(cat "${PACEMAKER_DIR}/.bootstrap_ok" 2>/dev/null)"
    else
        log "bootstrap_ok: not present (full bootstrap required)"
    fi
}

main() {
    local plugin_root
    if ! plugin_root="$(find_plugin_root)"; then
        cat >&2 <<'EOF'
Error: could not locate claude-pace-maker plugin root.

Try:
  claude plugin install claude-pace-maker@lightspeed-claude-plugins
  Or set CLAUDE_PLUGIN_ROOT to your plugin checkout.
EOF
        exit 1
    fi

    export PLUGIN_ROOT="$plugin_root"
    log "Running full bootstrap..."
    print_diagnostics "$plugin_root"

    # Allow retry after a previous failed venv/pip attempt.
    rm -f "${PACEMAKER_DIR}/.venv.failed"

    # shellcheck source=scripts/bootstrap-plugin.sh
    source "${plugin_root}/scripts/bootstrap-plugin.sh"

    if bootstrap_full; then
        log "Bootstrap succeeded."
        local cli="${HOME}/.local/bin/pace-maker"
        if [ -x "$cli" ] || [ -L "$cli" ]; then
            log "Smoke test: pace-maker status"
            "$cli" status
        fi
        exit 0
    fi

    cat >&2 <<EOF

Bootstrap failed. Check ${PACEMAKER_DIR}/hook_debug.log for details.

If venv setup failed, ensure Python 3.10+ and the venv module are installed, then re-run:
  bash "${plugin_root}/scripts/doctor.sh"
EOF
    exit 1
}

main "$@"
