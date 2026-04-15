"""
Unit tests for session_registry.workspace module.

Tests:
- resolve_workspace_root(cwd): git success path returns exact git toplevel realpath
- Subdirectory of git repo returns the repo root, not the subdir
- Non-git directory falls back to os.path.realpath(cwd)
- git subprocess timeout falls back to os.path.realpath(cwd)
- git binary not found (FileNotFoundError) falls back to os.path.realpath(cwd)
- git non-zero returncode (not a repo) falls back to os.path.realpath(cwd)
- test_returns_realpath_normalized: symlinked cwd is resolved to canonical realpath
"""

import os
import subprocess
import sys

# ── Module paths for cache-busting ───────────────────────────────────────────
MOD_WORKSPACE = "pacemaker.session_registry.workspace"
MOD_PACKAGE = "pacemaker.session_registry"


def _fresh_workspace():
    """Return a freshly imported workspace module."""
    sys.modules.pop(MOD_WORKSPACE, None)
    sys.modules.pop(MOD_PACKAGE, None)
    import pacemaker.session_registry.workspace as ws

    return ws


class TestResolveWorkspaceRoot:
    """Tests for resolve_workspace_root(cwd)."""

    def test_git_repo_returns_exact_toplevel(self, tmp_path):
        """In a git repo, resolve_workspace_root returns the exact realpath of the git top-level."""
        subprocess.run(
            ["git", "init", str(tmp_path)],
            check=True,
            capture_output=True,
        )
        ws = _fresh_workspace()
        result = ws.resolve_workspace_root(str(tmp_path))
        assert result == os.path.realpath(str(tmp_path))

    def test_git_repo_subdirectory_returns_toplevel(self, tmp_path):
        """When called from a subdirectory, returns the repo root, not the subdir."""
        subprocess.run(
            ["git", "init", str(tmp_path)],
            check=True,
            capture_output=True,
        )
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        ws = _fresh_workspace()
        result = ws.resolve_workspace_root(str(subdir))
        assert result == os.path.realpath(str(tmp_path))

    def test_non_git_directory_falls_back_to_cwd(self, tmp_path):
        """In a non-git directory, resolve_workspace_root returns os.path.realpath(cwd)."""
        ws = _fresh_workspace()
        result = ws.resolve_workspace_root(str(tmp_path))
        assert result == os.path.realpath(str(tmp_path))

    def test_git_timeout_falls_back_to_cwd(self, tmp_path, monkeypatch):
        """When git subprocess times out (TimeoutExpired), falls back to os.path.realpath(cwd)."""
        ws = _fresh_workspace()

        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="git", timeout=2.0)

        monkeypatch.setattr(subprocess, "run", raise_timeout)
        result = ws.resolve_workspace_root(str(tmp_path))
        assert result == os.path.realpath(str(tmp_path))

    def test_git_not_found_falls_back_to_cwd(self, tmp_path, monkeypatch):
        """When git binary is not found (FileNotFoundError), falls back to os.path.realpath(cwd)."""
        ws = _fresh_workspace()

        def raise_file_not_found(*args, **kwargs):
            raise FileNotFoundError("git not found")

        monkeypatch.setattr(subprocess, "run", raise_file_not_found)
        result = ws.resolve_workspace_root(str(tmp_path))
        assert result == os.path.realpath(str(tmp_path))

    def test_git_nonzero_returncode_falls_back_to_cwd(self, tmp_path, monkeypatch):
        """When git completes with non-zero returncode (not a repo), falls back to cwd.

        Tests the branch where subprocess.run() returns a completed result but
        result.returncode != 0 (e.g., 'not a git repository' from git itself).
        """
        ws = _fresh_workspace()

        class _FakeResult:
            returncode = 128
            stdout = b""
            stderr = b"not a git repository"

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _FakeResult())
        result = ws.resolve_workspace_root(str(tmp_path))
        assert result == os.path.realpath(str(tmp_path))

    def test_returns_realpath_normalized(self, tmp_path):
        """resolve_workspace_root resolves symlinks in the cwd path to the canonical target.

        Creates a real directory and a symlink pointing to it, then calls
        resolve_workspace_root with the symlink path. The result must equal
        os.path.realpath of the symlink (the canonical target), not the
        symlink path string itself.
        """
        real_dir = tmp_path / "real_dir"
        real_dir.mkdir()
        sym_link = tmp_path / "sym_link"
        sym_link.symlink_to(real_dir)

        ws = _fresh_workspace()
        result = ws.resolve_workspace_root(str(sym_link))

        expected = os.path.realpath(str(sym_link))
        assert result == expected
        assert expected == os.path.realpath(str(real_dir))
