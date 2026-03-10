#!/usr/bin/env python3
"""
Install commands module for Pace Maker.

Handles 'pace-maker install <target>' commands, providing one-command
installation of companion tools like claude-usage-monitor.
"""

import sys
import shutil
import subprocess
from typing import Dict, Any, Optional


# Known installable targets
KNOWN_TARGETS = {"claude-usage-monitor"}

# Repository details for claude-usage-monitor
_CLAUDE_USAGE_REPO_HOST = "github.com"
_CLAUDE_USAGE_REPO_PATH = "LightspeedDMS/claude-usage.git"


def handle_install(subcommand: Optional[str]) -> Dict[str, Any]:
    """
    Handle 'pace-maker install <subcommand>' command.

    Args:
        subcommand: The install target (e.g. 'claude-usage-monitor')

    Returns:
        Dictionary with:
        - success: bool
        - message: str (user-friendly message)
    """
    if subcommand is None:
        available = ", ".join(sorted(KNOWN_TARGETS))
        return {
            "success": False,
            "message": (
                f"Unknown install target: None. " f"Available targets: {available}"
            ),
        }

    if subcommand not in KNOWN_TARGETS:
        available = ", ".join(sorted(KNOWN_TARGETS))
        return {
            "success": False,
            "message": (
                f"Unknown install target: {subcommand}. "
                f"Available targets: {available}"
            ),
        }

    if subcommand == "claude-usage-monitor":
        return install_claude_usage_monitor()

    # Should never reach here given the KNOWN_TARGETS check above
    return {"success": False, "message": f"Unhandled target: {subcommand}"}


def install_claude_usage_monitor() -> Dict[str, Any]:
    """
    Install the claude-usage-monitor tool via pip from GitHub.

    Returns:
        Dictionary with success bool and user-friendly message.
    """
    # Step 1: Detect authentication method
    git_url = detect_git_auth(_CLAUDE_USAGE_REPO_HOST, _CLAUDE_USAGE_REPO_PATH)
    if git_url is None:
        return {
            "success": False,
            "message": error_message_with_auth_instructions(),
        }

    # Step 2: Run pip install --upgrade
    pip_cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        f"git+{git_url}",
    ]
    result = subprocess.run(
        pip_cmd,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        return {
            "success": False,
            "message": f"pip install failed:\n{result.stderr}",
        }

    # Step 3: Verify installation
    return verify_installation()


def detect_git_auth(repo_host: str, repo_path: str) -> Optional[str]:
    """
    Detect which git authentication method is available.

    Tries SSH first (exit code 1 from GitHub means authenticated),
    then falls back to gh CLI token.

    Args:
        repo_host: e.g. 'github.com'
        repo_path: e.g. 'LightspeedDMS/claude-usage.git'

    Returns:
        A git URL string if auth is available, None otherwise.
    """
    # Try SSH first
    ssh_result = subprocess.run(
        [
            "ssh",
            "-T",
            f"git@{repo_host}",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "StrictHostKeyChecking=accept-new",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if ssh_result.returncode == 1:
        # Exit 1 = GitHub authenticated (shell access denied = success for git)
        return f"ssh://git@{repo_host}/{repo_path}"

    # Try gh auth token
    try:
        gh_result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if gh_result.returncode == 0 and gh_result.stdout.strip() != "":
            token = gh_result.stdout.strip()
            return f"https://{token}@{repo_host}/{repo_path}"
    except FileNotFoundError:
        # gh CLI not installed, skip this method
        pass

    # Neither method works
    return None


def verify_installation() -> Dict[str, Any]:
    """
    Verify the claude-usage installation using shutil.which and pip show.

    The claude-usage CLI does not support --version; use pip show to get
    the installed version instead.

    Returns:
        Dictionary with:
        - success=True + version message if binary found in PATH and pip show succeeds
        - success=True + PATH warning if binary NOT in PATH but pip show succeeds
        - success=False + error if pip show fails (installation did not complete)
    """
    binary_path = shutil.which("claude-usage")

    pip_result = subprocess.run(
        [sys.executable, "-m", "pip", "show", "claude-usage"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    if pip_result.returncode != 0:
        return {
            "success": False,
            "message": "Installation verification failed: pip show claude-usage returned no results.",
        }

    # Extract version from pip show output (line: "Version: X.Y.Z")
    version = ""
    for line in pip_result.stdout.splitlines():
        if line.startswith("Version:"):
            version = line.split(":", 1)[1].strip()
            break

    if binary_path is not None:
        return {
            "success": True,
            "message": (
                f"claude-usage-monitor installed successfully. " f"Version: {version}"
            ),
        }
    else:
        return {
            "success": True,
            "message": (
                f"Package installed (version {version}) but 'claude-usage' command not found. "
                "Ensure ~/.local/bin is in your PATH."
            ),
        }


def error_message_with_auth_instructions() -> str:
    """
    Return a detailed error message explaining how to set up GitHub auth.

    Returns:
        Multi-line string with SSH and gh CLI setup instructions.
    """
    return (
        "Could not authenticate with GitHub to install claude-usage-monitor.\n"
        "The repository is private and requires authentication.\n"
        "\n"
        "Option 1 - SSH key (recommended):\n"
        "  1. Generate a key: ssh-keygen -t ed25519\n"
        "  2. Add to GitHub: https://github.com/settings/keys\n"
        "  3. Test: ssh -T git@github.com\n"
        "\n"
        "Option 2 - GitHub CLI:\n"
        "  1. Install gh: https://cli.github.com/\n"
        "  2. Authenticate: gh auth login\n"
        "  3. Retry: pace-maker install claude-usage-monitor"
    )
