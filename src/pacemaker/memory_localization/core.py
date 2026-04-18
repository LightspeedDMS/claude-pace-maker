"""
Memory Localization core implementation.

Public API:
  classify_central(central, expected_local) -> str
  assert_safe_to_destroy(path) -> None
  central_memory_path_from_transcript(transcript_path) -> Path
  central_memory_path_from_cwd(cwd) -> Path
  local_memory_path(project_root) -> Path
  replace_with_symlink_atomic(central, target) -> None
  link_if_local_exists(cwd, transcript_path, config) -> tuple[str, Path | None]
  seed_and_link(cwd) -> int
  unlink_and_restore(cwd) -> int

Environment:
  PACEMAKER_CENTRAL_BASE  override CENTRAL_BASE path (required in test mode)
  PACEMAKER_TEST_MODE     when "1", PACEMAKER_CENTRAL_BASE must be set
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from ..logger import log_error, log_debug

# ── Environment variables ─────────────────────────────────────────────────────
_ENV_CENTRAL_BASE = "PACEMAKER_CENTRAL_BASE"
_ENV_TEST_MODE = "PACEMAKER_TEST_MODE"

# ── Local folder name ─────────────────────────────────────────────────────────
LOCAL_FOLDER_NAME = ".claude-memory"

# ── Git timeout ───────────────────────────────────────────────────────────────
_GIT_TIMEOUT = 2.0


def _resolve_central_base() -> Path:
    """Compute the CENTRAL_BASE path.

    Resolution order:
    1. PACEMAKER_CENTRAL_BASE env var (always honoured when set)
    2. ~/.claude/projects/ production default

    Test-mode enforcement: raises RuntimeError when PACEMAKER_TEST_MODE=1
    and PACEMAKER_CENTRAL_BASE is unset to prevent polluting production.
    """
    env_val = os.environ.get(_ENV_CENTRAL_BASE)
    if env_val:
        return Path(env_val)
    if os.environ.get(_ENV_TEST_MODE) == "1":
        raise RuntimeError(
            "PACEMAKER_CENTRAL_BASE must be set in test mode — "
            "conftest.py must provide a tmp path via monkeypatch.setenv"
        )
    return Path.home() / ".claude" / "projects"


def __getattr__(name: str) -> Path:
    """Module-level dynamic attribute lookup.

    Allows ``CENTRAL_BASE`` to be resolved on every access, so tests that
    override ``PACEMAKER_CENTRAL_BASE`` via ``monkeypatch.setenv`` see the
    updated value. Raises AttributeError for unknown names so normal
    attribute lookup proceeds for other module globals.
    """
    if name == "CENTRAL_BASE":
        return _resolve_central_base()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _is_in_git_repo(cwd: str) -> bool:
    """Return True if cwd is inside a git repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            timeout=_GIT_TIMEOUT,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _resolve_workspace_root(cwd: str) -> Optional[str]:
    """Return canonical git repo root for cwd, or None when not a git repo.

    Delegates to resolve_workspace_root from session_registry for canonical
    path resolution (symlink resolution, logging, timeout handling).
    Returns None if cwd is not inside a git repository.
    """
    if not _is_in_git_repo(cwd):
        return None
    from ..session_registry.workspace import resolve_workspace_root

    return resolve_workspace_root(cwd)


# ── Safe path-boundary check ──────────────────────────────────────────────────


def _is_under(path: Path, base: Path) -> bool:
    """Return True if path's location is at or under base.

    Canonicalizes only the parent directory of path (not path itself) so that
    symlink candidates whose targets live outside base are still correctly
    identified as being under base.  The base is fully resolved so that
    platform-level indirections (e.g. /tmp -> /private/tmp on macOS) are
    handled correctly.

    Uses Path.relative_to() for correct boundary semantics — no false
    positives from string prefix matching (e.g. /tmp/projects-evil vs
    /tmp/projects).
    """
    try:
        canonical_parent = path.parent.resolve()
        candidate = canonical_parent / path.name
        base_resolved = base.resolve()
        candidate.relative_to(base_resolved)
        return True
    except ValueError:
        return False
    except OSError as exc:
        log_error(
            "memory_localization",
            f"_is_under: OSError resolving {path!r} or {base!r}: {exc}",
        )
        return False


def _assert_backup_safe_to_destroy(backup: Path) -> None:
    """Assert that a backup path created by replace_with_symlink_atomic is safe.

    Backup name is "memory.bak_localize" (central.with_suffix(".bak_localize")).
    Checks:
    - backup is under CENTRAL_BASE
    - backup.name == "memory.bak_localize"
    """
    assert _is_under(
        backup, _resolve_central_base()
    ), f"backup is outside central base: {backup!r} not under {_resolve_central_base()!r}"
    assert (
        backup.name == "memory.bak_localize"
    ), f"unexpected backup name: {backup.name!r}"


# ── Path helpers ──────────────────────────────────────────────────────────────


def central_memory_path_from_transcript(transcript_path: str) -> Path:
    """Return Path(transcript_path).parent / "memory".

    Raises AssertionError if the result is outside CENTRAL_BASE.
    """
    central = Path(transcript_path).resolve().parent / "memory"
    assert _is_under(central, _resolve_central_base()), (
        f"transcript is outside CENTRAL_BASE: {transcript_path!r} "
        f"not under {_resolve_central_base()!r}"
    )
    return central


def central_memory_path_from_cwd(cwd: str) -> Path:
    """Scan CENTRAL_BASE for an encoded folder whose .jsonl records cwd.

    Raises ValueError when:
    - cwd is not a git repo
    - no Claude session has been run for this project
    """
    project_root = _resolve_workspace_root(cwd)
    if project_root is None:
        raise ValueError(f"not a git repo: {cwd!r}")

    central_base = _resolve_central_base()
    matches = []
    if central_base.exists():
        for enc_dir in central_base.iterdir():
            if not enc_dir.is_dir():
                continue
            for jsonl in enc_dir.glob("*.jsonl"):
                try:
                    with jsonl.open() as fh:
                        for line in fh:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                data = json.loads(line)
                            except json.JSONDecodeError as exc:
                                log_debug(
                                    "memory_localization",
                                    f"skipping malformed JSON line in {jsonl!r}: {exc}",
                                )
                                continue
                            if data.get("cwd") == project_root:
                                matches.append(enc_dir / "memory")
                                break
                except OSError as exc:
                    log_debug(
                        "memory_localization",
                        f"cannot read {jsonl!r}, skipping: {exc}",
                    )
                    continue

    if not matches:
        raise ValueError(
            f"no Claude session has run here — start one first: {project_root!r}"
        )
    unique = list({str(m.resolve()): m for m in matches}.values())
    if len(unique) > 1:
        raise ValueError(
            f"ambiguous encoding — multiple encoded dirs for "
            f"{project_root!r}: {unique!r}"
        )
    return unique[0]


def local_memory_path(project_root: Path) -> Path:
    """Return project_root / '.claude-memory'."""
    return project_root / LOCAL_FOLDER_NAME


# ── State classification ──────────────────────────────────────────────────────


def classify_central(central: Path, expected_local: Path) -> str:
    """Classify the state of the central memory path.

    Returns one of:
      missing           — central does not exist (and is not a dangling symlink)
      correct_symlink   — central is a symlink pointing to expected_local
      wrong_symlink     — central is a symlink pointing elsewhere
      regular_folder    — central is a plain directory
      unknown           — central exists but is neither dir nor symlink
      permission_denied — PermissionError reading central
    """
    try:
        if not central.exists() and not central.is_symlink():
            return "missing"
        if central.is_symlink():
            if central.resolve() == expected_local.resolve():
                return "correct_symlink"
            return "wrong_symlink"
        if central.is_dir():
            return "regular_folder"
        return "unknown"
    except PermissionError:
        return "permission_denied"


# ── Safety guard ──────────────────────────────────────────────────────────────


def assert_safe_to_destroy(path: Path) -> None:
    """Assert that a "memory" path is safe to rmtree.

    Raises AssertionError if:
    - path is outside CENTRAL_BASE
    - path.name != "memory"
    """
    assert _is_under(
        path, _resolve_central_base()
    ), f"path is outside central base: {path!r} not under {_resolve_central_base()!r}"
    assert path.name == "memory", f"path is not a memory folder: {path.name!r}"


# ── Atomic symlink replacement ────────────────────────────────────────────────


def replace_with_symlink_atomic(central: Path, target: Path) -> None:
    """Replace central (folder or symlink) with a symlink to target.

    Algorithm:
    1. If central exists or is a dangling symlink, rename it to memory.bak_localize.
    2. Create central -> target symlink.
    3. Remove the backup:
       - if it was a symlink: unlink it
       - if it was a directory: validate it is under CENTRAL_BASE and named
         "memory.bak_localize", then rmtree
    4. On OSError during step 2: restore backup if present; re-raise.
    """
    backup = central.with_suffix(".bak_localize")
    # Step 1: move existing central aside
    if central.exists() or central.is_symlink():
        central.rename(backup)
    # Step 2: create symlink
    try:
        central.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(str(target), str(central))
        # Step 3: remove backup — validate backup path (not central, which is now
        # a symlink to target outside CENTRAL_BASE)
        if backup.is_symlink():
            backup.unlink()
        elif backup.exists():
            _assert_backup_safe_to_destroy(backup)
            shutil.rmtree(str(backup))
    except OSError:
        # Step 4: roll back — restore backup if symlink was not created
        if not central.exists() and not central.is_symlink():
            if backup.exists() or backup.is_symlink():
                backup.rename(central)
        log_error("memory_localization", f"atomic replace failed for {central!r}")
        raise


# ── Flow A: SessionStart auto-link ────────────────────────────────────────────


def link_if_local_exists(
    cwd: str,
    transcript_path: str,
    config: dict,
) -> Tuple[str, Optional[Path]]:
    """Auto-link central memory to .claude-memory/ at SessionStart.

    Returns (status, target_path) where status is one of:
      disabled           — feature disabled in config
      not_git_repo       — cwd is not inside a git repo
      no_local_folder    — .claude-memory/ does not exist in repo
      local_not_directory — .claude-memory exists but is not a directory
      linked_fresh       — newly created symlink (central was missing)
      already_linked     — central is already the correct symlink
      replaced_with_symlink — central was a regular folder, replaced
      relinked           — central was a wrong symlink, corrected
      permission_denied  — cannot access central
      unexpected_state   — central is in an unrecognised state
      raced_but_ok       — concurrent session beat us; symlink is now correct
      failed             — OSError during operation
    """
    if not config.get("memory_localization_enabled", True):
        return ("disabled", None)

    project_root = _resolve_workspace_root(cwd)
    if project_root is None:
        return ("not_git_repo", None)

    local = local_memory_path(Path(project_root))
    if not local.exists() and not local.is_symlink():
        return ("no_local_folder", None)
    if not local.is_dir():
        return ("local_not_directory", None)

    central = central_memory_path_from_transcript(transcript_path)
    target = local.resolve()
    state = classify_central(central, local)

    try:
        if state == "correct_symlink":
            return ("already_linked", target)

        if state == "missing":
            central.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(str(target), str(central))
            return ("linked_fresh", target)

        if state == "wrong_symlink":
            replace_with_symlink_atomic(central, target)
            return ("relinked", target)

        if state == "regular_folder":
            replace_with_symlink_atomic(central, target)
            return ("replaced_with_symlink", target)

        if state == "permission_denied":
            log_error(
                "memory_localization",
                f"permission denied accessing central memory at {central!r}",
            )
            return ("permission_denied", None)

        log_error(
            "memory_localization",
            f"central memory in unexpected state {state!r} at {central!r}",
        )
        return ("unexpected_state", None)

    except FileExistsError:
        state2 = classify_central(central, local)
        if state2 == "correct_symlink":
            return ("raced_but_ok", target)
        raise

    except OSError:
        log_error("memory_localization", f"OSError linking {central!r} -> {target!r}")
        return ("failed", None)


# ── Flow B: CLI seed ───────────────────────────────────────────────────────────


def seed_and_link(cwd: str) -> int:
    """Seed .claude-memory/ and link it to central memory (CLI command).

    Returns 0 on success.
    Raises ValueError on user-facing error conditions.
    """
    project_root = _resolve_workspace_root(cwd)
    if project_root is None:
        raise ValueError(f"not a git repo: {cwd!r}")

    central = central_memory_path_from_cwd(cwd)
    local = local_memory_path(Path(project_root))
    state = classify_central(central, local)

    if local.exists() and state == "correct_symlink":
        log_debug("memory_localization", "already localized — nothing to do")
        return 0

    if local.exists():
        raise ValueError(
            f".claude-memory/ already exists — refusing to overwrite: {local!r}"
        )

    if state == "wrong_symlink":
        raw = os.readlink(str(central))
        raise ValueError(f"central symlinks to unexpected location: {raw!r}")

    if state == "correct_symlink":
        raise ValueError(
            ".claude-memory/ is missing but central symlink already exists — "
            "repair manually"
        )

    if state == "permission_denied":
        raise ValueError("cannot read central memory folder (permission denied)")

    if state == "unknown":
        raise ValueError(f"central in unexpected state: {central!r}")

    # state is "missing" or "regular_folder"
    local.mkdir()
    file_count = 0
    if state == "regular_folder":
        for item in central.iterdir():
            dest = local / item.name
            if item.is_dir():
                shutil.copytree(str(item), str(dest))
            else:
                shutil.copy2(str(item), str(dest))
            file_count += 1
        assert_safe_to_destroy(central)
        replace_with_symlink_atomic(central, local.resolve())
        log_debug(
            "memory_localization",
            f"seeded {file_count} files. next: git add .claude-memory && git commit",
        )
    else:
        central.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(str(local.resolve()), str(central))
        log_debug(
            "memory_localization",
            "created empty .claude-memory/ and symlinked. "
            "commit: git add .claude-memory",
        )

    return 0


# ── Flow C: CLI unlink ─────────────────────────────────────────────────────────


def unlink_and_restore(cwd: str) -> int:
    """Remove central symlink and restore contents as a regular directory.

    Returns 0 on success.
    Raises ValueError on user-facing error conditions.
    """
    project_root = _resolve_workspace_root(cwd)
    if project_root is None:
        raise ValueError(f"not a git repo: {cwd!r}")

    central = central_memory_path_from_cwd(cwd)
    local = local_memory_path(Path(project_root))
    state = classify_central(central, local)

    if state != "correct_symlink":
        raise ValueError(
            f"central is not symlinked to .claude-memory/ — "
            f"nothing to unlink (state: {state!r})"
        )
    if not local.exists():
        raise ValueError(".claude-memory/ is missing — cannot restore from nothing")

    central.unlink()
    central.mkdir()
    file_count = 0
    for item in local.iterdir():
        dest = central / item.name
        if item.is_dir():
            shutil.copytree(str(item), str(dest))
        else:
            shutil.copy2(str(item), str(dest))
        file_count += 1

    log_debug(
        "memory_localization",
        f"unlinked. copied {file_count} files back to central memory. "
        "note: .claude-memory/ left in place — "
        "run `git rm -r .claude-memory` to remove from repo",
    )
    return 0
