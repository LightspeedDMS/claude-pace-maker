#!/usr/bin/env python3
"""
Langfuse data filtering module.

Provides truncation and secret redaction for tool results before
submission to Langfuse API.
"""

import re
from typing import Optional


# Secret patterns to redact (AC2 specification)
SECRET_PATTERNS = [
    # OpenAI/Anthropic API keys (sk- or sk-ant- prefix)
    (r"sk-[a-zA-Z0-9-]{20,}", "[REDACTED]"),
    # AWS access keys
    (r"AKIA[A-Z0-9]{16}", "[REDACTED]"),
    # Slack tokens
    (r"xoxb-[a-zA-Z0-9-]+", "[REDACTED]"),
    # Bearer tokens
    (r"Bearer [a-zA-Z0-9._-]+", "[REDACTED]"),
    # Private keys
    (r"-----BEGIN.*PRIVATE KEY-----", "[REDACTED]"),
    # Password patterns
    (r'password[=:]\s*[\'"]?([^\s\'"]+)', "password=[REDACTED]"),
    # Generic API keys
    (r'api[_-]?key[=:]\s*[\'"]?([a-zA-Z0-9-]+)', "api_key=[REDACTED]"),
    # GitHub Personal Access Tokens
    (r"ghp_[a-zA-Z0-9]{36}", "[REDACTED]"),
    # GitHub Server Tokens
    (r"ghs_[a-zA-Z0-9]{36}", "[REDACTED]"),
    # GitLab Personal Access Tokens
    (r"glpat-[a-zA-Z0-9-]{20,}", "[REDACTED]"),
]


def truncate_output(output: str, max_bytes: int = 10240) -> str:
    """
    Truncate tool output if it exceeds threshold (AC1).

    Args:
        output: Tool output string
        max_bytes: Maximum size in bytes (default: 10KB = 10240 bytes)

    Returns:
        Truncated output with marker if exceeded, original if within limit

    Raises:
        TypeError: If output is None
    """
    if output is None:
        raise TypeError("Output cannot be None")

    # Calculate byte size
    output_bytes = output.encode("utf-8")
    original_size = len(output_bytes)

    # Return unchanged if within limit
    if original_size <= max_bytes:
        return output

    # Truncate to max_bytes, handling UTF-8 boundary
    truncated_bytes = output_bytes[:max_bytes]

    # Decode and handle potential UTF-8 boundary issues
    # Try progressively smaller truncations until valid UTF-8
    for i in range(4):  # UTF-8 chars are max 4 bytes
        try:
            truncated_text = truncated_bytes[: max_bytes - i].decode("utf-8")
            break
        except UnicodeDecodeError:
            continue
    else:
        # Fallback: force decode with replacement
        truncated_text = truncated_bytes[:max_bytes].decode("utf-8", errors="ignore")

    # Append truncation marker
    marker = f"\n\n[TRUNCATED - original size: {original_size} bytes]"
    return truncated_text + marker


def redact_secrets(text: str, enabled: bool = True) -> str:
    """
    Redact secret patterns from text (AC2).

    Replaces API keys, passwords, tokens, and private keys with [REDACTED].

    Args:
        text: Input text to redact
        enabled: Whether redaction is enabled (default: True)

    Returns:
        Text with secrets replaced by [REDACTED]
    """
    if not enabled:
        return text

    result = text

    # Apply each pattern replacement
    for pattern, replacement in SECRET_PATTERNS:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    return result


def filter_tool_result(
    output: str, max_bytes: Optional[int] = 10240, enable_redaction: bool = True
) -> str:
    """
    Apply both truncation and redaction to tool result.

    This is the main entry point for filtering tool outputs before
    sending to Langfuse.

    Args:
        output: Tool output string
        max_bytes: Maximum size in bytes (None = no truncation, default: 10KB)
        enable_redaction: Whether to redact secrets (default: True)

    Returns:
        Filtered output with truncation and redaction applied
    """
    result = output

    # Apply secret redaction first (so secrets are redacted even if truncated)
    if enable_redaction:
        result = redact_secrets(result, enabled=True)

    # Apply truncation
    if max_bytes is not None:
        result = truncate_output(result, max_bytes=max_bytes)

    return result
