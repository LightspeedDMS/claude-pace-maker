#!/usr/bin/env bash
#
# Run all tests independently (one pytest invocation per file).
#
# Why: Running all tests in a single pytest process causes SQLite WAL
# contention — each test creates its own DB and the concurrent teardown
# of connections blocks executescript() calls in subsequent tests.
# Running files independently avoids this entirely.
#
# Usage:
#   ./scripts/run_tests.sh          # Run all tests
#   ./scripts/run_tests.sh --quick  # Skip slow e2e tests
#   ./scripts/run_tests.sh --tb     # Show failure tracebacks
#
# Env vars:
#   PACEMAKER_TEST_TIMEOUT       Per-file timeout in seconds for
#                                 tests/*.py and tests/unit/*.py (default
#                                 120, issue #143 -- raised from the
#                                 original 30s, which a normal file can
#                                 exceed on a loaded box even when it
#                                 passes when run alone).
#   PACEMAKER_E2E_TEST_TIMEOUT   Per-file timeout in seconds for
#                                 tests/e2e/*.py (default 900, issue #144
#                                 code-review follow-up). Files under
#                                 tests/e2e/ run real subprocesses
#                                 (install.sh, bootstrap-plugin.sh) and
#                                 were never given their own budget --
#                                 they inherited the fast-suite's 120s
#                                 file / 15s test caps, so a FULL run
#                                 (this script without --quick) always
#                                 timed them out even though --quick
#                                 (which skips tests/e2e/ entirely) was
#                                 green.
#   PACEMAKER_E2E_PYTEST_TIMEOUT Per-test --timeout for tests/e2e/*.py
#                                 (default 90, same follow-up).
#   PACEMAKER_TEST_PYTHON        Interpreter to run pytest with. Default:
#                                 mirrors src/hooks/*.sh's find_python()
#                                 -- prefer an interpreter where
#                                 claude_agent_sdk actually imports among
#                                 python3.11/python3.10/python3, else
#                                 fall back to existence-only order. Bare
#                                 `python` was previously hardcoded, which
#                                 on this box resolves to 3.9 (no
#                                 claude_agent_sdk installed) instead of
#                                 the 3.11 production hooks actually use
#                                 -- any test exercising the real SDK
#                                 spawn-guard path silently no-op'd via
#                                 ImportError instead of running against
#                                 the production interpreter.
#
# Issue #143: a killed/errored file must never be silently dropped from
# the totals with a green exit. See the EXIT_CODE capture below for why
# `OUTPUT=$(timeout ...) || true` was broken (it always reported 0).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Parse arguments
QUICK=false
TB_FLAG="--tb=no"
for arg in "$@"; do
    case "$arg" in
        --quick) QUICK=true ;;
        --tb) TB_FLAG="--tb=short" ;;
    esac
done

# Timeout per test file (seconds), for tests/*.py and tests/unit/*.py.
# Issue #143: configurable via env var, default raised from 30 to 120 --
# on a loaded box, files that pass fine individually (e.g.
# test_issue_93_danger_bash_anchor.py at 103s,
# test_post_tool_use_subagent_context.py at 47s) were exceeding the old
# 30s cap and getting silently dropped from the totals (see the EXIT_CODE
# fix below -- this is a SEPARATE, compounding cause of the same symptom).
TIMEOUT="${PACEMAKER_TEST_TIMEOUT:-120}"

# Separate, more generous budgets for tests/e2e/*.py (issue #144
# code-review follow-up #1). These files run real subprocesses
# (install.sh does a real `pip install --user` per fresh HOME, ~20-40s;
# bootstrap-plugin.sh does a real venv + pip install, similar cost) and
# were measured well over the fast-suite's 120s/15s caps even after
# fixture-sharing speedups (e.g. test_install_old_bloated.py at ~837s
# for 34 tests before the site-packages-seeding speedup below).
E2E_TIMEOUT="${PACEMAKER_E2E_TEST_TIMEOUT:-900}"
E2E_PYTEST_TIMEOUT="${PACEMAKER_E2E_PYTEST_TIMEOUT:-90}"

# Resolve which Python interpreter to run pytest with (issue #144
# code-review follow-up #3). Candidate order, highest priority first:
#   1. PACEMAKER_TEST_PYTHON  -- explicit override.
#   2. $VIRTUAL_ENV/bin/python -- an ACTIVE virtualenv must win over the
#      system interpreters below it. The original version ignored
#      VIRTUAL_ENV entirely, so running inside e.g. a 3.12 venv silently
#      picked the SYSTEM python3.11 instead.
#   3. python / python3.11 / python3.10 / python3 -- existence-only
#      fallback order, mirrors src/hooks/*.sh's find_python().
#
# A candidate is only ACCEPTED if BOTH claude_agent_sdk AND pytest import
# successfully in it -- the original version only checked for
# claude_agent_sdk, so an interpreter that had the SDK but not pytest
# (e.g. a stray system python3.11) was silently selected and every test
# file then exited non-zero into the errored bucket with no explanation.
# If no candidate has both:
#   - fall back to the first candidate that has at least pytest (tests
#     can still run; SDK spawn-guard tests just won't exercise the real
#     SDK path) and print a loud warning to stderr, never silently;
#   - if NONE has pytest either, fall back to the first existing
#     candidate and warn even louder -- every file is about to fail to
#     collect, but see the missing-dependency hint printed per-file
#     below (issue #144 code-review follow-up #4) for how to fix it.
resolve_test_python() {
    local candidates=()
    if [ -n "${PACEMAKER_TEST_PYTHON:-}" ]; then
        candidates+=("$PACEMAKER_TEST_PYTHON")
        # An explicit override that fails the both-imports check below
        # still falls through to auto-detection (same rule as every
        # other candidate) -- but silently discarding a user's explicit
        # choice with zero explanation is its own footgun, so warn here
        # specifically, before the unified loop, rather than leaving the
        # user to infer it from which interpreter ends up printed.
        if ! command -v "$PACEMAKER_TEST_PYTHON" >/dev/null 2>&1 || \
           ! "$PACEMAKER_TEST_PYTHON" -c "import claude_agent_sdk, pytest" >/dev/null 2>&1; then
            echo "WARNING: PACEMAKER_TEST_PYTHON='$PACEMAKER_TEST_PYTHON' is not usable (missing, or missing claude_agent_sdk/pytest); falling back to auto-detection." >&2
        fi
    fi
    if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
        candidates+=("$VIRTUAL_ENV/bin/python")
    fi
    candidates+=(python python3.11 python3.10 python3)

    local py
    for py in "${candidates[@]}"; do
        if command -v "$py" >/dev/null 2>&1; then
            if "$py" -c "import claude_agent_sdk, pytest" >/dev/null 2>&1; then
                echo "$py"
                return 0
            fi
        fi
    done

    for py in "${candidates[@]}"; do
        if command -v "$py" >/dev/null 2>&1; then
            if "$py" -c "import pytest" >/dev/null 2>&1; then
                echo "WARNING: no candidate interpreter has claude_agent_sdk installed; using '$py' (has pytest, missing claude_agent_sdk). Tests exercising the real SDK spawn-guard path will not run against the production interpreter. Install claude_agent_sdk on this interpreter, or set PACEMAKER_TEST_PYTHON to one that has it." >&2
                echo "$py"
                return 0
            fi
        fi
    done

    for py in "${candidates[@]}"; do
        if command -v "$py" >/dev/null 2>&1; then
            echo "WARNING: no candidate interpreter has pytest installed; using '$py' anyway -- every test file is about to fail to collect. Install pytest (see requirements-dev.txt) on this interpreter, or set PACEMAKER_TEST_PYTHON to one that has it." >&2
            echo "$py"
            return 0
        fi
    done

    echo "python3"
}
TEST_PYTHON="$(resolve_test_python)"

# Scans a file's captured pytest output for a missing-module import error
# and, if found, prints a hint naming the module and pointing at
# requirements-dev.txt (issue #144 code-review follow-up #4). Silent
# no-op when the output contains no such error -- most ERRORED files are
# fixture/setup failures unrelated to a missing dependency, and this
# must never print a misleading hint for those.
print_missing_import_hint() {
    local output="$1"
    local missing
    missing=$(echo "$output" | grep -oP "(?<=ModuleNotFoundError: No module named ')[^']+" | head -1)
    if [ -n "$missing" ]; then
        echo -e "    ${YELLOW}hint: missing module '${missing}' -- try: pip install -r requirements-dev.txt${NC}"
        return 0
    fi
    if echo "$output" | grep -q "^ImportError:"; then
        echo -e "    ${YELLOW}hint: ImportError during collection -- check requirements-dev.txt / requirements.txt are installed on $TEST_PYTHON${NC}"
    fi
}

# Counters
TOTAL_PASSED=0
TOTAL_FAILED=0
TOTAL_SKIPPED=0
TOTAL_ERRORED=0    # sum of pytest's own "N error(s)" counts (fixture/setup errors)
TOTAL_XFAILED=0
TOTAL_XPASSED=0
TOTAL_ERRORS=0      # count of FILES landing in TIMED_OUT/KILLED/ERRORED_FILES
FAILED_FILES=()
TIMED_OUT_FILES=()
KILLED_FILES=()
ERRORED_FILES=()

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Collect test files
TEST_FILES=()
for f in tests/test_*.py; do
    [ -f "$f" ] && TEST_FILES+=("$f")
done
for f in tests/unit/test_*.py; do
    [ -f "$f" ] && TEST_FILES+=("$f")
done
if [ "$QUICK" = false ]; then
    for f in tests/e2e/test_*.py; do
        [ -f "$f" ] && TEST_FILES+=("$f")
    done
fi

TOTAL_FILES=${#TEST_FILES[@]}
echo "Using $TEST_PYTHON for tests"
echo "Running $TOTAL_FILES test files independently"
echo "  tests/*.py, tests/unit/*.py : timeout=${TIMEOUT}s/file, 15s/test"
if [ "$QUICK" = false ]; then
    echo "  tests/e2e/*.py              : timeout=${E2E_TIMEOUT}s/file, ${E2E_PYTEST_TIMEOUT}s/test"
fi
echo ""

START_TIME=$(date +%s)

for f in "${TEST_FILES[@]}"; do
    BASENAME=$(basename "$f")
    printf "  %-55s " "$BASENAME"

    # tests/e2e/*.py gets its own, more generous budget (issue #144
    # code-review follow-up #1) -- see the E2E_TIMEOUT/E2E_PYTEST_TIMEOUT
    # comment near the top of this file.
    case "$f" in
        tests/e2e/*)
            FILE_TIMEOUT="$E2E_TIMEOUT"
            PYTEST_INNER_TIMEOUT="$E2E_PYTEST_TIMEOUT"
            ;;
        *)
            FILE_TIMEOUT="$TIMEOUT"
            PYTEST_INNER_TIMEOUT=15
            ;;
    esac

    # Run with timeout, capture output AND the real exit code.
    #
    # Issue #143: the previous form --
    #   OUTPUT=$(timeout "$TIMEOUT" ...) || true
    #   EXIT_CODE=${PIPESTATUS[0]:-$?}
    # -- always reported EXIT_CODE=0. `OUTPUT=$(...) || true` is a command
    # LIST (`A || B`), not a pipeline; when the command substitution's exit
    # status was non-zero, `true` ran as the fallback and became the most
    # recently executed pipeline, so PIPESTATUS[0] read back as `true`'s
    # own exit status (0) -- never the real exit code of the timed-out or
    # crashed pytest process. Proof: `bash -c 'set -euo pipefail; OUTPUT=$(timeout 1 sleep 3) || true; echo ${PIPESTATUS[0]}'` prints 0.
    # Fixed by disabling errexit around the substitution and reading `$?`
    # directly, immediately after the command -- no pipe, no `|| true`.
    set +e
    OUTPUT=$(timeout "$FILE_TIMEOUT" "$TEST_PYTHON" -m pytest "$f" -q --timeout="$PYTEST_INNER_TIMEOUT" "$TB_FLAG" 2>&1)
    EXIT_CODE=$?
    set -e

    # Re-review (issue #143, round 2): exit-code classification, in order:
    #   124            -> TIMEOUT (GNU `timeout` killed via its own SIGTERM
    #                     after the duration elapsed)
    #   >128           -> KILLED (signal N-128) -- the process was killed by
    #                     a SIGNAL, not by `timeout`'s own deadline (e.g. an
    #                     external OOM killer, or the test process itself
    #                     self-terminating via SIGKILL/SIGTERM). 137 (SIGKILL)
    #                     and 143 (SIGTERM) are both covered by this general
    #                     ">128" check rather than hardcoding just those two.
    #   5              -> "no tests collected" -- pytest's OWN exit code for
    #                     zero matching tests. Benign, NOT an error (a file
    #                     with no test_* functions is not a bug in the suite
    #                     runner). Never added to any *_FILES list, never
    #                     counted in TOTAL_ERRORS.
    #   anything else  -> parse counts and classify below. A non-zero exit
    #                     here (1/2/3/4) is ALWAYS a real problem even if a
    #                     "N passed" line also appears (issue #143 round 2,
    #                     HIGH finding: "1 passed, 1 error" was pytest exit 1
    #                     but had zero "failed" text, so the old check
    #                     `EXIT_CODE != 0 && PASSED==0 && FAILED==0` never
    #                     fired for it and it was silently reported green).
    if [ "$EXIT_CODE" -eq 124 ]; then
        printf "${RED}TIMEOUT${NC}\n"
        TIMED_OUT_FILES+=("$f")
        TOTAL_ERRORS=$((TOTAL_ERRORS + 1))
        if [ "$TB_FLAG" = "--tb=short" ]; then
            echo "$OUTPUT" | tail -20
            echo ""
        fi
        continue
    fi

    if [ "$EXIT_CODE" -gt 128 ]; then
        SIGNAL_NUM=$((EXIT_CODE - 128))
        printf "${RED}KILLED (signal ${SIGNAL_NUM})${NC}\n"
        KILLED_FILES+=("$f")
        TOTAL_ERRORS=$((TOTAL_ERRORS + 1))
        if [ "$TB_FLAG" = "--tb=short" ]; then
            echo "$OUTPUT" | tail -20
            echo ""
        fi
        continue
    fi

    if [ "$EXIT_CODE" -eq 5 ]; then
        printf "${YELLOW}no tests collected${NC}\n"
        continue
    fi

    # Parse results from last line. pytest's own summary line can contain
    # any subset of: passed, failed, skipped, error(s), xfailed, xpassed.
    LAST_LINE=$(echo "$OUTPUT" | tail -1)

    PASSED=$(echo "$LAST_LINE" | grep -oP '\d+(?= passed)' || echo "0")
    FAILED=$(echo "$LAST_LINE" | grep -oP '\d+(?= failed)' || echo "0")
    SKIPPED=$(echo "$LAST_LINE" | grep -oP '\d+(?= skipped)' || echo "0")
    ERRORED=$(echo "$LAST_LINE" | grep -oP '\d+(?= errors?)' || echo "0")
    XFAILED=$(echo "$LAST_LINE" | grep -oP '\d+(?= xfailed)' || echo "0")
    XPASSED=$(echo "$LAST_LINE" | grep -oP '\d+(?= xpassed)' || echo "0")

    [ -z "$PASSED" ] && PASSED=0
    [ -z "$FAILED" ] && FAILED=0
    [ -z "$SKIPPED" ] && SKIPPED=0
    [ -z "$ERRORED" ] && ERRORED=0
    [ -z "$XFAILED" ] && XFAILED=0
    [ -z "$XPASSED" ] && XPASSED=0

    TOTAL_PASSED=$((TOTAL_PASSED + PASSED))
    TOTAL_FAILED=$((TOTAL_FAILED + FAILED))
    TOTAL_SKIPPED=$((TOTAL_SKIPPED + SKIPPED))
    TOTAL_ERRORED=$((TOTAL_ERRORED + ERRORED))
    TOTAL_XFAILED=$((TOTAL_XFAILED + XFAILED))
    TOTAL_XPASSED=$((TOTAL_XPASSED + XPASSED))

    if [ "$FAILED" -gt 0 ]; then
        # Genuine test failures -- unchanged bucket/behavior from before.
        MSG="${PASSED} passed, ${FAILED} failed"
        [ "$ERRORED" -gt 0 ] && MSG="${MSG}, ${ERRORED} errored"
        printf "${RED}${MSG}${NC}\n"
        FAILED_FILES+=("$f")
        if [ "$TB_FLAG" = "--tb=short" ]; then
            echo "$OUTPUT" | grep -A5 "^FAILED\|^E " || true
            echo ""
        fi
    elif [ "$ERRORED" -gt 0 ]; then
        # pytest "error" (fixture/setup/teardown failure) with zero test
        # FAILURES -- this is the exact "1 passed, 1 error" HIGH finding.
        # A non-zero EXIT_CODE reaching this branch (guaranteed by the
        # elif below never firing on EXIT_CODE!=0) is always real.
        printf "${RED}${PASSED} passed, ${ERRORED} errored${NC}\n"
        ERRORED_FILES+=("$f")
        TOTAL_ERRORS=$((TOTAL_ERRORS + 1))
        print_missing_import_hint "$OUTPUT"
        if [ "$TB_FLAG" = "--tb=short" ]; then
            echo "$OUTPUT" | tail -20
            echo ""
        fi
    elif [ "$EXIT_CODE" -ne 0 ]; then
        # Non-zero exit (collection error, import error, internal pytest
        # error, usage error, ...) with NO parseable failed/errored counts
        # at all -- must never be silently treated as "0 passed, 0 failed"
        # and dropped from the totals.
        printf "${RED}ERROR (exit ${EXIT_CODE})${NC}\n"
        ERRORED_FILES+=("$f")
        TOTAL_ERRORS=$((TOTAL_ERRORS + 1))
        print_missing_import_hint "$OUTPUT"
        if [ "$TB_FLAG" = "--tb=short" ]; then
            echo "$OUTPUT" | tail -20
            echo ""
        fi
    elif [ "$PASSED" -gt 0 ] || [ "$SKIPPED" -gt 0 ] || [ "$XFAILED" -gt 0 ] || [ "$XPASSED" -gt 0 ]; then
        # Clean exit (0) with at least one real outcome. Issue #143 round 2
        # point 4: xfailed/xpassed-only files (exit 0, e.g. "1 xpassed in
        # 0.01s") were previously mislabeled "no tests collected" because
        # the old PASSED regex never matched inside "xpassed"/"xfailed".
        MSG="${PASSED} passed"
        [ "$SKIPPED" -gt 0 ] && MSG="${MSG}, ${SKIPPED} skipped"
        [ "$XFAILED" -gt 0 ] && MSG="${MSG}, ${XFAILED} xfailed"
        [ "$XPASSED" -gt 0 ] && MSG="${MSG}, ${XPASSED} xpassed"
        printf "${GREEN}${MSG}${NC}\n"
    else
        # EXIT_CODE==0 and every count is zero -- genuinely nothing to
        # report (should be rare; pytest normally uses exit 5 for this,
        # handled above). Benign, not an error.
        printf "${YELLOW}no tests collected${NC}\n"
    fi
done

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo ""
echo "================================================================="
echo "TOTAL: ${TOTAL_PASSED} passed, ${TOTAL_FAILED} failed, ${TOTAL_ERRORED} errored, ${TOTAL_SKIPPED} skipped, ${TOTAL_XFAILED} xfailed, ${TOTAL_XPASSED} xpassed, ${TOTAL_ERRORS} files timed-out/killed/errored in ${ELAPSED}s"

if [ ${#TIMED_OUT_FILES[@]} -gt 0 ]; then
    echo ""
    echo -e "${RED}TIMED OUT (${#TIMED_OUT_FILES[@]} files, exceeded ${TIMEOUT}s for tests/*.py and tests/unit/*.py"
    echo -e "or ${E2E_TIMEOUT}s for tests/e2e/*.py -- set PACEMAKER_TEST_TIMEOUT / PACEMAKER_E2E_TEST_TIMEOUT to raise):${NC}"
    for f in "${TIMED_OUT_FILES[@]}"; do
        echo "  - $f"
    done
fi

if [ ${#KILLED_FILES[@]} -gt 0 ]; then
    echo ""
    echo -e "${RED}KILLED (${#KILLED_FILES[@]} files -- terminated by a signal, not by the"
    echo -e "${TIMEOUT}s deadline; e.g. OOM killer or the process self-terminating):${NC}"
    for f in "${KILLED_FILES[@]}"; do
        echo "  - $f"
    done
fi

if [ ${#ERRORED_FILES[@]} -gt 0 ]; then
    echo ""
    echo -e "${RED}ERRORED (${#ERRORED_FILES[@]} files -- pytest fixture/setup errors, import"
    echo -e "error, collection error, internal pytest error, or usage error):${NC}"
    for f in "${ERRORED_FILES[@]}"; do
        echo "  - $f"
    done
fi

if [ ${#FAILED_FILES[@]} -gt 0 ]; then
    echo ""
    echo -e "${RED}FAILED FILES (${#FAILED_FILES[@]}):${NC}"
    for f in "${FAILED_FILES[@]}"; do
        echo "  - $f"
    done
fi

if [ "$TOTAL_FAILED" -eq 0 ] \
    && [ ${#TIMED_OUT_FILES[@]} -eq 0 ] \
    && [ ${#KILLED_FILES[@]} -eq 0 ] \
    && [ ${#ERRORED_FILES[@]} -eq 0 ]; then
    echo ""
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
else
    exit 1
fi
