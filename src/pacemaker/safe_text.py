"""Sanitizers for text that originates in a shared database (story #187).

Rows in the shared ``session_registry.db`` can be written by another OS user,
so every field read from it is untrusted before it is shown to Claude (nudge
banners) or to a terminal (``pace-maker status`` / ``sessions``).
"""

import math
import re
import unicodedata
from typing import Any, Optional

DEFAULT_MAX_FIELD_CHARS = 120

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
_LINE_BREAKERS = {"\n", "\r", "\t", "\u2028", "\u2029", "\x0b", "\x0c", "\x85"}


def sanitize_field(value: Any, max_len: Optional[int] = DEFAULT_MAX_FIELD_CHARS) -> str:
    """Single-line, control-free, length-bounded text.

    Line breaks and tabs become a space; every other control/format/surrogate/
    private-use/unassigned character (ESC, BEL, C1 CSI, bidi marks...) is
    removed, which defuses terminal escape sequences and newline-based prompt
    injection. ``None`` becomes the empty string. ``max_len=None`` means no
    length cap (default installs keep full-length paths).
    """
    if value is None:
        return ""
    out = []
    for ch in str(value):
        if ch in _LINE_BREAKERS:
            out.append(" ")
        elif unicodedata.category(ch).startswith("C"):
            continue
        else:
            out.append(ch)
    text = "".join(out)
    return text if max_len is None else text[:max_len]


def is_uuid_shaped(value: Any) -> bool:
    """True only for a canonical 8-4-4-4-12 hex UUID string."""
    return isinstance(value, str) and _UUID_RE.match(value) is not None


def is_plain_int(value: Any) -> bool:
    """True for a real ``int`` (not bool, float or numeric-looking text).

    SQLite's INTEGER affinity happily stores arbitrary TEXT, so a column read
    from a shared database must be type-checked before it is rendered.
    """
    return type(value) is int


def is_real_number(value: Any) -> bool:
    """True for a finite ``int``/``float`` (not bool, text, bytes, NaN or inf)."""
    if type(value) is int:
        return True
    return type(value) is float and math.isfinite(value)
