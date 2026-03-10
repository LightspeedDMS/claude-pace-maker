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

# Timeout per test file (seconds)
TIMEOUT=30

# Counters
TOTAL_PASSED=0
TOTAL_FAILED=0
TOTAL_SKIPPED=0
TOTAL_ERRORS=0
FAILED_FILES=()
TIMED_OUT_FILES=()

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

    # Run with timeout, capture output
    OUTPUT=$(timeout "$TIMEOUT" python -m pytest "$f" -q --timeout=15 "$TB_FLAG" 2>&1) || true
    EXIT_CODE=${PIPESTATUS[0]:-$?}

    # Check for timeout (exit code 124 from timeout command)
    if [ $EXIT_CODE -eq 124 ] || [ $EXIT_CODE -eq 143 ]; then
        printf "${RED}TIMEOUT${NC}\n"
        TIMED_OUT_FILES+=("$f")
        TOTAL_ERRORS=$((TOTAL_ERRORS + 1))
        continue
    fi

    # Parse results from last line
    LAST_LINE=$(echo "$OUTPUT" | tail -1)

    # Extract passed/failed/skipped counts
    PASSED=$(echo "$LAST_LINE" | grep -oP '\d+ passed' | grep -oP '\d+' || echo "0")
    FAILED=$(echo "$LAST_LINE" | grep -oP '\d+ failed' | grep -oP '\d+' || echo "0")
    SKIPPED=$(echo "$LAST_LINE" | grep -oP '\d+ skipped' | grep -oP '\d+' || echo "0")

    [ -z "$PASSED" ] && PASSED=0
    [ -z "$FAILED" ] && FAILED=0
    [ -z "$SKIPPED" ] && SKIPPED=0

    TOTAL_PASSED=$((TOTAL_PASSED + PASSED))
    TOTAL_FAILED=$((TOTAL_FAILED + FAILED))
    TOTAL_SKIPPED=$((TOTAL_SKIPPED + SKIPPED))

    if [ "$FAILED" -gt 0 ]; then
        printf "${RED}${PASSED} passed, ${FAILED} failed${NC}\n"
        FAILED_FILES+=("$f")
        # Show tracebacks if requested
        if [ "$TB_FLAG" = "--tb=short" ]; then
            echo "$OUTPUT" | grep -A5 "^FAILED\|^E " || true
            echo ""
        fi
    elif [ "$PASSED" -gt 0 ]; then
        printf "${GREEN}${PASSED} passed${NC}"
        [ "$SKIPPED" -gt 0 ] && printf ", ${YELLOW}${SKIPPED} skipped${NC}"
        printf "\n"
    else
        printf "${YELLOW}no tests collected${NC}\n"
    fi
done

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo ""
echo "================================================================="
echo "TOTAL: ${TOTAL_PASSED} passed, ${TOTAL_FAILED} failed, ${TOTAL_SKIPPED} skipped in ${ELAPSED}s"

if [ ${#TIMED_OUT_FILES[@]} -gt 0 ]; then
    echo ""
    echo -e "${RED}TIMED OUT (${#TIMED_OUT_FILES[@]} files):${NC}"
    for f in "${TIMED_OUT_FILES[@]}"; do
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

if [ "$TOTAL_FAILED" -eq 0 ] && [ ${#TIMED_OUT_FILES[@]} -eq 0 ]; then
    echo ""
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
else
    exit 1
fi
