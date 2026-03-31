"""
Intent Validation Sandbox - Safe test fixture for intent validation testing.

This file exists in src/ (core path) so it triggers full intent validation
including TDD checks. It can be safely modified by subagents during testing
without affecting any real functionality.
"""


def sandbox_function():
    """A placeholder function that can be safely modified for testing."""
    return "modified by subagent"
