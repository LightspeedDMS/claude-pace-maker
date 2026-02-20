"""
Secret declaration parser module.

Parses secret declarations from assistant responses and stores them in the database.

Patterns:
- Text: "🔐 SECRET_TEXT: <actual_value>"
- File: "🔐 SECRET_FILE: <path_or_value>"
"""

import os
import re
import logging
from typing import List, Dict, Any

from .database import create_secret

logger = logging.getLogger(__name__)


def parse_text_secret(response: str) -> List[str]:
    """
    Parse text secret declarations from a response.

    Pattern: 🔐 SECRET_TEXT: <value>
    The value extends to the end of the line.

    Args:
        response: The assistant's response text

    Returns:
        List of extracted secret values
    """
    pattern = r"🔐 SECRET_TEXT:\s*(.+?)(?:\n|$)"
    matches = re.findall(pattern, response)

    # Strip trailing whitespace AND common markdown characters (backticks, asterisks, underscores)
    cleaned = []
    for match in matches:
        value = match.strip().rstrip("`*_")  # Remove trailing markdown chars
        if value:  # Only add non-empty values
            cleaned.append(value)
    return cleaned


def parse_file_secret(response: str) -> List[str]:
    """
    Parse file secret declarations from a response.

    Pattern: 🔐 SECRET_FILE: <path_or_value>
    The value extends to the end of the line.

    If the value is a file path (starts with / or ~), attempts to read
    the file contents. If the file cannot be read, returns the original
    value (the path).

    Args:
        response: The assistant's response text

    Returns:
        List of extracted file secret contents (or file contents if path detected)
    """
    pattern = r"🔐 SECRET_FILE:\s*(.+?)(?:\n|$)"
    matches = re.findall(pattern, response)

    results = []
    for match in matches:
        content = match.strip().rstrip("`*_")
        if not content:
            continue

        # Check if this looks like a file path (starts with / or ~)
        if content.startswith("/") or content.startswith("~"):
            expanded_path = os.path.expanduser(content)
            try:
                if os.path.exists(expanded_path):
                    with open(expanded_path, "r") as f:
                        file_content = f.read()
                    results.append(file_content)
                else:
                    logger.warning(
                        f"File path in SECRET_FILE does not exist: {expanded_path}"
                    )
                    results.append(content)
            except PermissionError:
                logger.warning(f"Permission denied reading file: {expanded_path}")
                results.append(content)
            except Exception as e:
                logger.warning(f"Error reading file {expanded_path}: {e}")
                results.append(content)
        else:
            results.append(content)

    return results


def parse_assistant_response(response: str, db_path: str) -> List[Dict[str, Any]]:
    """
    Parse all secret declarations from assistant response and store in database.

    Extracts both text and file secrets, stores them in the database,
    and returns information about the stored secrets.

    Args:
        response: The assistant's response text
        db_path: Path to the secrets database

    Returns:
        List of dictionaries with 'id' and 'type' for each stored secret
    """
    results = []

    # Parse text secrets
    text_secrets = parse_text_secret(response)
    for secret_value in text_secrets:
        secret_id = create_secret(db_path, "text", secret_value)
        results.append({"id": secret_id, "type": "text"})

    # Parse file secrets
    file_secrets = parse_file_secret(response)
    for secret_value in file_secrets:
        secret_id = create_secret(db_path, "file", secret_value)
        results.append({"id": secret_id, "type": "file"})

    return results
