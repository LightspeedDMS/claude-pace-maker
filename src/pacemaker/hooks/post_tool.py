#!/usr/bin/env python3
"""
Post-tool hook - Mission Completion Reminder.

This hook runs after every tool execution to subtly remind Claude
about the mission completion protocol.
"""


def run_post_tool_hook() -> str:
    """
    Generate subtle mission completion reminder.

    Returns:
        Reminder string to be injected after tool use
    """
    reminder = "[Remember: Say 'Mission completed.' when ALL tasks are done]"
    return reminder
