#!/bin/bash
#
# Linting script for claude-pace-maker
# Runs mypy, black, and ruff on all Python code
#

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Running Python linters..."
echo ""

ERRORS=0

# Run black (code formatter)
echo -e "${YELLOW}Running black (code formatter)...${NC}"
if black --check src/ tests/ 2>&1; then
    echo -e "${GREEN}✓ Black: All files formatted correctly${NC}"
else
    echo -e "${RED}✗ Black: Formatting issues found${NC}"
    echo "  Run: black src/ tests/ to fix"
    ((ERRORS++))
fi
echo ""

# Run ruff (fast linter)
echo -e "${YELLOW}Running ruff (linter)...${NC}"
if ruff check src/ tests/ 2>&1; then
    echo -e "${GREEN}✓ Ruff: No linting errors${NC}"
else
    echo -e "${RED}✗ Ruff: Linting errors found${NC}"
    echo "  Run: ruff check --fix src/ tests/ to auto-fix"
    ((ERRORS++))
fi
echo ""

# Run mypy (type checker)
echo -e "${YELLOW}Running mypy (type checker)...${NC}"
if mypy src/ --ignore-missing-imports --no-strict-optional 2>&1; then
    echo -e "${GREEN}✓ Mypy: No type errors${NC}"
else
    echo -e "${RED}✗ Mypy: Type errors found${NC}"
    ((ERRORS++))
fi
echo ""

# Summary
if [ $ERRORS -eq 0 ]; then
    echo -e "${GREEN}✓ All linters passed${NC}"
    exit 0
else
    echo -e "${RED}✗ $ERRORS linter(s) failed${NC}"
    exit 1
fi
