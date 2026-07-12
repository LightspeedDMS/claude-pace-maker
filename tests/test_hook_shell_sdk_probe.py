"""
Tests for the SDK-aware find_python() interpreter selection in src/hooks/*.sh.

Regression coverage for issue #89: find_python() picked an interpreter by
existence only (`command -v`), never checking whether claude_agent_sdk
actually imports. When the first-found interpreter lacks the SDK (e.g.
system /bin/python3 = 3.9 while the SDK lives only in python3.11's
site-packages), every Anthropic-SDK-based verifier (haiku/sonnet/opus/fable)
silently fails inside the Python hook process with no user-visible warning,
degrading competitive review pipelines.

Fix: find_python() now probes each existence-only candidate with
`"$py" -c "import claude_agent_sdk"` and prefers the first candidate where
that import succeeds. If NO candidate has the SDK importable (e.g. a plain
non-SDK hook_model like codex), it falls back to the original
existence-only preference order — this is the regression case that must
not break.

Strategy: mirrors tests/test_hook_shell_enabled_guard.py — real subprocess
calls against the actual shell scripts with a controlled $HOME and fake
python3.11/python3.10/python3 executables on $PATH that respond
differently to `-c "import claude_agent_sdk"` vs. any other invocation.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

# snap-confined jq cannot access /tmp — create test homes under the real
# home directory so jq can read config files in its confined filesystem view.
_REAL_HOME = Path.home()

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_HOOKS_DIR = REPO_ROOT / "src" / "hooks"
SRC_HOOK_SCRIPTS = list(SRC_HOOKS_DIR.glob("*.sh"))


@pytest.fixture
def tmp_home(tmp_path_factory):
    """Temp directory under the real home so snap jq can read files in it."""
    base = _REAL_HOME / ".pytest-hook-sdk-probe-tests"
    base.mkdir(parents=True, exist_ok=True)
    d = tempfile.mkdtemp(dir=base)
    yield Path(d)
    import shutil

    shutil.rmtree(d, ignore_errors=True)


def _make_env(home: Path, fake_bin: Path) -> dict:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    return env


def _write_config(home: Path, config: dict) -> Path:
    pacemaker_dir = home / ".claude-pace-maker"
    pacemaker_dir.mkdir(parents=True, exist_ok=True)
    config_file = pacemaker_dir / "config.json"
    config_file.write_text(json.dumps(config))
    return config_file


_FAKE_PYTHON_TEMPLATE = """#!/bin/bash
# Fake python interpreter for issue #89 SDK-probe tests.
# Responds to `-c "import claude_agent_sdk"` per the HAS_SDK flag baked in
# at generation time; records the invoking interpreter name to
# SELECTED_LOG for any other invocation (the real hook run), then exits 0
# so the calling shell script (which uses `set -e`) does not abort.
if [ "$1" = "-c" ]; then
    case "$2" in
        *claude_agent_sdk*)
            exit {sdk_exit}
            ;;
        *)
            exit 1
            ;;
    esac
fi
echo "{name}" >> "{selected_log}"
exit 0
"""


def _write_fake_pythons(fake_bin: Path, selected_log: Path, sdk_capable: set):
    """Write python3.11/python3.10/python3 fakes; only names in sdk_capable import the SDK."""
    fake_bin.mkdir(parents=True, exist_ok=True)
    for name in ("python3.11", "python3.10", "python3"):
        fake_py = fake_bin / name
        fake_py.write_text(
            _FAKE_PYTHON_TEMPLATE.format(
                sdk_exit=0 if name in sdk_capable else 1,
                name=name,
                selected_log=selected_log,
            )
        )
        fake_py.chmod(0o755)


def _run_src_hook(script: Path, home: Path, fake_bin: Path):
    env = _make_env(home, fake_bin)
    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


class TestFindPythonPrefersSdkCapableInterpreter:
    """find_python() must select an SDK-importable interpreter over merely
    the first one found on PATH, when such an interpreter exists."""

    @pytest.fixture
    def home_enabled(self, tmp_home):
        _write_config(tmp_home, {"enabled": True})
        return tmp_home

    @pytest.mark.parametrize("script", SRC_HOOK_SCRIPTS, ids=lambda s: s.name)
    def test_skips_first_found_without_sdk_for_later_candidate_with_sdk(
        self, home_enabled, tmp_home, script
    ):
        """python3.11 exists but lacks the SDK; python3.10 has it. Must
        select python3.10, NOT python3.11 (the existence-only first hit)."""
        fake_bin = tmp_home / f"fake_bin_{script.stem}"
        selected_log = tmp_home / f"selected_{script.stem}.log"
        _write_fake_pythons(fake_bin, selected_log, sdk_capable={"python3.10"})

        result = _run_src_hook(script, home_enabled, fake_bin)

        assert selected_log.exists(), (
            f"{script.name}: hook never invoked a Python interpreter. "
            f"stderr={result.stderr[:300]}"
        )
        selected = selected_log.read_text().strip()
        assert selected == "python3.10", (
            f"{script.name}: expected SDK-capable python3.10 to be selected "
            f"over existence-only-first python3.11, got '{selected}'. "
            f"stderr={result.stderr[:300]}"
        )

    @pytest.mark.parametrize("script", SRC_HOOK_SCRIPTS, ids=lambda s: s.name)
    def test_selects_only_sdk_capable_candidate_even_if_last_in_order(
        self, home_enabled, tmp_home, script
    ):
        """Only python3 (last candidate in existence-only order) has the SDK.
        Must still select python3, not python3.11 or python3.10."""
        fake_bin = tmp_home / f"fake_bin2_{script.stem}"
        selected_log = tmp_home / f"selected2_{script.stem}.log"
        _write_fake_pythons(fake_bin, selected_log, sdk_capable={"python3"})

        result = _run_src_hook(script, home_enabled, fake_bin)

        assert selected_log.exists(), (
            f"{script.name}: hook never invoked a Python interpreter. "
            f"stderr={result.stderr[:300]}"
        )
        selected = selected_log.read_text().strip()
        assert selected == "python3", (
            f"{script.name}: expected SDK-capable python3 to be selected "
            f"even though it is last in existence-only order, got '{selected}'. "
            f"stderr={result.stderr[:300]}"
        )


class TestFindPythonFallsBackWhenNoSdkCapableCandidate:
    """Regression: when NO candidate has the SDK importable (e.g. a plain
    non-SDK hook_model like codex), the original existence-only preference
    order (python3.11 > python3.10 > python3) must be preserved unchanged."""

    @pytest.fixture
    def home_enabled(self, tmp_home):
        _write_config(tmp_home, {"enabled": True})
        return tmp_home

    @pytest.mark.parametrize("script", SRC_HOOK_SCRIPTS, ids=lambda s: s.name)
    def test_falls_back_to_existence_only_first_found(
        self, home_enabled, tmp_home, script
    ):
        """No fake python imports the SDK. Must select python3.11 — the
        first candidate in the existence-only preference order — exactly
        as before the fix."""
        fake_bin = tmp_home / f"fake_bin3_{script.stem}"
        selected_log = tmp_home / f"selected3_{script.stem}.log"
        _write_fake_pythons(fake_bin, selected_log, sdk_capable=set())

        result = _run_src_hook(script, home_enabled, fake_bin)

        assert selected_log.exists(), (
            f"{script.name}: hook never invoked a Python interpreter. "
            f"stderr={result.stderr[:300]}"
        )
        selected = selected_log.read_text().strip()
        assert selected == "python3.11", (
            f"{script.name}: expected existence-only fallback to select "
            f"python3.11 (first found) when no candidate has the SDK, "
            f"got '{selected}'. stderr={result.stderr[:300]}"
        )
