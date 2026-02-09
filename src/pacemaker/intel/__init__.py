#!/usr/bin/env python3
"""
Intel module for parsing prompt intelligence metadata from assistant responses.

Provides functions to extract and process § intel lines containing:
- Frustration level (△)
- Specificity (◎)
- Task type (■)
- Quality (◇)
- Iteration count (↻)
"""

from .parser import parse_intel_line, strip_intel_line

__all__ = ["parse_intel_line", "strip_intel_line"]
