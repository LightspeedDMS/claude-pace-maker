"""Story #187 review B1: a malformed ``shared_data_dir`` must never crash
imports (hooks, MCP server, CLI). Real subprocesses, real files, no mocks.

An invalid shape behaves like a missing directory: every shared DB open
fails (logged), ``pace-maker status`` shows the INVALID section, and the
hook still exits normally so the gates keep running.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parent.parent / "src")
BAD_VALUES = ["relative/dir", 5, "", "   ", ["x"], True]
HOOK_INPUT = json.dumps(
    {
        "session_id": "11111111-1111-4111-8111-111111111111",
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/x"},
        "cwd": "/tmp",
    }
)


def _home(tmp_path, setting):
    home = tmp_path / "home"
    (home / ".claude-pace-maker").mkdir(parents=True)
    cfg = {"enabled": True}
    if setting is not None:
        cfg["shared_data_dir"] = setting
    (home / ".claude-pace-maker" / "config.json").write_text(json.dumps(cfg))
    return home


def _run(home, args, stdin=""):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PACEMAKER_")}
    env.update(HOME=str(home), PYTHONPATH=SRC)
    return subprocess.run(
        [sys.executable] + args,
        input=stdin,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize("bad", BAD_VALUES, ids=repr)
def test_import_constants_does_not_raise(tmp_path, bad):
    out = _run(_home(tmp_path, bad), ["-c", "import pacemaker.constants"])
    assert out.returncode == 0, out.stderr


@pytest.mark.parametrize("bad", BAD_VALUES, ids=repr)
def test_hook_still_exits_like_legacy_and_does_not_traceback(tmp_path, bad):
    baseline = _run(
        _home(tmp_path / "ok", None),
        ["-m", "pacemaker.hook", "pre_tool_use"],
        HOOK_INPUT,
    )
    out = _run(
        _home(tmp_path / "bad", bad),
        ["-m", "pacemaker.hook", "pre_tool_use"],
        HOOK_INPUT,
    )
    assert "SharedDataDirError" not in out.stderr, out.stderr
    assert out.returncode == baseline.returncode, out.stderr


@pytest.mark.parametrize("bad", BAD_VALUES, ids=repr)
def test_status_shows_invalid_section(tmp_path, bad):
    home = _home(tmp_path, bad)
    cfg = home / ".claude-pace-maker" / "config.json"
    code = (
        "from pacemaker import user_commands, constants;"
        f"r = user_commands.execute_command('status', {str(cfg)!r}, "
        "db_path=constants.DEFAULT_DB_PATH);"
        "print(r['message'])"
    )
    out = _run(home, ["-c", code])
    assert out.returncode == 0, out.stderr
    assert "Shared Data Dir: INVALID" in out.stdout
    assert "shared_data_dir" in out.stdout


@pytest.mark.parametrize("bad", BAD_VALUES, ids=repr)
def test_db_open_fails_loudly_and_never_writes_anywhere(tmp_path, bad):
    home = _home(tmp_path, bad)
    code = (
        "import os;"
        "from pacemaker import constants, database as db;"
        "print(db.initialize_database(constants.DEFAULT_DB_PATH));"
        "print(db.record_activity_event(constants.DEFAULT_DB_PATH,'IV','green','s'));"
        "print(sorted(os.listdir('.')))"
    )
    work = tmp_path / "cwd"
    work.mkdir()
    env_cwd = subprocess.run(
        [sys.executable, "-c", code],
        cwd=work,
        env={
            **{k: v for k, v in os.environ.items() if not k.startswith("PACEMAKER_")},
            "HOME": str(home),
            "PYTHONPATH": SRC,
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    lines = env_cwd.stdout.strip().splitlines()
    assert lines[:2] == ["False", "False"], env_cwd.stderr
    assert lines[2] == "[]"  # nothing created in the cwd (no relative-path writes)
    assert not (home / ".claude-pace-maker" / "usage.db").exists()  # no fallback
    log = "".join(p.read_text() for p in (home / ".claude-pace-maker").glob("*.log"))
    assert "shared_data_dir" in log  # logged


@pytest.mark.parametrize("bad", BAD_VALUES, ids=repr)
def test_secrets_keep_masking_with_invalid_setting(tmp_path, bad):
    home = _home(tmp_path, bad)
    code = (
        "from pacemaker import constants;"
        "from pacemaker.secrets import database as s;"
        "from pacemaker.secrets.sanitizer import sanitize_trace;"
        "p = constants.DEFAULT_DB_PATH;"
        "s.create_secret(p, 'text', 'invalid-setting-secret-1');"
        "print(sanitize_trace({'m': 'invalid-setting-secret-1'}, p)[1])"
    )
    out = _run(home, ["-c", code])
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "1"
    assert (home / ".claude-pace-maker" / "secrets.db").exists()
