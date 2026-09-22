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
#   PACEMAKER_TEST_TIMEOUT  Per-file timeout in seconds (default 120,
#                            issue #143 -- raised from the original 30s,
#                            which a normal file can exceed on a loaded
#                            box even when it passes when run alone).
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

# Timeout per test file (seconds). Issue #143: configurable via env var,
# default raised from 30 to 120 -- on a loaded box, files that pass fine
# individually (e.g. test_issue_93_danger_bash_anchor.py at 103s,
# test_post_tool_use_subagent_context.py at 47s) were exceeding the old
# 30s cap and getting silently dropped from the totals (see the EXIT_CODE
# fix below -- this is a SEPARATE, compounding cause of the same symptom).
TIMEOUT="${PACEMAKER_TEST_TIMEOUT:-120}"

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
echo "Running $TOTAL_FILES test files independently (timeout=${TIMEOUT}s each)..."
echo ""

START_TIME=$(date +%s)

for f in "${TEST_FILES[@]}"; do
    BASENAME=$(basename "$f")
    printf "  %-55s " "$BASENAME"

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
    OUTPUT=$(timeout "$TIMEOUT" python -m pytest "$f" -q --timeout=15 "$TB_FLAG" 2>&1)
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
    echo -e "${RED}TIMED OUT (${#TIMED_OUT_FILES[@]} files, exceeded ${TIMEOUT}s -- set PACEMAKER_TEST_TIMEOUT to raise):${NC}"
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
