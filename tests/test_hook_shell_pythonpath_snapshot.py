"""
Tests for issue #146: installed hook scripts must import pacemaker from the
INSTALLED SNAPSHOT (the copy `./install.sh` places at
`~/.claude/hooks/pacemaker`) instead of from the Dev `$SOURCE_DIR/src` tree,
by default.

Before this fix, every `src/hooks/*.sh` template's "development installation"
branch did `export PYTHONPATH="$SOURCE_DIR/src:$PYTHONPATH"` unconditionally
whenever `install_source` did not point at a pipx venv. That means every hook
process imported `pacemaker` directly from the Dev working tree, so a
half-finished/uncommitted edit there (missing symbol, syntax error) crashed
every hook on the machine until the edit was completed — see the 26
`NameError: DEFAULT_TAIL_WINDOW_BYTES` crashes cited in the issue.

Fix: the same branch now points PYTHONPATH at the directory the hook script
itself lives in (i.e. the installed hooks dir, where install.sh's
`install_hook_modules()` already copies `pacemaker/`), unless the explicit
opt-in env var `PACEMAKER_DEV_LIVE_SRC=1` is set — which restores the old
live-source behavior for development only, never by default.

Strategy: mirrors tests/test_hook_shell_sdk_probe.py — real subprocess runs
of the actual `src/hooks/*.sh` scripts (these are copied byte-for-byte by
install.sh's `cp`, so testing the template IS testing the deployed script)
with a controlled $HOME. Each script is copied into a simulated installed
hooks directory ($HOME/.claude/hooks) exactly as install.sh would, an
`install_source` marker is written pointing at a DIFFERENT directory (a fake
Dev source tree) so SOURCE_DIR != the installed hooks dir, and a fake python
interpreter captures the PYTHONPATH the hook script builds before it would
have run `python -m pacemaker.hook <type>`.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import pytest

# snap-confined jq cannot access /tmp — create test homes under the real
# home directory so jq can read config files in its confined filesystem view
# (same convention as tests/test_hook_shell_sdk_probe.py).
_REAL_HOME = Path.home()

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_HOOKS_DIR = REPO_ROOT / "src" / "hooks"
SRC_HOOK_SCRIPTS = list(SRC_HOOKS_DIR.glob("*.sh"))

_SUBPROCESS_TIMEOUT_SECONDS = 10


@pytest.fixture
def tmp_home(tmp_path_factory):
    """Temp directory under the real home so snap jq can read files in it."""
    base = _REAL_HOME / ".pytest-hook-pythonpath-snapshot-tests"
    base.mkdir(parents=True, exist_ok=True)
    d = tempfile.mkdtemp(dir=base)
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


def _write_config(home: Path, config: dict) -> Path:
    pacemaker_dir = home / ".claude-pace-maker"
    pacemaker_dir.mkdir(parents=True, exist_ok=True)
    config_file = pacemaker_dir / "config.json"
    config_file.write_text(json.dumps(config))
    return config_file


def _write_install_source(home: Path, source_dir: Path) -> None:
    pacemaker_dir = home / ".claude-pace-maker"
    pacemaker_dir.mkdir(parents=True, exist_ok=True)
    (pacemaker_dir / "install_source").write_text(str(source_dir))


def _install_hook_script(home: Path, script: Path) -> Path:
    """Simulate install.sh's `cp` of a hook template into the installed hooks dir."""
    hooks_dir = home / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dest = hooks_dir / script.name
    shutil.copy(script, dest)
    dest.chmod(0o755)
    return dest


_CAPTURE_PYTHON = """#!/bin/bash
# Fake python: fails the SDK probe (forces existence-only fallback to
# python3.11, matching TestFindPythonFallsBackWhenNoSdkCapableCandidate) and
# captures PYTHONPATH for the real hook invocation.
if [ "$1" = "-c" ]; then
    exit 1
fi
echo "$PYTHONPATH" > "{captured_log}"
exit 0
"""


def _write_fake_python(fake_bin: Path, captured_log: Path) -> None:
    fake_bin.mkdir(parents=True, exist_ok=True)
    for name in ("python3.11", "python3.10", "python3"):
        fake_py = fake_bin / name
        fake_py.write_text(_CAPTURE_PYTHON.format(captured_log=captured_log))
        fake_py.chmod(0o755)


def _run_installed_hook(script_path: Path, home: Path, fake_bin: Path, extra_env=None):
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    # Never let the outer test-runner's own env leak the opt-in flag, or any
    # pre-existing PYTHONPATH, into the assertions below.
    env.pop("PACEMAKER_DEV_LIVE_SRC", None)
    env.pop("PYTHONPATH", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )


class TestInstalledHookImportsFromSnapshotByDefault:
    """Default (no opt-in): PYTHONPATH must point at the installed hooks
    directory (where install.sh copies pacemaker/), never at
    $SOURCE_DIR/src."""

    @pytest.mark.parametrize("script", SRC_HOOK_SCRIPTS, ids=lambda s: s.name)
    def test_pythonpath_points_at_installed_snapshot_not_dev_src(
        self, tmp_home, script
    ):
        _write_config(tmp_home, {"enabled": True})
        fake_source_dir = tmp_home / "fake_dev_source_tree"
        (fake_source_dir / "src").mkdir(parents=True)
        _write_install_source(tmp_home, fake_source_dir)

        installed_script = _install_hook_script(tmp_home, script)
        installed_hooks_dir = installed_script.parent
        # The missing-snapshot guard (review follow-up on issue #146) exits
        # early, before ever setting PYTHONPATH, unless the installed
        # pacemaker/ package is present -- exactly as it is in a real
        # install.sh deploy. Stub it so this test still exercises the
        # PYTHONPATH-setting logic it targets.
        _write_fake_pacemaker_package(
            installed_hooks_dir, tmp_home / f"unused_marker_{script.stem}.txt", "stub"
        )

        fake_bin = tmp_home / f"fake_bin_{script.stem}"
        captured_log = tmp_home / f"pythonpath_{script.stem}.log"
        _write_fake_python(fake_bin, captured_log)

        result = _run_installed_hook(installed_script, tmp_home, fake_bin)

        assert captured_log.exists(), (
            f"{script.name}: hook never invoked the real Python interpreter. "
            f"stderr={result.stderr[:400]}"
        )
        captured_pythonpath = captured_log.read_text().strip()

        assert str(installed_hooks_dir) in captured_pythonpath.split(os.pathsep), (
            f"{script.name}: PYTHONPATH must include the installed hooks "
            f"directory {installed_hooks_dir} so `import pacemaker` resolves "
            f"to the copied snapshot. Got PYTHONPATH={captured_pythonpath!r}"
        )
        assert str(fake_source_dir / "src") not in captured_pythonpath, (
            f"{script.name}: PYTHONPATH must NOT include $SOURCE_DIR/src by "
            f"default (issue #146) — got PYTHONPATH={captured_pythonpath!r}"
        )


class TestInstalledHookLiveDevOptIn:
    """PACEMAKER_DEV_LIVE_SRC=1 is an explicit opt-in that restores the old
    live-source behavior for development, and must NOT be the default
    (covered by TestInstalledHookImportsFromSnapshotByDefault above)."""

    @pytest.mark.parametrize("script", SRC_HOOK_SCRIPTS, ids=lambda s: s.name)
    def test_opt_in_env_var_restores_dev_src_pythonpath(self, tmp_home, script):
        _write_config(tmp_home, {"enabled": True})
        fake_source_dir = tmp_home / "fake_dev_source_tree"
        (fake_source_dir / "src").mkdir(parents=True)
        _write_install_source(tmp_home, fake_source_dir)

        installed_script = _install_hook_script(tmp_home, script)

        fake_bin = tmp_home / f"fake_bin_optin_{script.stem}"
        captured_log = tmp_home / f"pythonpath_optin_{script.stem}.log"
        _write_fake_python(fake_bin, captured_log)

        result = _run_installed_hook(
            installed_script,
            tmp_home,
            fake_bin,
            extra_env={"PACEMAKER_DEV_LIVE_SRC": "1"},
        )

        assert captured_log.exists(), (
            f"{script.name}: hook never invoked the real Python interpreter. "
            f"stderr={result.stderr[:400]}"
        )
        captured_pythonpath = captured_log.read_text().strip()
        assert str(fake_source_dir / "src") in captured_pythonpath.split(os.pathsep), (
            f"{script.name}: PACEMAKER_DEV_LIVE_SRC=1 must restore "
            f"$SOURCE_DIR/src on PYTHONPATH for live development, got "
            f"PYTHONPATH={captured_pythonpath!r}"
        )


class TestPipxInstallUnaffected:
    """The pipx branch (install_source containing 'pipx') must be
    unaffected by this fix — it already resolves to a proper venv python
    with pacemaker installed as a real package, no PYTHONPATH manipulation
    either before or after."""

    @pytest.mark.parametrize("script", SRC_HOOK_SCRIPTS, ids=lambda s: s.name)
    def test_pipx_marker_does_not_set_pythonpath(self, tmp_home, script):
        _write_config(tmp_home, {"enabled": True})
        pipx_share_dir = (
            tmp_home
            / "pipx"
            / "venvs"
            / "claude-pace-maker"
            / "share"
            / "claude-pace-maker"
        )
        pipx_share_dir.mkdir(parents=True)
        venv_python = pipx_share_dir.parent.parent / "bin" / "python3"
        venv_python.parent.mkdir(parents=True, exist_ok=True)

        captured_log = tmp_home / f"pipx_pythonpath_{script.stem}.log"
        venv_python.write_text(_CAPTURE_PYTHON.format(captured_log=captured_log))
        venv_python.chmod(0o755)

        _write_install_source(tmp_home, pipx_share_dir)
        installed_script = _install_hook_script(tmp_home, script)

        # No fake_bin needed — the pipx branch invokes venv_python directly
        # by absolute path, never via find_python()'s PATH lookup.
        result = _run_installed_hook(
            installed_script, tmp_home, tmp_home / "empty_bin_unused"
        )

        assert captured_log.exists(), (
            f"{script.name}: pipx venv python was never invoked. "
            f"stderr={result.stderr[:400]}"
        )
        captured_pythonpath = captured_log.read_text().strip()
        assert captured_pythonpath == "", (
            f"{script.name}: pipx branch must not set PYTHONPATH, got "
            f"{captured_pythonpath!r}"
        )


# ---------------------------------------------------------------------------
# Code-review follow-up (round 2) on issue #146:
#
# 1. `python -m` prepends the current working directory to sys.path AHEAD of
#    PYTHONPATH. The reviewer ran an installed hook script from cwd =
#    /home/jsbattig/Dev/claude-pace-maker/src (which itself contains a real
#    pacemaker/ package) and it imported the Dev tree regardless of
#    PYTHONPATH. Fix: PYTHONSAFEPATH=1 (Python 3.11+) plus fixing the
#    trailing-colon-means-cwd bug in the PYTHONPATH assignments.
# 2. If $HOOK_SCRIPT_DIR/pacemaker/__init__.py is missing (broken/partial
#    deploy), Python would otherwise silently fall through to whatever
#    `pacemaker` a leftover `pip install -e` (editable .pth in
#    site-packages) resolves to -- i.e. right back to the Dev tree, silently.
#    Fix: fail-open loudly -- log to hook_debug.log and exit 0, matching the
#    existing enabled-guard's exit style, before ever invoking python -m.
# ---------------------------------------------------------------------------

_FAKE_PACEMAKER_HOOK_TEMPLATE = '''"""Synthetic pacemaker.hook for issue #146 round-2 tests -- NOT the real
package. Writes a marker to disk identifying which copy ran, then exits 0
immediately. No stdin/stdout dependency, so it is safe to invoke exactly as
the real hook scripts do via `python -m pacemaker.hook <type>`.
"""
import sys
from pathlib import Path

Path({marker_path!r}).write_text({marker_value!r})
sys.exit(0)
'''


def _write_fake_pacemaker_package(
    package_parent: Path, marker_path: Path, marker_value: str
) -> None:
    """Create package_parent/pacemaker/{__init__.py,hook.py} -- a minimal
    importable package whose `hook.py`, when run as `__main__`, records
    which copy executed and exits cleanly."""
    if not isinstance(package_parent, Path) or not isinstance(marker_path, Path):
        raise TypeError("package_parent and marker_path must be pathlib.Path")
    if not package_parent.is_dir():
        raise ValueError(
            f"package_parent must already exist as a directory: {package_parent}"
        )
    if not marker_path.parent.is_dir():
        raise ValueError(
            f"marker_path's parent must already exist as a directory: "
            f"{marker_path.parent}"
        )
    if not isinstance(marker_value, str):
        raise TypeError("marker_value must be a string")
    if not marker_value:
        raise ValueError("marker_value must be a non-empty string")
    pkg_dir = package_parent / "pacemaker"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "hook.py").write_text(
        _FAKE_PACEMAKER_HOOK_TEMPLATE.format(
            marker_path=str(marker_path), marker_value=marker_value
        )
    )


def _copy_real_pacemaker_package(dest_parent: Path) -> None:
    """Copy the ACTUAL production src/pacemaker package tree (this repo's
    real code, not a synthetic stub) into dest_parent/pacemaker, so the
    real-interpreter test proves resolution against the real package."""
    if not isinstance(dest_parent, Path):
        raise TypeError("dest_parent must be a pathlib.Path")
    if not dest_parent.is_dir():
        raise ValueError(
            f"dest_parent must already exist as a directory: {dest_parent}"
        )
    real_pacemaker_src = REPO_ROOT / "src" / "pacemaker"
    if not real_pacemaker_src.is_dir():
        raise ValueError(
            f"expected real production package at {real_pacemaker_src}, not found"
        )
    shutil.copytree(
        real_pacemaker_src,
        dest_parent / "pacemaker",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def _patch_final_invocation_to_import_probe(script_path: Path) -> None:
    """Replace ONLY the final `$PYTHON_CMD -m pacemaker.hook ...` invocation
    line with an import probe that prints pacemaker.__file__, leaving every
    other line -- the real PYTHONPATH/PYTHONSAFEPATH-building logic under
    test -- byte-for-byte unchanged. Mirrors the manual scratch-HOME
    verification technique used for issue #146's live check."""
    if not isinstance(script_path, Path):
        raise TypeError("script_path must be a pathlib.Path")
    if not script_path.is_file():
        raise ValueError(f"script_path must be an existing file: {script_path}")
    lines = script_path.read_text().splitlines(keepends=True)
    out = []
    replaced = False
    for line in lines:
        if line.startswith("$PYTHON_CMD -m pacemaker.hook") and not replaced:
            out.append(
                "$PYTHON_CMD -c "
                "\"import pacemaker; print('PACEMAKER_FILE=' + pacemaker.__file__)\"\n"
            )
            replaced = True
        else:
            out.append(line)
    if not replaced:
        raise AssertionError(f"{script_path}: invocation line not found to patch")
    script_path.write_text("".join(out))


def _run_with_real_interpreter(
    script_path: Path, home: Path, cwd: Optional[Path] = None
):
    """Run an installed hook script with the REAL system PATH (no fake
    python stubbing) so find_python() selects the actual interpreter it
    would in production. Only HOME is overridden."""
    if not isinstance(script_path, Path):
        raise TypeError("script_path must be a pathlib.Path")
    if not script_path.is_file():
        raise ValueError(f"script_path must be an existing file: {script_path}")
    if not isinstance(home, Path):
        raise TypeError("home must be a pathlib.Path")
    if not home.is_dir():
        raise ValueError(f"home must already exist as a directory: {home}")
    if cwd is not None:
        if not isinstance(cwd, Path):
            raise TypeError("cwd must be a pathlib.Path or None")
        if not cwd.is_dir():
            raise ValueError(f"cwd must already exist as a directory: {cwd}")
    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("PACEMAKER_DEV_LIVE_SRC", None)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONSAFEPATH", None)
    return subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd is not None else None,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS * 3,
    )


class TestCwdDecoyPackageNotShadowingInstalledSnapshot:
    """`python -m` prepends the current working directory to sys.path ahead
    of PYTHONPATH. If a hook is ever invoked from a cwd that happens to
    contain its own pacemaker/ package -- notably this repo's own src/
    directory, which is exactly what the review that raised this found --
    that decoy must NOT shadow the real installed snapshot. Uses the REAL
    interpreter the script selects (no fake python stubbing) since the bug
    is in how the real Python resolves sys.path, not in interpreter
    selection."""

    @pytest.mark.parametrize("script", SRC_HOOK_SCRIPTS, ids=lambda s: s.name)
    def test_decoy_pacemaker_in_cwd_is_not_imported(self, tmp_home, script):
        _write_config(tmp_home, {"enabled": True})
        fake_source_dir = tmp_home / "fake_dev_source_tree"
        (fake_source_dir / "src").mkdir(parents=True)
        _write_install_source(tmp_home, fake_source_dir)

        installed_script = _install_hook_script(tmp_home, script)
        marker_path = tmp_home / f"which_ran_{script.stem}.txt"

        _write_fake_pacemaker_package(installed_script.parent, marker_path, "installed")

        decoy_cwd = tmp_home / f"decoy_cwd_{script.stem}"
        decoy_cwd.mkdir(parents=True, exist_ok=True)
        _write_fake_pacemaker_package(decoy_cwd, marker_path, "decoy")

        result = _run_with_real_interpreter(installed_script, tmp_home, cwd=decoy_cwd)

        assert marker_path.exists(), (
            f"{script.name}: neither the installed nor the decoy "
            f"pacemaker.hook ran. returncode={result.returncode} "
            f"stderr={result.stderr[:400]}"
        )
        assert marker_path.read_text() == "installed", (
            f"{script.name}: cwd's decoy pacemaker/ package was imported "
            f"instead of the installed snapshot (PYTHONSAFEPATH missing or "
            f"ineffective). Marker says: {marker_path.read_text()!r}"
        )


class TestMissingSnapshotGuardFailsOpenLoudly:
    """When the installed pacemaker/ package is missing (broken/partial
    deploy, or a hook script placed without ever running install.sh's
    install_hook_modules()), the hook must NOT silently fall through to
    whatever `pacemaker` happens to resolve elsewhere (e.g. a leftover
    editable install pointing at the Dev tree). It must log loudly and fail
    open (exit 0), matching the existing enabled-guard's exit style, before
    ever reaching the real `python -m pacemaker.hook` invocation."""

    @pytest.mark.parametrize("script", SRC_HOOK_SCRIPTS, ids=lambda s: s.name)
    def test_missing_pacemaker_package_logs_and_exits_zero_without_invoking_hook(
        self, tmp_home, script
    ):
        _write_config(tmp_home, {"enabled": True})
        fake_source_dir = tmp_home / "fake_dev_source_tree"
        (fake_source_dir / "src").mkdir(parents=True)
        _write_install_source(tmp_home, fake_source_dir)

        installed_script = _install_hook_script(tmp_home, script)
        # Deliberately do NOT create installed_script.parent / "pacemaker" --
        # simulates a broken/partial deploy.

        fake_bin = tmp_home / f"fake_bin_missing_{script.stem}"
        captured_log = tmp_home / f"pythonpath_missing_{script.stem}.log"
        _write_fake_python(fake_bin, captured_log)

        result = _run_installed_hook(installed_script, tmp_home, fake_bin)

        assert result.returncode == 0, (
            f"{script.name}: must fail OPEN (exit 0) when the installed "
            f"pacemaker/ package is missing, got {result.returncode}. "
            f"stderr={result.stderr[:400]}"
        )
        assert not captured_log.exists(), (
            f"{script.name}: must NOT reach `python -m pacemaker.hook` when "
            f"the installed snapshot is missing -- PYTHONPATH capture file "
            f"exists anyway, meaning the real invocation line ran."
        )
        debug_log = tmp_home / ".claude-pace-maker" / "hook_debug.log"
        assert debug_log.exists(), (
            f"{script.name}: must log a loud warning to hook_debug.log when "
            f"the installed pacemaker/ package is missing."
        )
        log_text = debug_log.read_text()
        assert "pacemaker" in log_text and "install.sh" in log_text, (
            f"{script.name}: hook_debug.log message must mention the "
            f"missing pacemaker package and instruct running ./install.sh. "
            f"Got: {log_text!r}"
        )


class TestRealInterpreterResolvesInstalledSnapshot:
    """Copies the ACTUAL production src/pacemaker package (not a stub) into
    the installed hooks dir and runs the real, unmodified env-building logic
    with the REAL interpreter the script selects (no fake python stubbing),
    proving pacemaker.__file__ resolves under the installed snapshot."""

    @pytest.mark.parametrize("script", SRC_HOOK_SCRIPTS, ids=lambda s: s.name)
    def test_real_python_imports_real_package_from_installed_dir(
        self, tmp_home, script
    ):
        _write_config(tmp_home, {"enabled": True})
        fake_source_dir = tmp_home / "fake_dev_source_tree"
        (fake_source_dir / "src").mkdir(parents=True)
        _write_install_source(tmp_home, fake_source_dir)

        installed_script = _install_hook_script(tmp_home, script)
        _copy_real_pacemaker_package(installed_script.parent)
        _patch_final_invocation_to_import_probe(installed_script)

        result = _run_with_real_interpreter(installed_script, tmp_home)

        assert "PACEMAKER_FILE=" in result.stdout, (
            f"{script.name}: import probe never ran or pacemaker import "
            f"failed. stdout={result.stdout!r} stderr={result.stderr[:400]}"
        )
        resolved = result.stdout.strip().split("PACEMAKER_FILE=", 1)[1]
        assert resolved.startswith(str(installed_script.parent)), (
            f"{script.name}: real interpreter resolved pacemaker.__file__ "
            f"to {resolved!r}, expected it under the installed snapshot "
            f"dir {installed_script.parent}"
        )
