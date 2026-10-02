#!/usr/bin/env python3
"""Unit tests for pacemaker.atomic_file.atomic_write_text (bug #161).

Real files in tmp dirs, no mocking. The concurrent reader/writer proof lives in
tests/test_issue_161_atomic_state_save.py (it drives this helper through
hook.save_state with a real second process).
"""

import os
import stat

import pytest

from pacemaker.atomic_file import atomic_write_text

_UMASK = 0o022
_DEFAULT_NEW_FILE_MODE = 0o644  # 0o666 & ~_UMASK
_PRESERVED_MODE = 0o600


class TestAtomicWriteText:
    def test_new_file_gets_content_parent_dir_and_umask_mode(self, tmp_path):
        target = tmp_path / "nested" / "state.json"
        old_umask = os.umask(_UMASK)
        try:
            atomic_write_text(str(target), '{"a": 1}')
        finally:
            os.umask(old_umask)

        assert target.read_text() == '{"a": 1}'
        assert stat.S_IMODE(target.stat().st_mode) == _DEFAULT_NEW_FILE_MODE
        assert [p.name for p in target.parent.iterdir()] == ["state.json"]

    def test_existing_file_is_replaced_and_keeps_its_mode(self, tmp_path):
        work = tmp_path / "work"  # conftest also puts fake_home in tmp_path
        work.mkdir()
        target = work / "state.json"
        target.write_text("old")
        os.chmod(target, _PRESERVED_MODE)

        atomic_write_text(str(target), "new")

        assert target.read_text() == "new"
        assert stat.S_IMODE(target.stat().st_mode) == _PRESERVED_MODE
        assert [p.name for p in work.iterdir()] == ["state.json"]

    def test_failure_propagates_and_leaves_no_temp_file(self, tmp_path):
        # A real failure: os.replace cannot put a file over a directory.
        work = tmp_path / "work"
        work.mkdir()
        target = work / "state.json"
        target.mkdir()
        (target / "child").write_text("untouched")

        with pytest.raises(OSError):
            atomic_write_text(str(target), "new")

        assert (target / "child").read_text() == "untouched"
        assert [p.name for p in work.iterdir()] == ["state.json"]


class TestSymlinkedTarget:
    """L5: a plain ``open(path, "w")`` writes THROUGH a symlink. The atomic
    version must not silently turn a user's symlinked state file (e.g. managed
    by a dotfiles tool) into a regular file: it resolves the link first and
    atomically replaces the file the link points to."""

    def test_symlink_is_kept_and_its_target_is_updated(self, tmp_path):
        real_dir = tmp_path / "real"
        link_dir = tmp_path / "links"
        real_dir.mkdir()
        link_dir.mkdir()
        real = real_dir / "state.json"
        real.write_text("old")
        os.chmod(real, _PRESERVED_MODE)
        link = link_dir / "state.json"
        link.symlink_to(real)

        atomic_write_text(str(link), "new")

        assert link.is_symlink(), "the symlink was replaced by a regular file"
        assert os.path.realpath(link) == str(real)
        assert real.read_text() == "new"
        assert stat.S_IMODE(real.stat().st_mode) == _PRESERVED_MODE
        assert [p.name for p in real_dir.iterdir()] == ["state.json"]
        assert [p.name for p in link_dir.iterdir()] == ["state.json"]

    def test_dangling_symlink_gets_its_target_created(self, tmp_path):
        real_dir = tmp_path / "real"
        link_dir = tmp_path / "links"
        real_dir.mkdir()
        link_dir.mkdir()
        link = link_dir / "state.json"
        link.symlink_to(real_dir / "state.json")  # target does not exist yet

        atomic_write_text(str(link), "new")

        assert link.is_symlink()
        assert (real_dir / "state.json").read_text() == "new"
