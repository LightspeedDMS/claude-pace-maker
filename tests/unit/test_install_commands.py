#!/usr/bin/env python3
"""
Unit tests for install_commands module (Story #45).

Tests all acceptance criteria:
1. Fresh install with SSH authentication available
2. Fresh install with gh CLI token fallback
3. Update existing installation (idempotent)
4. No authentication method available
5. Unknown install target
6. pip install fails
7. Install succeeds but binary not in PATH
8. Command works from in-conversation prompt (parse_command dispatch)

Unit tests mock subprocess.run since these tests should not make real
SSH/network calls. This is the CORRECT use of mocking — isolating
external system boundaries (SSH, network, pip).
"""

import unittest
from unittest.mock import patch, MagicMock


class TestParseCommandInstall(unittest.TestCase):
    """Test parse_command() recognizes pace-maker install commands."""

    def test_parse_install_claude_usage_monitor(self):
        """AC8: parse_command() recognizes 'pace-maker install claude-usage-monitor'."""
        from src.pacemaker.user_commands import parse_command

        result = parse_command("pace-maker install claude-usage-monitor")

        self.assertTrue(result["is_pace_maker_command"])
        self.assertEqual(result["command"], "install")
        self.assertEqual(result["subcommand"], "claude-usage-monitor")

    def test_parse_install_unknown_target(self):
        """parse_command() parses unknown install target correctly."""
        from src.pacemaker.user_commands import parse_command

        result = parse_command("pace-maker install unknown-thing")

        self.assertTrue(result["is_pace_maker_command"])
        self.assertEqual(result["command"], "install")
        self.assertEqual(result["subcommand"], "unknown-thing")

    def test_parse_install_case_insensitive(self):
        """parse_command() handles mixed case input."""
        from src.pacemaker.user_commands import parse_command

        result = parse_command("PACE-MAKER INSTALL CLAUDE-USAGE-MONITOR")

        self.assertTrue(result["is_pace_maker_command"])
        self.assertEqual(result["command"], "install")
        self.assertEqual(result["subcommand"], "claude-usage-monitor")

    def test_parse_install_extra_whitespace(self):
        """parse_command() handles extra whitespace in input."""
        from src.pacemaker.user_commands import parse_command

        result = parse_command("  pace-maker   install   claude-usage-monitor  ")

        self.assertTrue(result["is_pace_maker_command"])
        self.assertEqual(result["command"], "install")
        self.assertEqual(result["subcommand"], "claude-usage-monitor")

    def test_parse_non_install_command_not_affected(self):
        """Existing commands still parse correctly after install pattern added."""
        from src.pacemaker.user_commands import parse_command

        result = parse_command("pace-maker status")

        self.assertTrue(result["is_pace_maker_command"])
        self.assertEqual(result["command"], "status")
        self.assertIsNone(result["subcommand"])

    def test_parse_install_with_no_subcommand_not_matched(self):
        """'pace-maker install' with no target is not matched as install command."""
        from src.pacemaker.user_commands import parse_command

        result = parse_command("pace-maker install")

        # Should not match because our pattern requires at least one non-empty subcommand
        self.assertFalse(result["is_pace_maker_command"])


class TestHandleInstallUnknownTarget(unittest.TestCase):
    """Test handle_install() rejects unknown targets."""

    def test_unknown_target_returns_error(self):
        """AC5: Unknown install target returns error listing available targets."""
        from src.pacemaker.install_commands import handle_install

        result = handle_install("nonexistent-tool")

        self.assertFalse(result["success"])
        self.assertIn("Unknown install target", result["message"])
        self.assertIn("nonexistent-tool", result["message"])
        self.assertIn("claude-usage-monitor", result["message"])

    def test_unknown_target_lists_available_targets(self):
        """AC5: Error message lists all available targets."""
        from src.pacemaker.install_commands import handle_install

        result = handle_install("foobar")

        self.assertFalse(result["success"])
        # Must list the known target
        self.assertIn("claude-usage-monitor", result["message"])


class TestDetectGitAuth(unittest.TestCase):
    """Test detect_git_auth() with mocked subprocess calls."""

    # ------------------------------------------------------------------
    # New tests: plain HTTPS tried first (Step 1 in new auth order)
    # ------------------------------------------------------------------

    def test_https_public_repo_returns_https_url(self):
        """Step 1: git ls-remote succeeds for public repo → returns plain HTTPS URL (no token)."""
        from src.pacemaker.install_commands import detect_git_auth

        mock_ls_remote = MagicMock()
        mock_ls_remote.returncode = 0  # Public repo accessible

        def side_effect(cmd, **kwargs):
            if cmd[0] == "git" and cmd[1] == "ls-remote":
                return mock_ls_remote
            return MagicMock(returncode=255)  # SSH/gh should NOT be called

        with patch("subprocess.run", side_effect=side_effect):
            url = detect_git_auth("github.com", "LightspeedDMS/claude-usage.git")

        self.assertIsNotNone(url)
        self.assertEqual(url, "https://github.com/LightspeedDMS/claude-usage.git")
        # Must NOT contain any token or credentials
        self.assertNotIn("@", url.replace("https://", ""))

    def test_https_url_contains_no_token(self):
        """Plain HTTPS URL returned by Step 1 must have no embedded credentials."""
        from src.pacemaker.install_commands import detect_git_auth

        mock_ls_remote = MagicMock()
        mock_ls_remote.returncode = 0

        with patch("subprocess.run", return_value=mock_ls_remote):
            url = detect_git_auth("github.com", "LightspeedDMS/claude-usage.git")

        # URL must be exactly https://<host>/<path> — no token embedded
        self.assertEqual(url, "https://github.com/LightspeedDMS/claude-usage.git")

    def test_https_fails_falls_back_to_ssh(self):
        """Step 1 fails (git ls-remote non-zero) → falls through to SSH check."""
        from src.pacemaker.install_commands import detect_git_auth

        mock_ls_remote = MagicMock()
        mock_ls_remote.returncode = 128  # Git error (e.g. private repo, auth required)

        mock_ssh = MagicMock()
        mock_ssh.returncode = 1  # SSH authenticated

        def side_effect(cmd, **kwargs):
            if cmd[0] == "git" and cmd[1] == "ls-remote":
                return mock_ls_remote
            if cmd[0] == "ssh":
                return mock_ssh
            return MagicMock(returncode=255)

        with patch("subprocess.run", side_effect=side_effect):
            url = detect_git_auth("github.com", "LightspeedDMS/claude-usage.git")

        self.assertIsNotNone(url)
        self.assertIn("ssh://", url)
        self.assertIn("git@github.com", url)

    def test_https_timeout_falls_back_to_ssh(self):
        """Step 1 times out → falls through to SSH check (no crash)."""
        import subprocess
        from src.pacemaker.install_commands import detect_git_auth

        mock_ssh = MagicMock()
        mock_ssh.returncode = 1  # SSH authenticated

        def side_effect(cmd, **kwargs):
            if cmd[0] == "git" and cmd[1] == "ls-remote":
                raise subprocess.TimeoutExpired(cmd, 15)
            if cmd[0] == "ssh":
                return mock_ssh
            return MagicMock(returncode=255)

        with patch("subprocess.run", side_effect=side_effect):
            url = detect_git_auth("github.com", "LightspeedDMS/claude-usage.git")

        self.assertIsNotNone(url)
        self.assertIn("ssh://", url)

    def test_https_git_not_found_falls_back_to_ssh(self):
        """Step 1 raises FileNotFoundError (git binary missing) → falls through to SSH."""
        from src.pacemaker.install_commands import detect_git_auth

        mock_ssh = MagicMock()
        mock_ssh.returncode = 1  # SSH authenticated

        def side_effect(cmd, **kwargs):
            if cmd[0] == "git" and cmd[1] == "ls-remote":
                raise FileNotFoundError("git not found")
            if cmd[0] == "ssh":
                return mock_ssh
            return MagicMock(returncode=255)

        with patch("subprocess.run", side_effect=side_effect):
            url = detect_git_auth("github.com", "LightspeedDMS/claude-usage.git")

        self.assertIsNotNone(url)
        self.assertIn("ssh://", url)

    def test_https_fails_ssh_fails_falls_back_to_gh_token(self):
        """Step 1 and Step 2 fail → falls through to gh auth token."""
        from src.pacemaker.install_commands import detect_git_auth

        mock_ls_remote = MagicMock()
        mock_ls_remote.returncode = 128  # HTTPS failed

        mock_ssh = MagicMock()
        mock_ssh.returncode = 255  # SSH also failed

        mock_gh = MagicMock()
        mock_gh.returncode = 0
        mock_gh.stdout = "ghp_fallbacktoken\n"

        def side_effect(cmd, **kwargs):
            if cmd[0] == "git" and cmd[1] == "ls-remote":
                return mock_ls_remote
            if cmd[0] == "ssh":
                return mock_ssh
            if cmd[0] == "gh":
                return mock_gh
            return MagicMock(returncode=1)

        with patch("subprocess.run", side_effect=side_effect):
            url = detect_git_auth("github.com", "LightspeedDMS/claude-usage.git")

        self.assertIsNotNone(url)
        self.assertIn("https://", url)
        self.assertIn("ghp_fallbacktoken", url)
        self.assertIn("github.com", url)

    def test_all_three_methods_fail_returns_none(self):
        """All three methods fail → returns None."""
        from src.pacemaker.install_commands import detect_git_auth

        mock_ls_remote = MagicMock()
        mock_ls_remote.returncode = 128

        mock_ssh = MagicMock()
        mock_ssh.returncode = 255

        mock_gh = MagicMock()
        mock_gh.returncode = 1
        mock_gh.stdout = ""

        def side_effect(cmd, **kwargs):
            if cmd[0] == "git" and cmd[1] == "ls-remote":
                return mock_ls_remote
            if cmd[0] == "ssh":
                return mock_ssh
            if cmd[0] == "gh":
                return mock_gh
            return MagicMock(returncode=1)

        with patch("subprocess.run", side_effect=side_effect):
            url = detect_git_auth("github.com", "LightspeedDMS/claude-usage.git")

        self.assertIsNone(url)

    # ------------------------------------------------------------------
    # Existing tests updated to account for new Step 1 (git ls-remote)
    # ------------------------------------------------------------------

    def test_ssh_success_returns_ssh_url(self):
        """AC1: HTTPS fails, SSH exit code 1 (GitHub authenticated) returns SSH URL."""
        from src.pacemaker.install_commands import detect_git_auth

        mock_ls_remote = MagicMock()
        mock_ls_remote.returncode = 128  # HTTPS fails → fall through to SSH

        mock_ssh_result = MagicMock()
        mock_ssh_result.returncode = 1  # Exit 1 = GitHub SSH authenticated

        def side_effect(cmd, **kwargs):
            if cmd[0] == "git" and cmd[1] == "ls-remote":
                return mock_ls_remote
            if cmd[0] == "ssh":
                return mock_ssh_result
            return MagicMock(returncode=255)

        with patch("subprocess.run", side_effect=side_effect):
            url = detect_git_auth("github.com", "LightspeedDMS/claude-usage.git")

        self.assertIsNotNone(url)
        self.assertIn("ssh://", url)
        self.assertIn("git@github.com", url)
        self.assertIn("LightspeedDMS/claude-usage.git", url)

    def test_ssh_failure_gh_token_success_returns_https_url(self):
        """AC2: HTTPS fails, SSH fails (exit 255), gh auth token succeeds, returns HTTPS+token URL."""
        from src.pacemaker.install_commands import detect_git_auth

        mock_ls_remote = MagicMock()
        mock_ls_remote.returncode = 128  # HTTPS fails

        mock_ssh_result = MagicMock()
        mock_ssh_result.returncode = 255  # SSH auth failed

        mock_gh_result = MagicMock()
        mock_gh_result.returncode = 0
        mock_gh_result.stdout = "ghp_testtoken123\n"

        def side_effect(cmd, **kwargs):
            if cmd[0] == "git" and cmd[1] == "ls-remote":
                return mock_ls_remote
            if cmd[0] == "ssh":
                return mock_ssh_result
            elif cmd[0] == "gh":
                return mock_gh_result
            return MagicMock(returncode=1)

        with patch("subprocess.run", side_effect=side_effect):
            url = detect_git_auth("github.com", "LightspeedDMS/claude-usage.git")

        self.assertIsNotNone(url)
        self.assertIn("https://", url)
        self.assertIn("ghp_testtoken123", url)
        self.assertIn("github.com", url)
        self.assertIn("LightspeedDMS/claude-usage.git", url)

    def test_token_embedded_in_https_url(self):
        """Token is correctly embedded in HTTPS URL format."""
        from src.pacemaker.install_commands import detect_git_auth

        mock_ls_remote = MagicMock()
        mock_ls_remote.returncode = 128  # HTTPS fails

        mock_ssh_result = MagicMock()
        mock_ssh_result.returncode = 255

        mock_gh_result = MagicMock()
        mock_gh_result.returncode = 0
        mock_gh_result.stdout = "ghp_mytoken456"

        def side_effect(cmd, **kwargs):
            if cmd[0] == "git" and cmd[1] == "ls-remote":
                return mock_ls_remote
            if cmd[0] == "ssh":
                return mock_ssh_result
            elif cmd[0] == "gh":
                return mock_gh_result
            return MagicMock(returncode=1)

        with patch("subprocess.run", side_effect=side_effect):
            url = detect_git_auth("github.com", "LightspeedDMS/claude-usage.git")

        # Token must appear in the URL in standard git HTTPS token format
        self.assertIn("ghp_mytoken456@", url)

    def test_both_methods_fail_returns_none(self):
        """AC4: HTTPS, SSH, and gh all fail → returns None."""
        from src.pacemaker.install_commands import detect_git_auth

        mock_ls_remote = MagicMock()
        mock_ls_remote.returncode = 128  # HTTPS failed

        mock_ssh_result = MagicMock()
        mock_ssh_result.returncode = 255  # SSH failed

        mock_gh_result = MagicMock()
        mock_gh_result.returncode = 1  # gh auth failed
        mock_gh_result.stdout = ""

        def side_effect(cmd, **kwargs):
            if cmd[0] == "git" and cmd[1] == "ls-remote":
                return mock_ls_remote
            if cmd[0] == "ssh":
                return mock_ssh_result
            elif cmd[0] == "gh":
                return mock_gh_result
            return MagicMock(returncode=1)

        with patch("subprocess.run", side_effect=side_effect):
            url = detect_git_auth("github.com", "LightspeedDMS/claude-usage.git")

        self.assertIsNone(url)

    def test_gh_not_installed_returns_none(self):
        """AC4: HTTPS fails, SSH fails, gh CLI not installed (FileNotFoundError) → returns None."""
        from src.pacemaker.install_commands import detect_git_auth

        mock_ls_remote = MagicMock()
        mock_ls_remote.returncode = 128  # HTTPS failed

        mock_ssh_result = MagicMock()
        mock_ssh_result.returncode = 255  # SSH failed

        def side_effect(cmd, **kwargs):
            if cmd[0] == "git" and cmd[1] == "ls-remote":
                return mock_ls_remote
            if cmd[0] == "ssh":
                return mock_ssh_result
            elif cmd[0] == "gh":
                raise FileNotFoundError("gh not found")
            return MagicMock(returncode=1)

        with patch("subprocess.run", side_effect=side_effect):
            url = detect_git_auth("github.com", "LightspeedDMS/claude-usage.git")

        self.assertIsNone(url)

    def test_gh_empty_token_returns_none(self):
        """gh auth token returning empty string is treated as failure."""
        from src.pacemaker.install_commands import detect_git_auth

        mock_ls_remote = MagicMock()
        mock_ls_remote.returncode = 128  # HTTPS failed

        mock_ssh_result = MagicMock()
        mock_ssh_result.returncode = 255

        mock_gh_result = MagicMock()
        mock_gh_result.returncode = 0
        mock_gh_result.stdout = ""  # Empty token

        def side_effect(cmd, **kwargs):
            if cmd[0] == "git" and cmd[1] == "ls-remote":
                return mock_ls_remote
            if cmd[0] == "ssh":
                return mock_ssh_result
            elif cmd[0] == "gh":
                return mock_gh_result
            return MagicMock(returncode=1)

        with patch("subprocess.run", side_effect=side_effect):
            url = detect_git_auth("github.com", "LightspeedDMS/claude-usage.git")

        self.assertIsNone(url)


class TestVerifyInstallation(unittest.TestCase):
    """Test verify_installation() with mocked shutil.which, --version, and pip show."""

    def test_verify_version_flag_succeeds(self):
        """Primary path: claude-usage --version works, returns version from stdout."""
        from src.pacemaker.install_commands import verify_installation

        mock_version = MagicMock()
        mock_version.returncode = 0
        mock_version.stdout = "claude-usage 2.1.0\n"

        with patch("shutil.which", return_value="/home/user/.local/bin/claude-usage"):
            with patch("subprocess.run", return_value=mock_version):
                result = verify_installation()

        self.assertTrue(result["success"])
        self.assertIn("claude-usage 2.1.0", result["message"])
        self.assertIn("installed successfully", result["message"])

    def test_verify_version_flag_fails_falls_back_to_pip_show(self):
        """Fallback: --version fails but pip show succeeds, reports version from pip."""
        from src.pacemaker.install_commands import verify_installation

        mock_version = MagicMock()
        mock_version.returncode = 2  # --version not supported
        mock_version.stdout = ""

        mock_pip = MagicMock()
        mock_pip.returncode = 0
        mock_pip.stdout = "Name: claude-usage\nVersion: 1.2.0\nSummary: ...\n"

        def side_effect(cmd, **kwargs):
            if isinstance(cmd, list) and cmd[0] == "claude-usage":
                return mock_version
            return mock_pip

        with patch("shutil.which", return_value="/home/user/.local/bin/claude-usage"):
            with patch("subprocess.run", side_effect=side_effect):
                result = verify_installation()

        self.assertTrue(result["success"])
        self.assertIn("1.2.0", result["message"])
        self.assertIn("installed successfully", result["message"])

    def test_verify_binary_not_found_but_pip_show_succeeds_returns_path_warning(self):
        """AC7: Binary not in PATH but pip show succeeds returns PATH warning."""
        from src.pacemaker.install_commands import verify_installation

        mock_pip_result = MagicMock()
        mock_pip_result.returncode = 0
        mock_pip_result.stdout = "Name: claude-usage\nVersion: 1.2.0\n"

        with patch("shutil.which", return_value=None):
            with patch("subprocess.run", return_value=mock_pip_result):
                result = verify_installation()

        self.assertTrue(result["success"])  # Install succeeded, just PATH issue
        self.assertIn("PATH", result["message"])
        self.assertIn("~/.local/bin", result["message"])

    def test_verify_pip_show_fails_returns_error(self):
        """pip show non-zero return means something went wrong with installation."""
        from src.pacemaker.install_commands import verify_installation

        mock_pip_result = MagicMock()
        mock_pip_result.returncode = 1
        mock_pip_result.stdout = ""

        with patch("shutil.which", return_value=None):
            with patch("subprocess.run", return_value=mock_pip_result):
                result = verify_installation()

        self.assertFalse(result["success"])
        self.assertIn("failed", result["message"].lower())

    def test_verify_version_timeout_falls_back_to_pip(self):
        """--version times out, falls back to pip show gracefully."""
        import subprocess as sp
        from src.pacemaker.install_commands import verify_installation

        mock_pip = MagicMock()
        mock_pip.returncode = 0
        mock_pip.stdout = "Name: claude-usage\nVersion: 2.5.1\n"

        def side_effect(cmd, **kwargs):
            if isinstance(cmd, list) and cmd[0] == "claude-usage":
                raise sp.TimeoutExpired(cmd, 10)
            return mock_pip

        with patch("shutil.which", return_value="/usr/local/bin/claude-usage"):
            with patch("subprocess.run", side_effect=side_effect):
                result = verify_installation()

        self.assertTrue(result["success"])
        self.assertIn("2.5.1", result["message"])


class TestErrorMessageWithAuthInstructions(unittest.TestCase):
    """Test error_message_with_auth_instructions() content."""

    def test_contains_ssh_instructions(self):
        """AC4: Error message includes SSH key setup instructions."""
        from src.pacemaker.install_commands import error_message_with_auth_instructions

        message = error_message_with_auth_instructions()

        self.assertIn("SSH", message)
        self.assertIn("ssh-keygen", message)
        self.assertIn("github.com/settings/keys", message)

    def test_contains_gh_cli_instructions(self):
        """AC4: Error message includes gh CLI setup instructions."""
        from src.pacemaker.install_commands import error_message_with_auth_instructions

        message = error_message_with_auth_instructions()

        self.assertIn("gh", message)
        self.assertIn("gh auth login", message)
        self.assertIn("cli.github.com", message)

    def test_contains_retry_instruction(self):
        """AC4: Error message tells user to retry after setup."""
        from src.pacemaker.install_commands import error_message_with_auth_instructions

        message = error_message_with_auth_instructions()

        self.assertIn("pace-maker install claude-usage-monitor", message)

    def test_explains_private_repo(self):
        """AC4: Error message explains why auth is needed."""
        from src.pacemaker.install_commands import error_message_with_auth_instructions

        message = error_message_with_auth_instructions()

        self.assertIn("private", message)


class TestInstallClaudeUsageMonitorFlow(unittest.TestCase):
    """Integration tests for full install flow via handle_install()."""

    def test_full_flow_https_public_repo_success(self):
        """New AC: Full install via plain HTTPS (public repo, no auth needed)."""
        from src.pacemaker.install_commands import handle_install

        # git ls-remote succeeds (public repo)
        mock_ls_remote = MagicMock()
        mock_ls_remote.returncode = 0

        # pip install succeeds
        mock_pip = MagicMock()
        mock_pip.returncode = 0
        mock_pip.stdout = "Name: claude-usage\nVersion: 2.0.0\n"
        mock_pip.stderr = ""

        # claude-usage --version succeeds
        mock_version = MagicMock()
        mock_version.returncode = 0
        mock_version.stdout = "claude-usage 2.0.0\n"

        def side_effect(cmd, **kwargs):
            if isinstance(cmd, list) and cmd[0] == "git" and cmd[1] == "ls-remote":
                return mock_ls_remote
            elif isinstance(cmd, list) and cmd[0] == "claude-usage":
                return mock_version
            elif isinstance(cmd, list) and len(cmd) > 2 and "pip" in cmd:
                return mock_pip
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=side_effect):
            with patch(
                "shutil.which", return_value="/home/user/.local/bin/claude-usage"
            ):
                result = handle_install("claude-usage-monitor")

        self.assertTrue(result["success"])
        self.assertIn("installed successfully", result["message"])

    def test_full_flow_ssh_success(self):
        """AC1: Full install with SSH auth - HTTPS fails, SSH succeeds, installs, verifies."""
        from src.pacemaker.install_commands import handle_install

        # git ls-remote fails (would require auth)
        mock_ls_remote = MagicMock()
        mock_ls_remote.returncode = 128

        # SSH succeeds (exit 1 = GitHub authenticated)
        mock_ssh = MagicMock()
        mock_ssh.returncode = 1

        # pip install succeeds
        mock_pip = MagicMock()
        mock_pip.returncode = 0
        mock_pip.stdout = "Name: claude-usage\nVersion: 2.0.0\n"
        mock_pip.stderr = ""

        # claude-usage --version succeeds
        mock_version = MagicMock()
        mock_version.returncode = 0
        mock_version.stdout = "claude-usage 2.0.0\n"

        def side_effect(cmd, **kwargs):
            if isinstance(cmd, list) and cmd[0] == "git" and cmd[1] == "ls-remote":
                return mock_ls_remote
            elif isinstance(cmd, list) and cmd[0] == "ssh":
                return mock_ssh
            elif isinstance(cmd, list) and cmd[0] == "claude-usage":
                return mock_version
            elif isinstance(cmd, list) and len(cmd) > 2 and "pip" in cmd:
                return mock_pip
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=side_effect):
            with patch(
                "shutil.which", return_value="/home/user/.local/bin/claude-usage"
            ):
                result = handle_install("claude-usage-monitor")

        self.assertTrue(result["success"])
        self.assertIn("installed successfully", result["message"])

    def test_full_flow_gh_token_fallback(self):
        """AC2: Full install with gh token fallback - HTTPS fails, SSH fails, gh succeeds."""
        from src.pacemaker.install_commands import handle_install

        # git ls-remote fails
        mock_ls_remote = MagicMock()
        mock_ls_remote.returncode = 128

        # SSH fails
        mock_ssh = MagicMock()
        mock_ssh.returncode = 255

        # gh auth token succeeds
        mock_gh = MagicMock()
        mock_gh.returncode = 0
        mock_gh.stdout = "ghp_testtoken"

        # pip install succeeds
        mock_pip = MagicMock()
        mock_pip.returncode = 0
        mock_pip.stdout = "Name: claude-usage\nVersion: 2.0.0\n"
        mock_pip.stderr = ""

        # claude-usage --version succeeds
        mock_version = MagicMock()
        mock_version.returncode = 0
        mock_version.stdout = "claude-usage 2.0.0\n"

        def side_effect(cmd, **kwargs):
            if isinstance(cmd, list) and cmd[0] == "git" and cmd[1] == "ls-remote":
                return mock_ls_remote
            elif isinstance(cmd, list) and cmd[0] == "ssh":
                return mock_ssh
            elif isinstance(cmd, list) and cmd[0] == "gh":
                return mock_gh
            elif isinstance(cmd, list) and cmd[0] == "claude-usage":
                return mock_version
            elif isinstance(cmd, list) and len(cmd) > 2 and "pip" in cmd:
                return mock_pip
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=side_effect):
            with patch(
                "shutil.which", return_value="/home/user/.local/bin/claude-usage"
            ):
                result = handle_install("claude-usage-monitor")

        self.assertTrue(result["success"])
        self.assertIn("installed successfully", result["message"])

    def test_full_flow_no_auth_returns_error(self):
        """AC4: All auth methods fail - returns clear error with instructions."""
        from src.pacemaker.install_commands import handle_install

        # git ls-remote fails
        mock_ls_remote = MagicMock()
        mock_ls_remote.returncode = 128

        # SSH fails
        mock_ssh = MagicMock()
        mock_ssh.returncode = 255

        # gh auth token fails
        mock_gh = MagicMock()
        mock_gh.returncode = 1
        mock_gh.stdout = ""

        def side_effect(cmd, **kwargs):
            if isinstance(cmd, list) and cmd[0] == "git" and cmd[1] == "ls-remote":
                return mock_ls_remote
            elif isinstance(cmd, list) and cmd[0] == "ssh":
                return mock_ssh
            elif isinstance(cmd, list) and cmd[0] == "gh":
                return mock_gh
            return MagicMock(returncode=1)

        with patch("subprocess.run", side_effect=side_effect):
            result = handle_install("claude-usage-monitor")

        self.assertFalse(result["success"])
        self.assertIn("authenticate", result["message"].lower())
        # Should contain setup instructions
        self.assertIn("SSH", result["message"])
        self.assertIn("gh", result["message"])

    def test_pip_failure_returns_actionable_error(self):
        """AC6: pip install failure shows pip stderr with actionable message."""
        from src.pacemaker.install_commands import handle_install

        # git ls-remote succeeds (HTTPS public repo — auth detection short-circuits)
        mock_ls_remote = MagicMock()
        mock_ls_remote.returncode = 0

        # pip install FAILS
        mock_pip = MagicMock()
        mock_pip.returncode = 1
        mock_pip.stderr = (
            "ERROR: Could not find a version that satisfies the requirement"
        )

        def side_effect(cmd, **kwargs):
            if isinstance(cmd, list) and cmd[0] == "git" and cmd[1] == "ls-remote":
                return mock_ls_remote
            elif isinstance(cmd, list) and len(cmd) > 2 and "pip" in " ".join(cmd):
                return mock_pip
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=side_effect):
            result = handle_install("claude-usage-monitor")

        self.assertFalse(result["success"])
        self.assertIn("pip install failed", result["message"])
        self.assertIn("Could not find a version", result["message"])

    def test_install_succeeds_but_not_in_path(self):
        """AC7: Install succeeds but binary not in PATH - warning with ~/.local/bin."""
        from src.pacemaker.install_commands import handle_install

        # git ls-remote succeeds (HTTPS public repo — auth detection short-circuits)
        mock_ls_remote = MagicMock()
        mock_ls_remote.returncode = 0

        # pip install + pip show both succeed
        mock_pip = MagicMock()
        mock_pip.returncode = 0
        mock_pip.stdout = "Name: claude-usage\nVersion: 1.2.0\n"
        mock_pip.stderr = ""

        def side_effect(cmd, **kwargs):
            if isinstance(cmd, list) and cmd[0] == "git" and cmd[1] == "ls-remote":
                return mock_ls_remote
            elif isinstance(cmd, list) and len(cmd) > 2 and "pip" in cmd:
                return mock_pip
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=side_effect):
            with patch("shutil.which", return_value=None):  # binary NOT in PATH
                result = handle_install("claude-usage-monitor")

        # Install returned success=True but with PATH warning
        self.assertTrue(result["success"])
        self.assertIn("PATH", result["message"])
        self.assertIn("~/.local/bin", result["message"])


class TestExecuteCommandDispatch(unittest.TestCase):
    """Test execute_command() dispatches install commands correctly."""

    def test_execute_command_routes_to_install_handler(self):
        """AC8: execute_command() with 'install' routes to install handler."""
        from src.pacemaker.user_commands import execute_command

        # Mock the install handler to verify it's called
        mock_result = {"success": True, "message": "Installed!"}

        with patch(
            "src.pacemaker.install_commands.handle_install",
            return_value=mock_result,
        ) as mock_install:
            result = execute_command(
                "install",
                config_path="/tmp/test_config.json",
                subcommand="claude-usage-monitor",
            )

        mock_install.assert_called_once_with("claude-usage-monitor")
        self.assertEqual(result, mock_result)

    def test_execute_command_install_without_subcommand(self):
        """execute_command() handles missing subcommand gracefully."""
        from src.pacemaker.user_commands import execute_command

        # Should not crash with no subcommand
        result = execute_command(
            "install",
            config_path="/tmp/test_config.json",
            subcommand=None,
        )

        # Either fail gracefully or route to handler with None
        self.assertIn("success", result)
        self.assertIn("message", result)


if __name__ == "__main__":
    unittest.main()
