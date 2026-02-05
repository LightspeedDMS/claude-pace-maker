#!/usr/bin/env python3
"""
Unit tests for project context metadata extraction.

Tests:
- get_project_context() returns correct structure with all required fields
- Extracts git remote URL when available
- Extracts git branch when available
- Handles non-git directories gracefully (None for git fields)
- Handles git command failures gracefully
- Caches results for performance
"""

import os
import subprocess
import unittest
from unittest.mock import patch, MagicMock

from src.pacemaker.langfuse.project_context import get_project_context, _clear_cache


class TestProjectContext(unittest.TestCase):
    """Test project context extraction"""

    def setUp(self):
        """Clear cache before each test"""
        _clear_cache()

    def test_get_project_context_structure(self):
        """Returns dict with required fields: project_path, project_name, git_remote, git_branch"""
        # Act
        context = get_project_context()

        # Assert - Has all required keys
        self.assertIn("project_path", context)
        self.assertIn("project_name", context)
        self.assertIn("git_remote", context)
        self.assertIn("git_branch", context)

        # Assert - Types
        self.assertIsInstance(context["project_path"], str)
        self.assertIsInstance(context["project_name"], str)
        # git_remote and git_branch can be str or None
        self.assertTrue(
            context["git_remote"] is None or isinstance(context["git_remote"], str)
        )
        self.assertTrue(
            context["git_branch"] is None or isinstance(context["git_branch"], str)
        )

    def test_project_path_and_name(self):
        """Extracts project_path from cwd and project_name from last component"""
        # Act
        context = get_project_context()

        # Assert
        expected_path = os.getcwd()
        expected_name = os.path.basename(expected_path)

        self.assertEqual(context["project_path"], expected_path)
        self.assertEqual(context["project_name"], expected_name)

    @patch("subprocess.run")
    def test_extracts_git_remote_when_available(self, mock_run):
        """Extracts git remote URL when git command succeeds"""
        # Arrange
        mock_run.return_value = MagicMock(
            returncode=0, stdout="https://github.com/user/repo.git\n", stderr=""
        )

        # Act
        context = get_project_context()

        # Assert
        self.assertEqual(context["git_remote"], "https://github.com/user/repo.git")

        # Verify subprocess.run was called correctly
        mock_run.assert_any_call(
            ["git", "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=2,
        )

    @patch("subprocess.run")
    def test_extracts_git_branch_when_available(self, mock_run):
        """Extracts git branch when git command succeeds"""

        # Arrange
        def run_side_effect(*args, **kwargs):
            cmd = args[0]
            if "remote.origin.url" in cmd:
                return MagicMock(
                    returncode=0, stdout="https://github.com/user/repo.git\n", stderr=""
                )
            elif "rev-parse" in cmd:
                return MagicMock(returncode=0, stdout="main\n", stderr="")
            return MagicMock(returncode=1, stdout="", stderr="error")

        mock_run.side_effect = run_side_effect

        # Act
        context = get_project_context()

        # Assert
        self.assertEqual(context["git_branch"], "main")

        # Verify subprocess.run was called for branch
        calls = mock_run.call_args_list
        branch_calls = [c for c in calls if any("rev-parse" in arg for arg in c[0][0])]
        self.assertEqual(len(branch_calls), 1)

    @patch("subprocess.run")
    def test_handles_non_git_directory_gracefully(self, mock_run):
        """Returns None for git fields when not in a git repo"""
        # Arrange - git commands fail with returncode 128 (not a git repo)
        mock_run.return_value = MagicMock(
            returncode=128, stdout="", stderr="fatal: not a git repository"
        )

        # Act
        context = get_project_context()

        # Assert - project_path and project_name still work
        self.assertIsNotNone(context["project_path"])
        self.assertIsNotNone(context["project_name"])

        # Assert - git fields are None
        self.assertIsNone(context["git_remote"])
        self.assertIsNone(context["git_branch"])

    @patch("subprocess.run")
    def test_handles_git_command_failure_gracefully(self, mock_run):
        """Returns None for git fields when git commands fail"""
        # Arrange - git commands raise exception
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=2)

        # Act
        context = get_project_context()

        # Assert - doesn't crash
        self.assertIsNotNone(context)
        self.assertIsNone(context["git_remote"])
        self.assertIsNone(context["git_branch"])

    @patch("subprocess.run")
    def test_caches_results_for_performance(self, mock_run):
        """Caches results - subprocess.run called only once per field"""
        # Arrange
        mock_run.return_value = MagicMock(
            returncode=0, stdout="https://github.com/user/repo.git\n", stderr=""
        )

        # Act - call twice
        context1 = get_project_context()
        context2 = get_project_context()

        # Assert - same results
        self.assertEqual(context1, context2)

        # Assert - subprocess.run called exactly twice (once for remote, once for branch)
        # on first call, zero times on second call
        self.assertEqual(mock_run.call_count, 2)

    @patch("subprocess.run")
    def test_git_remote_strips_whitespace(self, mock_run):
        """Strips whitespace from git remote output"""
        # Arrange - output has trailing newline and spaces
        mock_run.return_value = MagicMock(
            returncode=0, stdout="  https://github.com/user/repo.git  \n", stderr=""
        )

        # Act
        context = get_project_context()

        # Assert - whitespace stripped
        self.assertEqual(context["git_remote"], "https://github.com/user/repo.git")

    @patch("subprocess.run")
    def test_git_branch_strips_whitespace(self, mock_run):
        """Strips whitespace from git branch output"""

        # Arrange
        def run_side_effect(*args, **kwargs):
            cmd = args[0]
            if "remote.origin.url" in cmd:
                return MagicMock(returncode=0, stdout="origin\n", stderr="")
            elif "rev-parse" in cmd:
                return MagicMock(
                    returncode=0, stdout="  feature/test-branch  \n", stderr=""
                )
            return MagicMock(returncode=1, stdout="", stderr="error")

        mock_run.side_effect = run_side_effect

        # Act
        context = get_project_context()

        # Assert - whitespace stripped
        self.assertEqual(context["git_branch"], "feature/test-branch")

    @patch("subprocess.run")
    def test_git_remote_handles_empty_output(self, mock_run):
        """Returns None when git remote command returns empty output"""
        # Arrange
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Act
        context = get_project_context()

        # Assert
        self.assertIsNone(context["git_remote"])


if __name__ == "__main__":
    unittest.main()
