"""Monitoring data directory resolver (story #187).

Single place that decides where pace-maker's *monitoring* SQLite databases
(``usage.db``, ``session_registry.db``) live.

Default (setting absent or null): the per-user ``~/.claude-pace-maker``,
exactly as before this module existed.

Opt-in shared mode: the per-user ``~/.claude-pace-maker/config.json`` holds
``"shared_data_dir": "/var/lib/claude-pace-maker"``. Only the monitoring
SQLite databases move there. Code, config, rules, state, hooks, logs and
the ``secrets`` table always stay per user (see README "Shared data
directory").

Constraints:
- stdlib only and NO import of ``logger``/``constants`` (they import this).
- Nothing read from the shared directory is ever used as a path,
  interpreter, module or command; it only holds SQLite files.
- A configured-but-unusable shared dir raises ``SharedDataDirError``; there
  is deliberately no fallback to the per-user dir.
"""

import functools
import json
import os
import pwd
import stat
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional, Tuple

PER_USER_DIR_NAME = ".claude-pace-maker"
CONFIG_FILE_NAME = "config.json"
SHARED_DATA_DIR_KEY = "shared_data_dir"
SECRETS_DB_FILE = "secrets.db"
SHARED_DB_FILES = ("usage.db", "session_registry.db")

# Stand-in "directory" for a malformed shared_data_dir (wrong type, empty,
# relative, or the per-user dir). Path resolution must never raise (it runs at
# import time in every hook, the MCP server and the CLI), so a bad setting
# resolves to this deliberately non-existent absolute path. It is recognised
# as a shared path, so every DB open fails with the real configuration error
# (validate_shared_data_dir) instead of writing anywhere; secrets still map to
# the per-user store.
INVALID_SHARED_DIR = "/nonexistent/pace-maker-invalid-shared_data_dir"

# Shared DB files must be group read/write regardless of the process umask.
SHARED_DB_MODE = 0o660
_GROUP_RW = 0o060


class SharedDataDirError(RuntimeError):
    """The configured shared data dir is invalid, missing or not writable."""


def per_user_dir() -> str:
    """Per-user pace-maker dir (config, code, rules, state, secrets)."""
    return os.path.join(os.path.expanduser("~"), PER_USER_DIR_NAME)


def per_user_config_path() -> str:
    return os.path.join(per_user_dir(), CONFIG_FILE_NAME)


@functools.lru_cache(maxsize=1)
def os_user() -> str:
    """Login name of the OS user running this process (cached: the uid is fixed
    for the life of the process, and NSS lookups can be slow with sssd/LDAP)."""
    return pwd.getpwuid(os.getuid()).pw_name


# Cache keyed on the config file's identity so hot DB paths do not re-parse
# JSON on every connection, yet a changed config is picked up immediately.
_setting_cache: Dict[str, Tuple[Tuple[int, int], Any]] = {}


def _raw_setting() -> Any:
    """Return the raw ``shared_data_dir`` value, or None when not configured.

    A missing/unreadable/malformed config.json means "not configured"
    (legacy behaviour): other code paths already handle a broken config.
    """
    path = per_user_config_path()
    try:
        st = os.stat(path)
    except OSError:
        return None
    ident = (st.st_mtime_ns, st.st_size)
    cached = _setting_cache.get(path)
    if cached is not None and cached[0] == ident:
        return cached[1]
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, RecursionError):
        # Pathologically nested JSON raises RecursionError, not ValueError.
        # Still legacy mode (other code paths, and `pace-maker status`, already
        # cope with / report a broken config); hooks stay silent.
        _setting_cache[path] = (ident, None)  # do not re-parse on every DB open
        return None
    value = data.get(SHARED_DATA_DIR_KEY) if isinstance(data, dict) else None
    _setting_cache[path] = (ident, value)
    return value


def shared_data_dir() -> Optional[str]:
    """Absolute shared dir from the per-user config, or None (legacy mode).

    Raises:
        SharedDataDirError: the setting is present but not a usable path.
    """
    value = _raw_setting()
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SharedDataDirError(
            f"pace-maker: {SHARED_DATA_DIR_KEY} must be a non-empty string, "
            f"got {value!r}"
        )
    expanded = os.path.expanduser(value.strip())
    if not os.path.isabs(expanded):
        raise SharedDataDirError(
            f"pace-maker: {SHARED_DATA_DIR_KEY} '{value}' must be an absolute path"
        )
    normalized = os.path.normpath(expanded)
    if os.path.realpath(normalized) == os.path.realpath(per_user_dir()):
        raise SharedDataDirError(
            f"pace-maker: {SHARED_DATA_DIR_KEY} must not be the per-user "
            f"directory '{normalized}' (it holds code, config and secrets)"
        )
    return normalized


def _shared_dir_or_invalid() -> Optional[str]:
    """Like shared_data_dir() but never raises: a bad setting -> INVALID_SHARED_DIR."""
    try:
        return shared_data_dir()
    except SharedDataDirError:
        return INVALID_SHARED_DIR


def shared_mode_enabled() -> bool:
    """True when ``shared_data_dir`` is configured (even if malformed). Never raises."""
    return _shared_dir_or_invalid() is not None


def monitoring_data_dir() -> str:
    """Directory holding the monitoring DBs: shared dir if set, else per-user.

    Never raises (see INVALID_SHARED_DIR).
    """
    return _shared_dir_or_invalid() or per_user_dir()


def monitoring_db_path(filename: str) -> str:
    """Full path of a monitoring DB file (single choke point for DB paths)."""
    return os.path.join(monitoring_data_dir(), filename)


def per_user_secrets_db_path() -> str:
    """Per-user secrets DB used in shared mode (never in the shared dir)."""
    return os.path.join(per_user_dir(), SECRETS_DB_FILE)


def is_invalid_sentinel_path(db_path: str) -> bool:
    """True for a file directly inside INVALID_SHARED_DIR (never create it)."""
    return os.path.dirname(os.path.normpath(db_path)) == INVALID_SHARED_DIR


def is_shared_path(db_path: str) -> bool:
    """True when ``db_path`` is a file directly inside the active shared dir."""
    shared = _shared_dir_or_invalid()
    if shared is None:
        return False
    return os.path.dirname(os.path.normpath(db_path)) == shared


def validate_shared_data_dir() -> None:
    """Raise SharedDataDirError if shared mode is on and the dir is unusable.

    No-op in legacy mode.
    """
    shared = shared_data_dir()
    if shared is None:
        return
    if not os.path.exists(shared):
        raise SharedDataDirError(
            f"pace-maker: {SHARED_DATA_DIR_KEY} '{shared}' does not exist"
        )
    if not os.path.isdir(shared):
        raise SharedDataDirError(
            f"pace-maker: {SHARED_DATA_DIR_KEY} '{shared}' is not a directory"
        )
    if not os.access(shared, os.W_OK | os.X_OK):
        raise SharedDataDirError(
            f"pace-maker: {SHARED_DATA_DIR_KEY} '{shared}' is not writable by "
            f"{os_user()} (need group rw, setgid dir)"
        )


def shared_dir_problem() -> Optional[str]:
    """Error text for ``pace-maker status``, or None when fine / legacy.

    Covers an unusable directory and a planted symlink/special file where a
    shared DB (or its -wal/-shm/-journal) is expected.
    """
    try:
        validate_shared_data_dir()
        for filename in SHARED_DB_FILES:
            confirm_shared_files(monitoring_db_path(filename))
    except SharedDataDirError as exc:
        return str(exc)
    return None


_SIDE_SUFFIXES = ("-wal", "-shm", "-journal")
FileIdentity = Tuple[int, int]


def _lstat_regular(path: str) -> Optional["os.stat_result"]:
    """lstat ``path``: None if absent; SharedDataDirError unless a regular file.

    The shared dir is writable by other OS users, who could plant a symlink
    (or FIFO/device) where a DB or SQLite side file is expected. Anything that
    is not a plain regular file is refused, never followed.
    """
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise SharedDataDirError(
            f"pace-maker: shared database file '{path}' is not a regular file "
            "(symlink or special file refused)"
        )
    return st


def confirm_shared_files(
    db_path: str, expected: Optional[FileIdentity] = None
) -> Optional[FileIdentity]:
    """Refuse symlinks/special files for a shared DB and its -wal/-shm/-journal.

    Returns the main file's (dev, ino) when it exists. With ``expected`` it
    also fails if the main file was swapped since (post-connect re-check).
    No-op (returns None) outside the shared dir.
    """
    if not is_shared_path(db_path):
        return None
    ident: Optional[FileIdentity] = None
    for suffix in ("",) + _SIDE_SUFFIXES:
        st = _lstat_regular(db_path + suffix)
        if st is not None and suffix == "":
            ident = (st.st_dev, st.st_ino)
    if expected is not None and ident != expected:
        raise SharedDataDirError(
            f"pace-maker: shared database '{db_path}' was replaced while opening it"
        )
    return ident


def _create_new_shared_file(db_path: str) -> bool:
    """Create ``db_path`` 0660 exclusively. False if another process won the race.

    O_EXCL matters twice: with several OS users starting at once a plain
    O_CREAT would open the winner's file and then fail to fchmod a file we do
    not own, and O_EXCL refuses to create through a planted (dangling) symlink.
    The umask goes through the same lock/refcount as ``shared_umask``.
    """
    try:
        with _umask_scope():
            fd = os.open(db_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, SHARED_DB_MODE)
    except FileExistsError:
        return False
    try:
        os.fchmod(fd, SHARED_DB_MODE)
    finally:
        os.close(fd)
    return True


def prepare_db_file(db_path: str) -> Optional[FileIdentity]:
    """Make sure a shared DB file exists and is group read/write (0660).

    No-op for any path outside the shared dir (legacy behaviour untouched).
    SQLite creates ``-wal``/``-shm``/``-journal`` with the main DB's mode,
    so fixing the main file is sufficient. Never narrows a mode.

    Symlinks are never followed: the mode is only changed through an
    ``O_NOFOLLOW`` file descriptor after ``fstat`` confirms a regular file we
    own that is the very inode ``lstat`` saw. Returns that file's identity so
    the caller can re-check it after connecting.

    Raises:
        SharedDataDirError: shared dir unusable, a path is a symlink/special
            file, or the file is owned by another user and lacks group rw
            (cannot be repaired by us).
    """
    if not is_shared_path(db_path):
        return None
    validate_shared_data_dir()
    confirm_shared_files(db_path)  # refuse planted symlinks before creating
    if not os.path.lexists(db_path) and _create_new_shared_file(db_path):
        return confirm_shared_files(db_path)
    st = _lstat_regular(db_path)
    if st is None:
        raise SharedDataDirError(
            f"pace-maker: shared database '{db_path}' disappeared while opening it"
        )
    mode = stat.S_IMODE(st.st_mode)
    if mode & _GROUP_RW != _GROUP_RW:
        if st.st_uid != os.geteuid():
            # Not ours and not group rw: we could never repair it, so refuse
            # from the lstat result without opening someone else's file.
            raise _unrepairable(db_path)
        _widen_mode_nofollow(db_path, st, mode)
    return (st.st_dev, st.st_ino)


def _unrepairable(db_path: str) -> SharedDataDirError:
    return SharedDataDirError(
        f"pace-maker: shared database '{db_path}' is not group read/write "
        f"and {os_user()} does not own it"
    )


def _widen_mode_nofollow(db_path: str, st: "os.stat_result", mode: int) -> None:
    try:
        # O_NONBLOCK: a file swapped for a FIFO after our lstat must not hang
        # a hook (O_RDONLY on a FIFO blocks until a writer appears).
        fd = os.open(db_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise SharedDataDirError(
            f"pace-maker: cannot open shared database '{db_path}' safely: {exc}"
        ) from exc
    try:
        fst = os.fstat(fd)
        if not stat.S_ISREG(fst.st_mode) or (fst.st_dev, fst.st_ino) != (
            st.st_dev,
            st.st_ino,
        ):
            raise SharedDataDirError(
                f"pace-maker: shared database '{db_path}' was replaced while "
                "opening it"
            )
        if fst.st_uid != os.geteuid():
            raise _unrepairable(db_path)
        os.fchmod(fd, mode | _GROUP_RW)
    finally:
        os.close(fd)


# ── Group-friendly umask while a shared DB connection is OPENED ──────────────
# SQLite creates -wal/-shm lazily using the process umask and only afterwards
# fixes their mode. With the common umask 022 a second OS user can hit the
# file inside that window and fail with "attempt to write a readonly
# database" / "unable to open database file" (reproduced with two real users).
# The scope is deliberately narrow: only the connect + first read that creates
# the side files (database._open_connection), never the lifetime of the
# connection, so other threads and child processes see the umask 007 for
# milliseconds at most. The umask is process-wide, so entries are reference
# counted under a lock and the original value is restored by the last exit.
SHARED_UMASK = 0o007
_umask_lock = threading.Lock()
_umask_depth = 0
_umask_saved = 0


@contextmanager
def _umask_scope() -> Iterator[None]:
    """Hold umask 007 (refcounted under ``_umask_lock``); restore on last exit."""
    global _umask_depth, _umask_saved
    with _umask_lock:
        if _umask_depth == 0:
            _umask_saved = os.umask(SHARED_UMASK)
        _umask_depth += 1
    try:
        yield
    finally:
        with _umask_lock:
            _umask_depth -= 1
            if _umask_depth == 0:
                os.umask(_umask_saved)


@contextmanager
def shared_umask(db_path: str) -> Iterator[None]:
    """Hold umask 007 while opening a connection to a shared DB.

    No-op (the umask is not touched) for any path outside the shared dir.
    """
    if not is_shared_path(db_path):
        yield
        return
    with _umask_scope():
        yield
