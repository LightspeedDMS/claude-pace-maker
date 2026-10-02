"""Shared helpers for the tests that bootstrap one real venv and reuse it
(``tests/test_bootstrap_plugin.py``, ``tests/test_plugin_lazy_init.py``).
Issue #168.

NOT a test module (no ``test_`` prefix -- ``scripts/run_tests.sh`` globs
``tests/test_*.py``). Test files import it as a flat sibling
(``from prebaked_home_support import ...``), the same convention
``tests/declare_intent_harness.py`` uses.

Why this exists: both files build a "prebaked home" (a real venv plus a real
``pip install``, a few hundred MB) and used to ``shutil.copytree`` the whole
home for every test that needs a mutable copy. Under concurrent disk I/O those
copies stalled past the per-test caps. Now:

* ``clone_home`` hard-links the venv tree (no data is written) and really
  copies everything else, because tests rewrite those files in place;
* ``freeze_venv`` makes the prebaked venv files read-only, so an in-place write
  through a clone fails loudly instead of silently corrupting the shared inode;
* ``remove_tree`` deletes clones and the prebaked home when they are done;
* ``run_bounded`` gives every subprocess a deadline and kills its whole
  process group, so a stall fails with a readable message rather than a pytest
  kill.
"""

import os
import shutil
import signal
import stat
import subprocess
from pathlib import Path
from typing import Mapping, Optional, Sequence, Union

import pytest

PathLike = Union[str, "os.PathLike[str]"]

PACEMAKER_DIR_NAME = ".claude-pace-maker"
VENV_DIR_NAME = "venv"

# Big trees that are hard-linked into clones instead of copied. The venv is
# required (a prebaked home without one is a broken fixture); pip's cache is
# optional because it only exists when the prebake ran pip with caching on.
# Both are read-only for the tests: they replace venv files by unlink + write
# and never touch the cache.
REQUIRED_SHARED_TREES = (Path(PACEMAKER_DIR_NAME) / VENV_DIR_NAME,)
OPTIONAL_SHARED_TREES = (Path(".cache") / "pip",)

# After SIGKILL to the process group, how long to wait for the pipes to reach
# EOF before giving up on reading the partial output.
_POST_KILL_DRAIN_SEC = 10


def _shared_trees(prebaked: Path) -> list:
    """Relative paths of the trees to share: every required one (a missing one
    surfaces as FileNotFoundError at link time) plus the optional ones that
    exist."""
    present_optional = [t for t in OPTIONAL_SHARED_TREES if (prebaked / t).is_dir()]
    return list(REQUIRED_SHARED_TREES) + present_optional


def clone_home(prebaked_home: PathLike, dest: PathLike) -> Path:
    """Clone a prebaked home into ``dest`` so a test can mutate it.

    Everything is copied for real except the shared trees (the venv and pip's
    cache), whose regular files are hard-linked (directories and symlinks are
    recreated). Safe for the tests' mutations: they replace venv files by
    unlink + write (a new inode), and ``freeze_venv`` makes any in-place write
    fail.

    Raises ``FileNotFoundError`` when the prebaked home has no venv, and
    ``OSError`` if hard links are impossible -- there is no silent fallback to
    a full copy.
    """
    prebaked = Path(prebaked_home)
    target = Path(dest)
    shared = _shared_trees(prebaked)
    skipped = {os.fspath(prebaked / tree) for tree in shared}

    def _skip_shared(directory: str, names: Sequence[str]) -> list:
        return [n for n in names if os.path.join(directory, n) in skipped]

    shutil.copytree(prebaked, target, symlinks=True, ignore=_skip_shared)
    for tree in shared:
        shutil.copytree(
            prebaked / tree,
            target / tree,
            symlinks=True,
            copy_function=os.link,
        )
    return target


def freeze_venv(prebaked_home: PathLike) -> None:
    """Make every regular file under the prebaked shared trees read-only.

    Hard-linked clones share these inodes, so an in-place write through a
    clone would corrupt the prebaked home for every later test. Read-only
    files turn that into a ``PermissionError``. Symlinks are skipped:
    ``os.chmod`` follows them, and ``venv/bin/python3`` points at the real
    system interpreter.
    """
    prebaked = Path(prebaked_home)
    read_only_mask = ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    for tree in _shared_trees(prebaked):
        for directory, _subdirs, filenames in os.walk(
            prebaked / tree, followlinks=False
        ):
            for name in filenames:
                path = os.path.join(directory, name)
                if os.path.islink(path):
                    continue
                os.chmod(path, os.stat(path).st_mode & read_only_mask)


def remove_tree(path: PathLike) -> None:
    """Delete a clone or the prebaked home; a path already gone is fine.

    Read-only files (``freeze_venv``) need no special handling: removing a
    file needs write permission on its DIRECTORY, not on the file, and
    ``freeze_venv`` leaves directories writable. Hard-linked files only lose
    one link, so the prebaked tree is unaffected. Any other error propagates.
    """
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass


def run_bounded(
    cmd: Sequence[str],
    *,
    timeout: float,
    env: Optional[Mapping[str, str]] = None,
    input_data: Optional[str] = None,
    cwd: Optional[str] = None,
    label: Optional[str] = None,
) -> "subprocess.CompletedProcess[str]":
    """``subprocess.run(..., capture_output=True, text=True)`` with a deadline.

    On timeout the whole process group is killed (the hook is bash -> python
    -> pip; killing only bash would leave a grandchild holding the pipes open
    and hang the cleanup too) and the test fails with ``label``, the deadline
    and whatever output was captured, instead of being killed by pytest with
    no context.
    """
    proc = subprocess.Popen(
        list(cmd),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=None if env is None else dict(env),
        cwd=cwd,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(input=input_data, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # exited between the timeout and the kill
        try:
            stdout, stderr = proc.communicate(timeout=_POST_KILL_DRAIN_SEC)
        except subprocess.TimeoutExpired:
            stdout, stderr = "<unreadable: a descendant escaped the kill>", ""
        pytest.fail(
            f"{label or cmd[0]} did not finish within {timeout:g}s "
            f"(process group killed).\nstdout:\n{stdout}\nstderr:\n{stderr}",
            pytrace=False,
        )
    return subprocess.CompletedProcess(list(cmd), proc.returncode, stdout, stderr)
