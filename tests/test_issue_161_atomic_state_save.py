#!/usr/bin/env python3
"""
Bug #161 (part 1): the machine-wide ~/.claude-pace-maker/state.json must be
written atomically.

Root cause (live evidence: three "Failed to load state ... JSONDecodeError:
... char 0" warnings in one run): hook.save_state() did
`open(path, "w")` + `json.dump()`. `open(..., "w")` truncates the file to 0
bytes first, and json.dump then streams the content in several writes, so any
concurrent hook process that loads state in that window reads an EMPTY or
HALF-WRITTEN file, falls back to defaults and then saves those defaults back,
wiping everything.

Fix contract: save_state writes a temp file in the same directory and
os.replace()s it over the target, so a reader sees either the complete old
file or the complete new one, never anything in between.

These tests use real files in tmp dirs and a real second process as the
writer; nothing is mocked.
"""

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

from pacemaker.hook import load_state, save_state

_SRC = str(Path(__file__).resolve().parent.parent / "src")

# Big enough that a non-atomic write cannot finish in a single write() call.
_BLOB_SIZE = 600_000
_WRITER_ITERATIONS = 120
_WRITER_TIMEOUT_SECONDS = 60
_MIN_PROBES = 5
_ERROR_HEAD_BYTES = 40

# Writer process: rewrites the state file many times. `payload` is a dict
# literal merged into every save so each test can carry its own marker fields.
_WRITER_SCRIPT = """
import sys
sys.path.insert(0, {src!r})
from pacemaker.hook import save_state
path = {path!r}
blob = "x" * {blob_size}
for i in range({iterations}):
    save_state(dict({payload}, n=i, blob=blob), path)
"""

# A probe inspects the state file once and returns None when it looked fine,
# or a short description of what was wrong.
Probe = Callable[[], Optional[str]]


def _run_against_writer(
    state_file: Path, payload: dict, probe: Probe
) -> Tuple[int, int, list]:
    """Start a real writer process on `state_file`, call `probe` in a bounded
    loop until the writer exits, and always clean the writer up.

    Returns (writer_returncode, probe_count, problems)."""
    script = _WRITER_SCRIPT.format(
        src=_SRC,
        path=str(state_file),
        blob_size=_BLOB_SIZE,
        iterations=_WRITER_ITERATIONS,
        payload=repr(payload),
    )
    # conftest fakes HOME, so the child cannot rediscover user-site packages
    # (e.g. `requests`) on its own: hand it this interpreter's own sys.path.
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    writer = subprocess.Popen([sys.executable, "-c", script], env=env)
    probes = 0
    problems: list = []
    deadline = time.monotonic() + _WRITER_TIMEOUT_SECONDS
    try:
        while writer.poll() is None and time.monotonic() < deadline:
            probes += 1
            problem = probe()
            if problem:
                problems.append(problem)
    finally:
        if writer.poll() is None:
            writer.kill()
        writer.wait()
    return writer.returncode, probes, problems


class TestAtomicSaveStateUnderConcurrency:
    def test_concurrent_reader_never_sees_empty_or_partial_state(self, tmp_path):
        """Every raw read of state.json while another process rewrites it must
        be complete, valid JSON."""
        state_file = tmp_path / "state.json"
        save_state({"n": -1, "blob": "x" * _BLOB_SIZE}, str(state_file))

        def probe() -> Optional[str]:
            raw = state_file.read_bytes()
            try:
                data = json.loads(raw)
            except ValueError:
                return f"{len(raw)} bytes, head={raw[:_ERROR_HEAD_BYTES]!r}"
            if not isinstance(data, dict) or "n" not in data:
                return f"unexpected content: {raw[:_ERROR_HEAD_BYTES]!r}"
            return None

        rc, probes, problems = _run_against_writer(state_file, {}, probe)

        assert rc == 0, "writer process failed"
        assert probes > _MIN_PROBES, f"reader loop barely ran ({probes} reads)"
        assert not problems, f"{len(problems)}/{probes} reads bad: {problems[:3]}"

    def test_load_state_never_falls_back_to_defaults_during_saves(self, tmp_path):
        """load_state (used by every hook) must never see a torn file and
        silently return defaults."""
        state_file = tmp_path / "state.json"
        marker = {"subagent_counter": 7, "tool_execution_count": 99}
        save_state(dict(marker, blob="y" * _BLOB_SIZE), str(state_file))

        def probe() -> Optional[str]:
            state = load_state(str(state_file))
            if state.get("subagent_counter") != marker["subagent_counter"]:
                return f"defaults returned: {sorted(state)}"
            return None

        rc, probes, problems = _run_against_writer(state_file, marker, probe)

        assert rc == 0, "writer process failed"
        assert probes > _MIN_PROBES
        assert not problems, f"{len(problems)}/{probes} loads defaulted"


class TestSaveStateFileSemantics:
    @staticmethod
    def _work_dir(tmp_path: Path) -> Path:
        work = tmp_path / "work"  # conftest also puts fake_home in tmp_path
        work.mkdir()
        return work

    def test_existing_mode_kept_and_no_temp_files_after_repeated_saves(self, tmp_path):
        work = self._work_dir(tmp_path)
        state_file = work / "state.json"
        state_file.write_text("{}")
        os.chmod(state_file, 0o640)

        save_state({"a": 1}, str(state_file))
        save_state({"a": 2}, str(state_file))

        assert stat.S_IMODE(state_file.stat().st_mode) == 0o640
        assert json.loads(state_file.read_text()) == {"a": 2}
        assert [p.name for p in work.iterdir()] == ["state.json"]

    def test_failed_serialization_keeps_previous_file_and_leaves_no_temp(
        self, tmp_path
    ):
        work = self._work_dir(tmp_path)
        state_file = work / "state.json"
        save_state({"keep": "me"}, str(state_file))

        save_state({"bad": object()}, str(state_file))  # not JSON serializable

        assert json.loads(state_file.read_text()) == {"keep": "me"}
        assert [p.name for p in work.iterdir()] == ["state.json"]

    def test_creates_missing_parent_directory(self, tmp_path):
        state_file = tmp_path / "nested" / "dir" / "state.json"

        save_state({"a": 1}, str(state_file))

        assert json.loads(state_file.read_text()) == {"a": 1}
