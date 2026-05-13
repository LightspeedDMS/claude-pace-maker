"""
Claude Code version detection and comparison utilities.

Public API:
- ClaudeCodeVersion — dataclass with major, minor, patch, raw fields
    .parse(version_output) -> ClaudeCodeVersion | None
    .compare(other) -> int    (negative / 0 / positive, semver)
    .is_below(minimum) -> bool
- probe_installed_version() -> ClaudeCodeVersion | None
    Run 'claude --version', parse, return None on any failure (fail-open).
"""

import re
import subprocess
from dataclasses import dataclass
from typing import Optional

from .logger import log_warning

# Regex: first whitespace-separated token must match X.Y.Z optionally followed
# by a pre-release or build-metadata suffix (everything after the third digit).
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")

# Timeout for the 'claude --version' subprocess call.
# Specified as 5 seconds in Story #66 Component 1.
_PROBE_TIMEOUT_SEC = 5


@dataclass
class ClaudeCodeVersion:
    """Parsed representation of a Claude Code semver version string."""

    major: int
    minor: int
    patch: int
    raw: str

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    # ── factory ───────────────────────────────────────────────────────────────

    @classmethod
    def parse(cls, version_output: Optional[str]) -> Optional["ClaudeCodeVersion"]:
        """Extract a ClaudeCodeVersion from 'claude --version' output.

        Extracts the first whitespace-separated token and matches it against
        the semver pattern X.Y.Z (with optional pre-release/build suffix).

        Returns None for empty, null, or unparseable input.
        """
        if not version_output or not version_output.strip():
            return None

        token = version_output.strip().split()[0]
        m = _VERSION_RE.match(token)
        if m is None:
            return None

        return cls(
            major=int(m.group(1)),
            minor=int(m.group(2)),
            patch=int(m.group(3)),
            raw=version_output,
        )

    # ── comparison ────────────────────────────────────────────────────────────

    def compare(self, other: "ClaudeCodeVersion") -> int:
        """Semver tuple comparison.

        Returns:
            negative int  — self < other
            0             — self == other
            positive int  — self > other
        """
        self_tuple = (self.major, self.minor, self.patch)
        other_tuple = (other.major, other.minor, other.patch)
        if self_tuple < other_tuple:
            return -1
        if self_tuple > other_tuple:
            return 1
        return 0

    def is_below(self, minimum: "ClaudeCodeVersion") -> bool:
        """Return True when self is strictly below the given minimum version."""
        return self.compare(minimum) < 0


# ── probe ─────────────────────────────────────────────────────────────────────


def probe_installed_version() -> Optional[ClaudeCodeVersion]:
    """Run 'claude --version' and return a parsed ClaudeCodeVersion.

    Returns None on any failure:
    - Binary not found (FileNotFoundError)
    - Subprocess timeout (TimeoutExpired)
    - Non-zero exit code
    - Unparseable output

    This is a fail-open function: callers must treat None as "version unknown"
    and proceed normally rather than blocking.
    """
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SEC,
        )
        if result.returncode != 0:
            log_warning(
                "claude_code_version",
                f"'claude --version' exited with code {result.returncode}",
            )
            return None
        parsed = ClaudeCodeVersion.parse(result.stdout)
        if parsed is None:
            log_warning(
                "claude_code_version",
                f"Could not parse 'claude --version' output: {result.stdout!r}",
            )
        return parsed
    except FileNotFoundError:
        log_warning("claude_code_version", "'claude' binary not found in PATH")
        return None
    except subprocess.TimeoutExpired:
        log_warning(
            "claude_code_version",
            f"'claude --version' timed out after {_PROBE_TIMEOUT_SEC}s",
        )
        return None
    except OSError as e:
        log_warning("claude_code_version", f"OS error probing 'claude --version': {e}")
        return None
