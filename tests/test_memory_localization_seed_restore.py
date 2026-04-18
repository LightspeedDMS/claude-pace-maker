"""
Unit tests for memory_localization: seed_and_link (Flow B),
unlink_and_restore (Flow C), and central_memory_path_from_cwd.

All tests drive through real filesystem state without mocking.
Shared fixtures from tests/conftest.py.

PACEMAKER_TEST_MODE=1 set globally by conftest.py.
PACEMAKER_CENTRAL_BASE set per-test via ml_central_base fixture.
"""

import json

import pytest


# ---------------------------------------------------------------------------
# Helper: write a JSONL that encodes cwd for central_memory_path_from_cwd
# ---------------------------------------------------------------------------


def _write_cwd_jsonl(enc_dir, project_root):
    """Write a session .jsonl inside enc_dir with cwd=project_root."""
    enc_dir.mkdir(parents=True, exist_ok=True)
    jsonl = enc_dir / "session.jsonl"
    jsonl.write_text(json.dumps({"cwd": str(project_root)}) + "\n")


# ---------------------------------------------------------------------------
# central_memory_path_from_cwd
# ---------------------------------------------------------------------------


class TestCentralMemoryPathFromCwd:

    def test_finds_correct_encoding(self, ml_central_base, ml_repo):
        from pacemaker.memory_localization.core import central_memory_path_from_cwd

        enc = ml_central_base / "enc_abc"
        _write_cwd_jsonl(enc, ml_repo)
        result = central_memory_path_from_cwd(str(ml_repo))
        assert result == enc / "memory"

    def test_raises_when_no_sessions(self, ml_central_base, ml_repo):
        from pacemaker.memory_localization.core import central_memory_path_from_cwd

        with pytest.raises(ValueError, match="no Claude session"):
            central_memory_path_from_cwd(str(ml_repo))


# ---------------------------------------------------------------------------
# seed_and_link (Flow B)
# ---------------------------------------------------------------------------


class TestSeedAndLink:

    def test_seed_fresh_copies_files_and_symlinks(self, ml_central_base, ml_repo):
        """Gherkin: Seed a fresh project; central has N files to migrate."""
        from pacemaker.memory_localization.core import seed_and_link

        enc = ml_central_base / "enc_proj"
        enc.mkdir(parents=True)
        central = enc / "memory"
        central.mkdir()
        for i in range(3):
            (central / f"file{i}.md").write_text(f"content {i}")
        _write_cwd_jsonl(enc, ml_repo)
        result = seed_and_link(str(ml_repo))
        assert result == 0
        local = ml_repo / ".claude-memory"
        assert local.is_dir()
        copied_files = list(local.iterdir())
        assert len(copied_files) == 3
        assert central.is_symlink()
        assert central.resolve() == local.resolve()

    def test_seed_missing_central_creates_empty_local_and_symlinks(
        self, ml_central_base, ml_repo
    ):
        """When central is missing: create empty .claude-memory and symlink."""
        from pacemaker.memory_localization.core import seed_and_link

        enc = ml_central_base / "enc_proj"
        _write_cwd_jsonl(enc, ml_repo)
        # No memory folder at enc/memory
        result = seed_and_link(str(ml_repo))
        assert result == 0
        local = ml_repo / ".claude-memory"
        assert local.is_dir()
        central = enc / "memory"
        assert central.is_symlink()
        assert central.resolve() == local.resolve()

    def test_refuses_when_local_exists_and_central_not_symlink(
        self, ml_central_base, ml_repo
    ):
        """Gherkin: Refuse to seed when local folder already exists."""
        from pacemaker.memory_localization.core import seed_and_link

        local = ml_repo / ".claude-memory"
        local.mkdir()
        enc = ml_central_base / "enc_proj"
        enc.mkdir(parents=True)
        central = enc / "memory"
        central.mkdir()
        _write_cwd_jsonl(enc, ml_repo)
        with pytest.raises(ValueError, match="already exists"):
            seed_and_link(str(ml_repo))

    def test_idempotent_when_already_correct_symlink(self, ml_central_base, ml_repo):
        """Gherkin: Idempotent re-run on already-localized project."""
        from pacemaker.memory_localization.core import seed_and_link

        local = ml_repo / ".claude-memory"
        local.mkdir()
        enc = ml_central_base / "enc_proj"
        enc.mkdir(parents=True)
        central = enc / "memory"
        central.symlink_to(local.resolve())
        _write_cwd_jsonl(enc, ml_repo)
        result = seed_and_link(str(ml_repo))
        assert result == 0
        # Filesystem state unchanged
        assert central.is_symlink()
        assert central.resolve() == local.resolve()


# ---------------------------------------------------------------------------
# unlink_and_restore (Flow C)
# ---------------------------------------------------------------------------


class TestUnlinkAndRestore:

    def test_unlinks_symlink_and_copies_files_back(self, ml_central_base, ml_repo):
        """Gherkin: Unlink a localized project and restore central memory."""
        from pacemaker.memory_localization.core import unlink_and_restore

        local = ml_repo / ".claude-memory"
        local.mkdir()
        (local / "mem1.md").write_text("mem1")
        (local / "mem2.md").write_text("mem2")
        enc = ml_central_base / "enc_proj"
        enc.mkdir(parents=True)
        central = enc / "memory"
        central.symlink_to(local.resolve())
        _write_cwd_jsonl(enc, ml_repo)
        result = unlink_and_restore(str(ml_repo))
        assert result == 0
        assert not central.is_symlink()
        assert central.is_dir()
        assert (central / "mem1.md").exists()
        assert (central / "mem2.md").exists()
        # Local folder left untouched
        assert local.is_dir()

    def test_raises_when_central_is_not_correct_symlink(self, ml_central_base, ml_repo):
        """Gherkin: Refuse to unlink when central is a regular directory."""
        from pacemaker.memory_localization.core import unlink_and_restore

        enc = ml_central_base / "enc_proj"
        enc.mkdir(parents=True)
        central = enc / "memory"
        central.mkdir()
        _write_cwd_jsonl(enc, ml_repo)
        with pytest.raises(ValueError, match="not symlinked"):
            unlink_and_restore(str(ml_repo))
