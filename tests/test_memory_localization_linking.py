"""
Unit tests for memory_localization: link_if_local_exists (Flow A).

Covers statuses reachable via real filesystem without any mocking:
  disabled, no_local_folder, local_not_directory, linked_fresh,
  already_linked, replaced_with_symlink, relinked.

Also verifies the absolute-symlink-target design invariant on linked_fresh.

Statuses deferred to tests/test_memory_localization_integration.py
(require internal mocking or non-deterministic concurrency to trigger):
  not_git_repo, permission_denied, unexpected_state, failed, raced_but_ok.

Shared fixtures from tests/conftest.py:
  ml_central_base, ml_repo, ml_enc_dir, ml_transcript_path, ml_local_memory

PACEMAKER_TEST_MODE=1 set globally by conftest.py.
PACEMAKER_CENTRAL_BASE set per-test via ml_central_base fixture.
"""

import os
import pytest


class TestLinkIfLocalExists:

    def test_disabled_returns_disabled(self, ml_central_base, ml_transcript_path):
        from pacemaker.memory_localization.core import link_if_local_exists

        config = {"memory_localization_enabled": False}
        status, target = link_if_local_exists(
            "/any/cwd", str(ml_transcript_path), config
        )
        assert status == "disabled"
        assert target is None

    def test_no_local_folder(self, ml_central_base, ml_repo, ml_transcript_path):
        from pacemaker.memory_localization.core import link_if_local_exists

        config = {"memory_localization_enabled": True}
        status, target = link_if_local_exists(
            str(ml_repo), str(ml_transcript_path), config
        )
        assert status == "no_local_folder"
        assert target is None

    def test_local_not_directory(self, ml_central_base, ml_repo, ml_transcript_path):
        from pacemaker.memory_localization.core import link_if_local_exists

        local = ml_repo / ".claude-memory"
        local.write_text("not a dir")
        config = {"memory_localization_enabled": True}
        status, target = link_if_local_exists(
            str(ml_repo), str(ml_transcript_path), config
        )
        assert status == "local_not_directory"
        assert target is None

    def test_linked_fresh_when_central_missing(
        self, ml_central_base, ml_repo, ml_enc_dir, ml_transcript_path, ml_local_memory
    ):
        """Gherkin: First clone of a repo with localized memory."""
        from pacemaker.memory_localization.core import link_if_local_exists

        (ml_local_memory / "a.md").write_text("content a")
        (ml_local_memory / "b.md").write_text("content b")
        config = {"memory_localization_enabled": True}
        status, target = link_if_local_exists(
            str(ml_repo), str(ml_transcript_path), config
        )
        assert status == "linked_fresh"
        assert target is not None
        central = ml_enc_dir / "memory"
        assert central.is_symlink()
        assert central.resolve() == ml_local_memory.resolve()

    def test_linked_fresh_symlink_target_is_absolute(
        self, ml_central_base, ml_repo, ml_enc_dir, ml_transcript_path, ml_local_memory
    ):
        """Design invariant from issue #65: symlink target must be an absolute path."""
        from pacemaker.memory_localization.core import link_if_local_exists

        config = {"memory_localization_enabled": True}
        link_if_local_exists(str(ml_repo), str(ml_transcript_path), config)
        central = ml_enc_dir / "memory"
        raw_link = os.readlink(str(central))
        assert os.path.isabs(raw_link)

    def test_already_linked_is_idempotent(
        self, ml_central_base, ml_repo, ml_enc_dir, ml_transcript_path, ml_local_memory
    ):
        """Gherkin: Subsequent session in already-localized repo — no filesystem changes."""
        from pacemaker.memory_localization.core import link_if_local_exists

        central = ml_enc_dir / "memory"
        central.symlink_to(ml_local_memory.resolve())
        config = {"memory_localization_enabled": True}
        status, target = link_if_local_exists(
            str(ml_repo), str(ml_transcript_path), config
        )
        assert status == "already_linked"
        assert target == ml_local_memory.resolve()
        assert central.resolve() == ml_local_memory.resolve()

    def test_replaced_with_symlink_when_central_is_regular_folder(
        self, ml_central_base, ml_repo, ml_enc_dir, ml_transcript_path, ml_local_memory
    ):
        """Gherkin: Central has stale content; local wins."""
        from pacemaker.memory_localization.core import link_if_local_exists

        central = ml_enc_dir / "memory"
        central.mkdir()
        (central / "old_session.md").write_text("stale data")
        (ml_local_memory / "current.md").write_text("current")
        config = {"memory_localization_enabled": True}
        status, target = link_if_local_exists(
            str(ml_repo), str(ml_transcript_path), config
        )
        assert status == "replaced_with_symlink"
        assert central.is_symlink()
        assert central.resolve() == ml_local_memory.resolve()
        assert not (ml_enc_dir / "memory.bak_localize").exists()

    def test_relinked_when_central_has_wrong_symlink(
        self,
        ml_central_base,
        ml_repo,
        ml_enc_dir,
        ml_transcript_path,
        ml_local_memory,
        tmp_path,
    ):
        from pacemaker.memory_localization.core import link_if_local_exists

        other = tmp_path / "old_repo_clone"
        other.mkdir()
        central = ml_enc_dir / "memory"
        central.symlink_to(other.resolve())
        config = {"memory_localization_enabled": True}
        status, target = link_if_local_exists(
            str(ml_repo), str(ml_transcript_path), config
        )
        assert status == "relinked"
        assert central.resolve() == ml_local_memory.resolve()


class TestRacedButOk:
    """Finding 2 (HIGH): raced_but_ok branch had no test coverage."""

    def test_raced_but_ok_when_symlink_created_between_classify_and_link(
        self,
        ml_central_base,
        ml_repo,
        ml_enc_dir,
        ml_local_memory,
        ml_transcript_path,
        monkeypatch,
    ):
        """When os.symlink raises FileExistsError but central is now the correct
        symlink (another session created it between classify and act), we should
        return raced_but_ok without error."""
        from pacemaker.memory_localization import core as ml_core

        central = ml_enc_dir / "memory"
        # central does NOT exist — classify() returns "missing"
        assert not central.exists()

        # Patch os.symlink on the module so that on the first call it creates
        # the correct symlink AND raises FileExistsError (simulating a racing session).
        real_symlink = os.symlink

        def racing_symlink(src, dst, *args, **kw):
            real_symlink(src, dst)  # racing session's symlink lands on disk
            raise FileExistsError(str(dst))  # we pretend we lost the race

        monkeypatch.setattr(ml_core.os, "symlink", racing_symlink)

        config = {"memory_localization_enabled": True}
        status, target = ml_core.link_if_local_exists(
            str(ml_repo), str(ml_transcript_path), config
        )

        assert status == "raced_but_ok"
        assert target == ml_local_memory.resolve()
        assert central.is_symlink()


class TestReplaceWithSymlinkAtomicRollback:
    """Finding 3 (MEDIUM): OSError rollback path in replace_with_symlink_atomic had no coverage."""

    def test_replace_with_symlink_atomic_rolls_back_on_oserror(
        self,
        ml_central_base,
        ml_enc_dir,
        ml_repo,
        ml_local_memory,
        monkeypatch,
    ):
        """When os.symlink raises OSError after rename-to-backup, the backup must
        be renamed back to central so the original directory and its contents are
        restored."""
        from pacemaker.memory_localization import core as ml_core

        # Set up a regular-folder central with real content.
        central = ml_enc_dir / "memory"
        central.mkdir()
        marker = central / "content.md"
        marker.write_text("original-content")

        # Force os.symlink to fail with OSError.
        def broken_symlink(src, dst, *a, **kw):
            raise OSError("simulated failure")

        monkeypatch.setattr(ml_core.os, "symlink", broken_symlink)

        with pytest.raises(OSError):
            ml_core.replace_with_symlink_atomic(central, ml_local_memory.resolve())

        # Rollback assertions: central must be restored as a regular directory.
        assert central.is_dir(), "central should be restored to a regular directory"
        assert (central / "content.md").exists(), "content.md should be intact"
        assert (central / "content.md").read_text() == "original-content"
        backup = ml_enc_dir / "memory.bak_localize"
        assert not backup.exists(), "backup should be renamed back, not left behind"
