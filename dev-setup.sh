#!/bin/bash
#
# Development environment setup script for claude-pace-maker
# Run this after freshly cloning the repository
#

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Setting up claude-pace-maker development environment..."
echo ""

# Check Python version
echo "Checking Python version..."
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo -e "${GREEN}✓ Python $PYTHON_VERSION${NC}"
echo ""

# Install development dependencies
echo "Installing development dependencies..."
if pip3 install --user black ruff mypy pytest pytest-cov 2>&1 | tail -1; then
    echo -e "${GREEN}✓ Development dependencies installed${NC}"
else
    echo -e "${RED}✗ Failed to install dependencies${NC}"
    exit 1
fi
echo ""

# Install pre-commit hook
echo "Installing pre-commit hook..."
if [ -f ".git/hooks/pre-commit" ]; then
    echo -e "${YELLOW}⚠ Pre-commit hook already exists, backing up...${NC}"
    mv .git/hooks/pre-commit .git/hooks/pre-commit.backup
fi

cp lint.sh .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
echo -e "${GREEN}✓ Pre-commit hook installed${NC}"
echo ""

# Make lint.sh executable
chmod +x lint.sh
echo -e "${GREEN}✓ Made lint.sh executable${NC}"
echo ""

# Run tests to verify setup
echo "Running tests to verify setup..."
if python3 -m pytest tests/ -q 2>&1 | tail -5; then
    echo -e "${GREEN}✓ Tests passing${NC}"
else
    echo -e "${YELLOW}⚠ Some tests may be failing (this is OK for initial setup)${NC}"
fi
echo ""

# Run linters to check code quality
echo "Running linters..."
if ./lint.sh; then
    echo -e "${GREEN}✓ All linters passed${NC}"
else
    echo -e "${YELLOW}⚠ Linting issues found - run './lint.sh' to see details${NC}"
fi
echo ""

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Development environment setup complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Next steps:"
echo "  1. Run tests:    python3 -m pytest tests/"
echo "  2. Run linters:  ./lint.sh"
echo "  3. Install:      ./install.sh"
echo "  4. Check status: pace-maker status"
echo ""
