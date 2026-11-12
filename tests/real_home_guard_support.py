"""Support for the #188 guard: detect the suite creating the real user's
``~/.claude-pace-maker``.

Stdlib only, flat sibling of the tests (like ``prebaked_home_support``). It
reads the directory's state and never creates, modifies or removes anything.
The real home comes from the passwd entry, NOT from ``$HOME``: the suite points
``$HOME`` at a throwaway directory, which is exactly what is being protected.
"""

import os
import pwd
from typing import Callable, List, NamedTuple, Optional

MAX_LISTED_FILES = 20
NO_PASSWD_NOTICE = "real-home guard disabled: uid {uid} has no passwd entry"


class Snapshot(NamedTuple):
    path: str
    absent_at_start: bool


def real_pace_maker_dir(
    lookup: Callable[[int], "pwd.struct_passwd"] = pwd.getpwuid,
    uid: Optional[int] = None,
) -> Optional[str]:
    """The real user's ``.claude-pace-maker`` (passwd home, never ``$HOME``).

    None when the uid (default: the current one) has no passwd entry, common in
    CI containers: there is no "real home" to protect then. Only that
    ``KeyError`` is caught; any other lookup failure is a real problem and
    propagates.
    """
    try:
        entry = lookup(os.getuid() if uid is None else uid)
    except KeyError:
        return None
    return os.path.join(entry.pw_dir, ".claude-pace-maker")


def disabled_notice(uid: int) -> str:
    """The one-line notice shown when the guard cannot run for ``uid``."""
    return NO_PASSWD_NOTICE.format(uid=uid)


def take_snapshot(
    lookup: Callable[[int], "pwd.struct_passwd"] = pwd.getpwuid,
    uid: Optional[int] = None,
) -> Optional[Snapshot]:
    """Snapshot the real directory, or None when the uid has no passwd entry
    and the guard therefore cannot run (the caller shows ``disabled_notice``)."""
    path = real_pace_maker_dir(lookup, uid)
    return None if path is None else snapshot(path)


def snapshot(path: str) -> Snapshot:
    """Record whether ``path`` exists (a dangling symlink counts as existing)."""
    return Snapshot(path=path, absent_at_start=not os.path.lexists(path))


def _list_files(path: str) -> List[str]:
    """Relative file names under ``path``; unreadable parts are listed, not hidden."""
    found: List[str] = []

    def record_unreadable(err: OSError) -> None:
        found.append(f"<unreadable: {err.filename}: {err.strerror}>")

    for root, _dirs, files in os.walk(path, onerror=record_unreadable):
        for name in files:
            found.append(os.path.relpath(os.path.join(root, name), path))
    return sorted(found)


def leak_message(before: Optional[Snapshot]) -> Optional[str]:
    """A failure message if the directory appeared during the run, else None.

    Only a run that started without the directory is judged: when it already
    existed (a live install whose own hooks write there) a test leak cannot be
    told apart from legitimate activity. ``None`` (guard disabled, see
    take_snapshot) is never a leak.
    """
    if before is None:
        return None
    if not before.absent_at_start or not os.path.lexists(before.path):
        return None
    files = _list_files(before.path)
    shown = files[:MAX_LISTED_FILES]
    lines = [
        f"TEST ISOLATION LEAK (#188): the suite created {before.path}, "
        "which did not exist when it started. Files:"
    ]
    lines.extend(f"  {name}" for name in shown)
    if len(files) > len(shown):
        lines.append(f"  ... and {len(files) - len(shown)} more")
    return "\n".join(lines)
