"""Issue #134 — governance events must be labelled by project, not by cwd.

`hook.py` derived the label from `os.path.basename(os.getcwd())`, the hook
process's incidental working directory. That mislabelled 231 of 528 events in
48h as `hooks`, `src`, `.claude` and similar fragments — `src` being the worst
because it is ambiguous across every repository.
"""

import pathlib
import subprocess

import pytest

from pacemaker.hook import _resolve_project_name


@pytest.fixture
def git_repo(tmp_path):
    """A real git worktree with a nested subdirectory."""
    root = tmp_path / "my-project"
    (root / "src" / "deep").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


class TestResolvesToWorkspaceRoot:
    def test_returns_git_root_basename_from_root(self, git_repo):
        assert _resolve_project_name(str(git_repo)) == "my-project"

    def test_returns_git_root_basename_from_nested_dir(self, git_repo):
        """The regression: a nested cwd must not become the label."""
        nested = git_repo / "src" / "deep"
        assert _resolve_project_name(str(nested)) == "my-project"

    def test_nested_dir_does_not_yield_src(self, git_repo):
        """`src` is the ambiguous label that motivated this fix."""
        assert _resolve_project_name(str(git_repo / "src")) != "src"


class TestFallbacks:
    def test_non_git_dir_falls_back_to_basename(self, tmp_path):
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        assert _resolve_project_name(str(plain)) == "not-a-repo"

    def test_none_cwd_uses_process_cwd(self):
        """No explicit cwd → resolve from the process cwd, still non-empty."""
        assert _resolve_project_name(None)

    def test_explicit_cwd_wins_over_process_cwd(self, git_repo, monkeypatch):
        monkeypatch.chdir(pathlib.Path(__file__).parent)
        assert _resolve_project_name(str(git_repo)) == "my-project"


class TestNeverRaises:
    """A telemetry label must never break the gate."""

    def test_resolver_import_failure_degrades_not_raises(self, tmp_path, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def boom(name, *a, **kw):
            if "workspace" in name:
                raise ImportError("simulated")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", boom)
        target = tmp_path / "fallback-name"
        target.mkdir()
        assert _resolve_project_name(str(target)) == "fallback-name"

    def test_resolver_oserror_degrades_not_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "pacemaker.session_registry.workspace.resolve_workspace_root",
            lambda cwd: (_ for _ in ()).throw(OSError("simulated")),
        )
        target = tmp_path / "oserror-name"
        target.mkdir()
        assert _resolve_project_name(str(target)) == "oserror-name"


class TestNoRegressionToProcessCwd:
    def test_no_remaining_getcwd_label_sites(self):
        """No call site may go back to labelling from the process cwd."""
        src = pathlib.Path(__file__).resolve().parents[2] / "src/pacemaker/hook.py"
        text = src.read_text()
        offenders = [
            line.strip()
            for line in text.splitlines()
            if "_project_name = os.path.basename(os.getcwd())" in line
        ]
        assert offenders == [], f"process-cwd label sites remain: {offenders}"

    def test_all_label_sites_use_the_resolver(self):
        src = pathlib.Path(__file__).resolve().parents[2] / "src/pacemaker/hook.py"
        assert src.read_text().count("_project_name = _resolve_project_name()") == 5
