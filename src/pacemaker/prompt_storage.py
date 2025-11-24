#!/usr/bin/env python3
"""
User prompt storage and slash command expansion for intent-based validation.

This module handles UserPromptSubmit hook logic:
- Captures user prompts (plain text or slash commands)
- Expands slash commands by reading command definition files
- Stores prompts as JSON for later validation by Stop hook
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


def is_slash_command(prompt: str) -> bool:
    """
    Detect if prompt is a slash command.

    Args:
        prompt: User prompt text

    Returns:
        True if prompt starts with '/', False otherwise
    """
    stripped = prompt.strip()
    # Must start with '/' but not '//' (which is a comment)
    return stripped.startswith("/") and not stripped.startswith("//")


def extract_command_name(prompt: str) -> str:
    """
    Extract command name from slash command prompt.

    Args:
        prompt: Slash command prompt (e.g., "/implement-epic user-auth")

    Returns:
        Command name (e.g., "implement-epic")
    """
    # Remove leading '/' and split on whitespace
    parts = prompt.strip()[1:].split()
    return parts[0] if parts else ""


def resolve_command_file(
    command_name: str,
    project_commands_dir: Optional[str] = None,
    global_commands_dir: Optional[str] = None,
) -> Optional[str]:
    """
    Resolve command file path with project-level precedence.

    Lookup order:
    1. Project-level: $CLAUDE_PROJECT_DIR/.claude/commands/[name].md
    2. Global-level: ~/.claude/commands/[name].md

    Args:
        command_name: Name of the command
        project_commands_dir: Project commands directory (optional)
        global_commands_dir: Global commands directory (optional)

    Returns:
        Path to command file if found, None otherwise
    """
    command_filename = f"{command_name}.md"

    # Check project level first
    if project_commands_dir:
        project_path = os.path.join(project_commands_dir, command_filename)
        if os.path.exists(project_path):
            return project_path

    # Fallback to global level
    if global_commands_dir:
        global_path = os.path.join(global_commands_dir, command_filename)
        if os.path.exists(global_path):
            return global_path

    # Not found
    return None


def expand_slash_command(
    prompt: str,
    project_commands_dir: Optional[str] = None,
    global_commands_dir: Optional[str] = None,
) -> str:
    """
    Expand slash command by reading command definition file.

    If command file is not found, returns original prompt (treat as plain text).

    Args:
        prompt: Slash command prompt
        project_commands_dir: Project commands directory (optional)
        global_commands_dir: Global commands directory (optional)

    Returns:
        Expanded command content from file, or original prompt if not found
    """
    if not is_slash_command(prompt):
        return prompt

    command_name = extract_command_name(prompt)
    if not command_name:
        return prompt

    # Resolve command file
    command_file = resolve_command_file(
        command_name, project_commands_dir, global_commands_dir
    )

    if command_file is None:
        # Command not found - treat as plain text
        return prompt

    # Read command file content
    try:
        with open(command_file, "r") as f:
            return f.read()
    except Exception:
        # Error reading file - treat as plain text
        return prompt


def convert_old_format_to_new(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert old single-prompt format to new multi-prompt format.

    Args:
        data: Prompt data dict (may be old or new format)

    Returns:
        Data dict in new format with 'prompts' array
    """
    if "raw_prompt" in data and "prompts" not in data:
        return {
            "session_id": data.get("session_id"),
            "prompts": [
                {
                    "raw_prompt": data.get("raw_prompt"),
                    "expanded_prompt": data.get("expanded_prompt"),
                    "timestamp": data.get("timestamp"),
                    "sequence": 1,
                }
            ],
        }
    return data


def store_user_prompt(
    session_id: str,
    raw_prompt: str,
    prompts_dir: str,
    project_commands_dir: Optional[str] = None,
    global_commands_dir: Optional[str] = None,
    max_prompts: int = 5,
) -> bool:
    """
    Store user prompt with optional slash command expansion.

    Creates JSON file: ~/.claude-pace-maker/prompts/[session_id].json

    JSON structure (NEW - rolling window of last N prompts):
    {
        "session_id": "sess-12345",
        "prompts": [
            {
                "raw_prompt": "build calculator",
                "expanded_prompt": "build calculator",
                "timestamp": "2025-11-23T12:00:00",
                "sequence": 1
            },
            {
                "raw_prompt": "add fractions support",
                "expanded_prompt": "add fractions support",
                "timestamp": "2025-11-23T12:01:30",
                "sequence": 2
            }
        ]
    }

    Args:
        session_id: Session ID
        raw_prompt: Raw user prompt (may be slash command)
        prompts_dir: Directory to store prompt files
        project_commands_dir: Project commands directory (optional)
        global_commands_dir: Global commands directory (optional)
        max_prompts: Maximum number of prompts to keep (rolling window). 0 = unlimited.

    Returns:
        True if stored successfully, False otherwise
    """
    try:
        # Ensure prompts directory exists
        Path(prompts_dir).mkdir(parents=True, exist_ok=True)

        prompt_file = os.path.join(prompts_dir, f"{session_id}.json")

        # Expand slash command if applicable
        expanded_prompt = expand_slash_command(
            raw_prompt, project_commands_dir, global_commands_dir
        )

        # Load existing prompts or initialize
        if os.path.exists(prompt_file):
            with open(prompt_file, "r") as f:
                data = json.load(f)
                # Handle old format (backwards compatibility)
                data = convert_old_format_to_new(data)
                existing_prompts = data.get("prompts", [])
        else:
            existing_prompts = []

        # Create new prompt entry
        # Sequence number is the highest existing sequence + 1 (tracks absolute session count)
        max_sequence = max([p.get("sequence", 0) for p in existing_prompts], default=0)
        new_prompt = {
            "raw_prompt": raw_prompt,
            "expanded_prompt": expanded_prompt,
            "timestamp": datetime.now().isoformat(),
            "sequence": max_sequence + 1,
        }

        # Append and maintain rolling window
        existing_prompts.append(new_prompt)
        if max_prompts > 0 and len(existing_prompts) > max_prompts:
            existing_prompts = existing_prompts[-max_prompts:]

        # Build data structure
        prompt_data = {
            "session_id": session_id,
            "prompts": existing_prompts,
        }

        # Write JSON file
        with open(prompt_file, "w") as f:
            json.dump(prompt_data, f, indent=2)

        return True

    except Exception as e:
        logger.debug(f"Failed to store user prompt: {e}")
        return False
