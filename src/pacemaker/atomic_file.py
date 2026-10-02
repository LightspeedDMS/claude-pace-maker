"""
Atomic file replacement for files shared between concurrent hook processes.

Bug #161: ~/.claude-pace-maker/state.json is read and rewritten by every hook
of every concurrent Claude Code session. A plain ``open(path, "w")`` truncates
the file to zero bytes before the new content is streamed in, so another
process reading in that window sees an empty or half-written file.

``atomic_write_text`` writes the content to a temp file in the SAME directory
and then ``os.replace()``s it over the target. POSIX guarantees a reader sees
either the complete old file or the complete new one, never anything between.

STDLIB-ONLY leaf module (no imports from other pacemaker modules).
"""

import os
import stat
import uuid


def atomic_write_text(path: str, text: str) -> None:
    """Atomically replace ``path`` with ``text``.

    - The parent directory is created if missing.
    - The mode of an existing target is preserved; a new file gets the mode a
      plain ``open(path, "w")`` would give it (0o666 minus the umask).
    - The temp file is always removed if anything fails; the previous file is
      then left exactly as it was.
    - A symlinked ``path`` is resolved first (``os.path.realpath``): the file
      the link points to is replaced and the link itself is kept. This is the
      write-through behaviour of the ``open(path, "w")`` this helper replaces;
      replacing the link itself would silently turn a user's symlinked state
      file (e.g. one managed by a dotfiles tool) into a regular file. A dangling
      link gets its target created, as ``open(path, "w")`` would.

    Not fsync'd, on purpose (hook latency): the guarantee is atomic visibility
    to concurrent readers, not durability across power loss. A process killed
    between creating the temp file and the ``os.replace`` (SIGKILL, power loss)
    can leave an orphan ``<name>.tmp.<pid>.<rand>`` file behind; it is never
    read and is harmless, and nothing cleans it up.

    Raises OSError (or whatever the write raises) -- callers decide whether a
    failed save is fatal. Nothing is swallowed here.
    """
    path = os.path.realpath(path)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)

    try:
        existing_mode = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        existing_mode = None

    # Unique per process AND per call: two hooks (or two saves in one hook)
    # can never share a temp file. O_EXCL turns any collision into an error
    # instead of two writers interleaving into one file.
    temp_path = f"{path}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            if existing_mode is not None:
                os.fchmod(handle.fileno(), existing_mode)
            handle.write(text)
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            # Deliberate best-effort cleanup: the ORIGINAL failure is
            # re-raised below, which is the error the caller must see.
            pass
        raise
