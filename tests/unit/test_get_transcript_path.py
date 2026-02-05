#!/usr/bin/env python3
"""
Unit tests for get_transcript_path() helper function.

Tests the derivation of transcript path from session_id and current working directory.
"""

import os
import tempfile
from pathlib import Path


def test_get_transcript_path_success():
    """Test get_transcript_path() with valid session and existing transcript file."""
    from pacemaker.hook import get_transcript_path

    # Create temporary directory structure mimicking ~/.claude/projects/
    with tempfile.TemporaryDirectory() as tmpdir:
        # Simulate current working directory: /home/user/Dev/my-project
        fake_cwd = "/home/user/Dev/my-project"

        # Expected project directory name: -home-user-Dev-my-project
        project_dir_name = fake_cwd.replace("/", "-")

        # Create .claude/projects structure
        projects_dir = Path(tmpdir) / ".claude" / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)

        # Create project directory
        project_dir = projects_dir / project_dir_name
        project_dir.mkdir(parents=True, exist_ok=True)

        # Create transcript file
        session_id = "abc123-def456"
        transcript_file = project_dir / f"{session_id}.jsonl"
        transcript_file.write_text('{"message": "test"}\n')

        # Mock os.getcwd() and os.path.expanduser()
        original_getcwd = os.getcwd
        original_expanduser = os.path.expanduser

        try:
            os.getcwd = lambda: fake_cwd
            os.path.expanduser = lambda path: path.replace("~", tmpdir)

            # Call function
            result = get_transcript_path(session_id)

            # Verify result
            assert result is not None
            assert result == str(transcript_file)
            assert os.path.exists(result)

        finally:
            os.getcwd = original_getcwd
            os.path.expanduser = original_expanduser


def test_get_transcript_path_file_not_found():
    """Test get_transcript_path() when transcript file does not exist."""
    from pacemaker.hook import get_transcript_path

    # Create temporary directory structure
    with tempfile.TemporaryDirectory() as tmpdir:
        fake_cwd = "/home/user/Dev/my-project"

        # Mock os.getcwd() and os.path.expanduser()
        original_getcwd = os.getcwd
        original_expanduser = os.path.expanduser

        try:
            os.getcwd = lambda: fake_cwd
            os.path.expanduser = lambda path: path.replace("~", tmpdir)

            # Call function with non-existent session
            result = get_transcript_path("nonexistent-session-id")

            # Verify result is None
            assert result is None

        finally:
            os.getcwd = original_getcwd
            os.path.expanduser = original_expanduser


def test_get_transcript_path_root_directory():
    """Test get_transcript_path() with root directory as cwd."""
    from pacemaker.hook import get_transcript_path

    with tempfile.TemporaryDirectory() as tmpdir:
        fake_cwd = "/"

        # Expected project directory name: - (just a hyphen)
        project_dir_name = "-"

        # Create .claude/projects structure
        projects_dir = Path(tmpdir) / ".claude" / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)

        # Create project directory
        project_dir = projects_dir / project_dir_name
        project_dir.mkdir(parents=True, exist_ok=True)

        # Create transcript file
        session_id = "root-session"
        transcript_file = project_dir / f"{session_id}.jsonl"
        transcript_file.write_text('{"message": "test"}\n')

        # Mock
        original_getcwd = os.getcwd
        original_expanduser = os.path.expanduser

        try:
            os.getcwd = lambda: fake_cwd
            os.path.expanduser = lambda path: path.replace("~", tmpdir)

            # Call function
            result = get_transcript_path(session_id)

            # Verify
            assert result is not None
            assert result == str(transcript_file)

        finally:
            os.getcwd = original_getcwd
            os.path.expanduser = original_expanduser


def test_get_transcript_path_complex_path():
    """Test get_transcript_path() with complex directory path containing multiple slashes."""
    from pacemaker.hook import get_transcript_path

    with tempfile.TemporaryDirectory() as tmpdir:
        fake_cwd = "/home/user/Dev/projects/my-awesome-project"

        # Expected: -home-user-Dev-projects-my-awesome-project
        project_dir_name = fake_cwd.replace("/", "-")

        # Create .claude/projects structure
        projects_dir = Path(tmpdir) / ".claude" / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)

        # Create directory and file
        project_dir = projects_dir / project_dir_name
        project_dir.mkdir(parents=True, exist_ok=True)

        session_id = "complex-session-123"
        transcript_file = project_dir / f"{session_id}.jsonl"
        transcript_file.write_text('{"test": "data"}\n')

        # Mock
        original_getcwd = os.getcwd
        original_expanduser = os.path.expanduser

        try:
            os.getcwd = lambda: fake_cwd
            os.path.expanduser = lambda path: path.replace("~", tmpdir)

            # Call
            result = get_transcript_path(session_id)

            # Verify
            assert result is not None
            assert result == str(transcript_file)
            assert os.path.exists(result)

        finally:
            os.getcwd = original_getcwd
            os.path.expanduser = original_expanduser


def test_get_transcript_path_actual_structure():
    """
    Test get_transcript_path() with actual Claude Code directory structure.

    This test validates the real naming convention:
    /home/jsbattig/Dev/claude-pace-maker -> -home-jsbattig-Dev-claude-pace-maker
    """
    from pacemaker.hook import get_transcript_path

    with tempfile.TemporaryDirectory() as tmpdir:
        fake_cwd = "/home/jsbattig/Dev/claude-pace-maker"

        # Expected directory name based on actual structure
        project_dir_name = "-home-jsbattig-Dev-claude-pace-maker"

        # Create .claude/projects structure
        projects_dir = Path(tmpdir) / ".claude" / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)

        # Create directory structure
        project_dir = projects_dir / project_dir_name
        project_dir.mkdir(parents=True, exist_ok=True)

        session_id = "f9185385-8e2a-4f1e-9a7b-1c2d3e4f5g6h"
        transcript_file = project_dir / f"{session_id}.jsonl"
        transcript_file.write_text('{"message": "langfuse test"}\n')

        # Mock
        original_getcwd = os.getcwd
        original_expanduser = os.path.expanduser

        try:
            os.getcwd = lambda: fake_cwd
            os.path.expanduser = lambda path: path.replace("~", tmpdir)

            # Call
            result = get_transcript_path(session_id)

            # Verify
            assert result is not None
            assert project_dir_name in result
            assert session_id in result
            assert result.endswith(".jsonl")
            assert os.path.exists(result)

        finally:
            os.getcwd = original_getcwd
            os.path.expanduser = original_expanduser
