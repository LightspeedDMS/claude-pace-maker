#!/usr/bin/env python3
"""
Prompt loader for externalized prompts.

Loads prompt files from organized subfolders with template variable replacement
and fallback to legacy locations for backward compatibility.
"""

import json
import re
from pathlib import Path
from typing import Optional, Dict, Any

from .logger import log_warning


class PromptLoader:
    """Loads prompts from organized subfolders with fallback support."""

    def __init__(self, prompts_base_dir: Optional[str] = None):
        """
        Initialize PromptLoader.

        Args:
            prompts_base_dir: Base directory for prompts (default: src/pacemaker/prompts)
        """
        if prompts_base_dir:
            self.prompts_dir = Path(prompts_base_dir)
        else:
            # Default to prompts subdirectory in pacemaker module
            module_dir = Path(__file__).parent
            self.prompts_dir = module_dir / "prompts"

    def load_prompt(
        self,
        name: str,
        subfolder: Optional[str] = None,
        variables: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Load a prompt file with optional template variable replacement.

        Args:
            name: Prompt filename (e.g., "intent_validation_guidance.md")
            subfolder: Hook-type subfolder (e.g., "session_start")
            variables: Template variables to replace {{var}} placeholders

        Returns:
            Prompt content with variables replaced

        Raises:
            FileNotFoundError: If prompt not found in organized or legacy location
            ValueError: If template has unreplaced placeholders
        """
        # Try organized location first
        organized_path = None
        if subfolder:
            organized_path = self.prompts_dir / subfolder / name
            if organized_path.exists():
                return self._load_and_replace(organized_path, variables)

        # Fallback to legacy root location
        legacy_path = self.prompts_dir / name
        if legacy_path.exists():
            if subfolder:
                log_warning(
                    "prompt_loader",
                    f"Loading prompt from legacy location: {legacy_path}. "
                    f"Consider migrating to: prompts/{subfolder}/{name}",
                    None,
                )
            return self._load_and_replace(legacy_path, variables)

        # Not found anywhere
        paths_searched = []
        if organized_path:
            paths_searched.append(str(organized_path))
        paths_searched.append(str(legacy_path))

        raise FileNotFoundError(
            f"Prompt '{name}' not found.\n"
            f"Searched: {', '.join(paths_searched)}\n"
            f"Create the prompt file in: prompts/{subfolder}/{name}"
            if subfolder
            else str(legacy_path)
        )

    def _load_and_replace(self, path: Path, variables: Optional[Dict[str, str]]) -> str:
        """
        Load file and replace template variables.

        Args:
            path: Path to prompt file
            variables: Template variables to replace

        Returns:
            Content with variables replaced

        Raises:
            ValueError: If unreplaced placeholders remain
        """
        content = path.read_text(encoding="utf-8")

        # Replace variables if provided
        if variables:
            for key, value in variables.items():
                content = content.replace(f"{{{{{key}}}}}", value)

        # Check for unreplaced placeholders
        unreplaced = re.findall(r"\{\{(\w+)\}\}", content)
        if unreplaced:
            raise ValueError(f"Unreplaced placeholders in {path.name}: {unreplaced}")

        return content

    def load_json_messages(self, name: str, subfolder: str) -> Dict[str, Any]:
        """
        Load JSON message file.

        Args:
            name: Filename (e.g., "messages.json")
            subfolder: Subfolder (e.g., "user_commands")

        Returns:
            Parsed JSON content

        Raises:
            FileNotFoundError: If file not found
        """
        path = self.prompts_dir / subfolder / name
        if not path.exists():
            raise FileNotFoundError(f"Messages file not found: {path}")

        return json.loads(path.read_text(encoding="utf-8"))
