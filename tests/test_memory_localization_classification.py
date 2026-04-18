"""
Unit tests for memory_localization: classify_central (all 6 states),
assert_safe_to_destroy, central_memory_path_from_transcript, local_memory_path,
and replace_with_symlink_atomic (happy paths).

All tests drive through real filesystem state only.
Shared fixtures come from tests/conftest.py (ml_central_base, ml_enc_dir,
ml_local_memory, ml_transcript_path).

PACEMAKER_TEST_MODE=1 is set globally by conftest.py.
PACEMAKER_CENTRAL_BASE is set per-test via the ml_central_base fixture.
"""

import os
import stat
import sys

import pytest


def _can_use_chmod_restrictions():
    """Return True only on POSIX with a non-root user where chmod 000 is meaningful."""
    if sys.platform == "win32":
        return False
    try:
        return os.getuid() != 0
    except AttributeError:
        return False


# ---------------------------------------------------------------------------
# classify_central — all 6 states
# ---------------------------------------------------------------------------


class TestClassifyCentral:
    """classify_central: 6 states exercised via real filesystem."""

    def test_missing(self, ml_central_base, ml_local_memory):
        from pacemaker.memory_localization.core import classify_central

        central = ml_central_base / "enc999" / "memory"
        assert classify_central(central, ml_local_memory) == "missing"

    def test_correct_symlink(self, ml_enc_dir, ml_local_memory):
        from pacemaker.memory_localization.core import classify_central

        central = ml_enc_dir / "memory"
        central.symlink_to(ml_local_memory.resolve())
        assert classify_central(central, ml_local_memory) == "correct_symlink"

    def test_wrong_symlink(self, ml_enc_dir, ml_local_memory, tmp_path):
        from pacemaker.memory_localization.core import classify_central

        other = tmp_path / "other_dir"
        other.mkdir()
        central = ml_enc_dir / "memory"
        central.symlink_to(other.resolve())
        assert classify_central(central, ml_local_memory) == "wrong_symlink"

    def test_regular_folder(self, ml_enc_dir, ml_local_memory):
        from pacemaker.memory_localization.core import classify_central

        central = ml_enc_dir / "memory"
        central.mkdir()
        assert classify_central(central, ml_local_memory) == "regular_folder"

    def test_unknown_when_central_is_a_file(self, ml_enc_dir, ml_local_memory):
        from pacemaker.memory_localization.core import classify_central

        central = ml_enc_dir / "memory"
        central.write_text("not a dir")
        assert classify_central(central, ml_local_memory) == "unknown"

    @pytest.mark.skipif(
        not _can_use_chmod_restrictions(),
        reason="Requires POSIX non-root environment for chmod 000 to block access",
    )
    def test_permission_denied_via_unreadable_parent(self, ml_enc_dir, ml_local_memory):
        """Drive permission_denied via a real chmod-000 parent directory."""
        central = ml_enc_dir / "memory"
        central.mkdir()
        original_mode = stat.S_IMODE(os.stat(str(ml_enc_dir)).st_mode)
        os.chmod(str(ml_enc_dir), 0o000)
        try:
            from pacemaker.memory_localization.core import classify_central

            result = classify_central(central, ml_local_memory)
            assert result == "permission_denied"
        finally:
            os.chmod(str(ml_enc_dir), original_mode)


# ---------------------------------------------------------------------------
# assert_safe_to_destroy
# ---------------------------------------------------------------------------


class TestAssertSafeToDestroy:

    def test_valid_memory_path_does_not_raise(self, ml_central_base):
        from pacemaker.memory_localization.core import assert_safe_to_destroy

        enc = ml_central_base / "enc_valid"
        enc.mkdir()
        memory = enc / "memory"
        memory.mkdir()
        assert_safe_to_destroy(memory)  # must not raise

    def test_raises_outside_central_base(self, ml_central_base, tmp_path):
        from pacemaker.memory_localization.core import assert_safe_to_destroy

        outside = tmp_path / "outside" / "memory"
        outside.mkdir(parents=True)
        with pytest.raises(AssertionError, match="outside central base"):
            assert_safe_to_destroy(outside)

    def test_raises_when_name_not_memory(self, ml_central_base):
        from pacemaker.memory_localization.core import assert_safe_to_destroy

        enc = ml_central_base / "enc_docs"
        enc.mkdir()
        documents = enc / "documents"
        documents.mkdir()
        with pytest.raises(AssertionError, match="not a memory folder"):
            assert_safe_to_destroy(documents)


# ---------------------------------------------------------------------------
# central_memory_path_from_transcript
# ---------------------------------------------------------------------------


class TestCentralMemoryPathFromTranscript:

    def test_returns_sibling_memory_dir(self, ml_enc_dir, ml_transcript_path):
        from pacemaker.memory_localization.core import (
            central_memory_path_from_transcript,
        )

        result = central_memory_path_from_transcript(str(ml_transcript_path))
        assert result == ml_enc_dir / "memory"

    def test_raises_when_transcript_outside_central_base(
        self, ml_central_base, tmp_path
    ):
        from pacemaker.memory_localization.core import (
            central_memory_path_from_transcript,
        )

        outside = tmp_path / "other_project" / "session.jsonl"
        outside.parent.mkdir(parents=True)
        outside.write_text("{}")
        with pytest.raises(AssertionError):
            central_memory_path_from_transcript(str(outside))


# ---------------------------------------------------------------------------
# local_memory_path
# ---------------------------------------------------------------------------


class TestLocalMemoryPath:

    def test_returns_dot_claude_memory_in_project_root(self, ml_central_base, tmp_path):
        from pacemaker.memory_localization.core import local_memory_path

        project_root = tmp_path / "myproject"
        result = local_memory_path(project_root)
        assert result == project_root / ".claude-memory"


# ---------------------------------------------------------------------------
# replace_with_symlink_atomic — happy paths via real filesystem
# ---------------------------------------------------------------------------


class TestReplaceWithSymlinkAtomic:

    def test_replaces_existing_folder_with_symlink(self, ml_enc_dir, ml_local_memory):
        from pacemaker.memory_localization.core import replace_with_symlink_atomic

        central = ml_enc_dir / "memory"
        central.mkdir()
        (central / "old.md").write_text("old content")
        replace_with_symlink_atomic(central, ml_local_memory.resolve())
        assert central.is_symlink()
        assert central.resolve() == ml_local_memory.resolve()
        assert not (ml_enc_dir / "memory.bak_localize").exists()

    def test_creates_symlink_when_central_missing(self, ml_enc_dir, ml_local_memory):
        from pacemaker.memory_localization.core import replace_with_symlink_atomic

        central = ml_enc_dir / "memory"
        replace_with_symlink_atomic(central, ml_local_memory.resolve())
        assert central.is_symlink()
        assert central.resolve() == ml_local_memory.resolve()

    def test_replaces_wrong_symlink_with_correct(
        self, ml_enc_dir, ml_local_memory, tmp_path
    ):
        from pacemaker.memory_localization.core import replace_with_symlink_atomic

        other = tmp_path / "wrong_target"
        other.mkdir()
        central = ml_enc_dir / "memory"
        central.symlink_to(other.resolve())
        replace_with_symlink_atomic(central, ml_local_memory.resolve())
        assert central.is_symlink()
        assert central.resolve() == ml_local_memory.resolve()
