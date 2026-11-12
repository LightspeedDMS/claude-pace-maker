"""Story #187 integration: two REAL OS users share one data dir (no mocks).

Runs ``fixtures/multiuser_187/run.sh`` as root inside a throwaway rootless
podman container: two users in a common group, a setgid 2770 shared dir, the
real pacemaker code (read-only bind mount of ``src``), concurrent traffic with
different umasks (077 / 022). It would have caught the umask-022 "attempt to
write a readonly database" race that single-user tests cannot see.

Opt-in because it needs podman and a LOCAL image that has ``python3``,
``runuser``, ``useradd`` and ``groupadd`` (nothing is pulled):

    PACEMAKER_MULTIUSER_IMAGE=<local-image> ./scripts/run_tests.sh --quick

Skips cleanly when podman or the image is missing.
"""

import os
import pwd
import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "multiuser_187"
SRC = Path(__file__).resolve().parent.parent / "src"
IMAGE = os.environ.get("PACEMAKER_MULTIUSER_IMAGE", "")
ITERATIONS = 100
DEADLINE_SECONDS = 100  # run_tests.sh gives a file 120 s


def _podman_env() -> dict:
    """The autouse fixture fakes HOME; rootless podman storage lives under the
    REAL home, so point it back there for the container run."""
    env = dict(os.environ)
    env["HOME"] = pwd.getpwuid(os.getuid()).pw_dir
    return env


def _image_present() -> bool:
    if not IMAGE or not shutil.which("podman"):
        return False
    probe = subprocess.run(
        ["podman", "image", "exists", IMAGE],
        capture_output=True,
        timeout=30,
        env=_podman_env(),
    )
    return probe.returncode == 0


pytestmark = pytest.mark.skipif(
    not _image_present(),
    reason="set PACEMAKER_MULTIUSER_IMAGE to a local podman image (python3, "
    "runuser, useradd) and have podman available",
)


@pytest.mark.timeout(DEADLINE_SECONDS + 10)
def test_two_real_os_users_write_one_shared_db_without_errors():
    out = subprocess.run(
        [
            "podman", "run", "--rm", "--user", "root", "--network", "none",
            "--security-opt", "label=disable",
            "-v", f"{SRC}:/src:ro",
            "-v", f"{FIXTURES}:/w:ro",
            IMAGE, "bash", "/w/run.sh", str(ITERATIONS),
        ],
        capture_output=True,
        text=True,
        timeout=DEADLINE_SECONDS,
        env=_podman_env(),
    )  # fmt: skip
    report = out.stdout + out.stderr
    assert out.returncode == 0, report
    assert "files_not_0660=0" in report
    assert "logged_errors_or_warnings=0" in report
    assert "rows_ok" in report
