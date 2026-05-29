"""
Regression test: pace-maker CLI must not corrupt the ~/.local/bin/pace-maker
symlink when invoked from another plugin's hook context.

Background:
- The developer plugin's SessionStart hook sources ensure-pace-maker-install.sh
  and runs the pace-maker CLI as a smoke test, AND setup-langfuse-telemetry.sh
  invokes `pace-maker langfuse provision-url ...`. Both invocations inherit
  CLAUDE_PLUGIN_ROOT pointing at the developer plugin (NOT at pace-maker).
- bootstrap_light writes ~/.local/bin/pace-maker → $PLUGIN_ROOT/scripts/pace-maker.
- Before the fix, _bootstrap_resolve_plugin_root blindly used CLAUDE_PLUGIN_ROOT
  when PLUGIN_ROOT wasn't pinned by the caller, so the smoke test rewrote
  ~/.local/bin/pace-maker → <developer plugin>/scripts/pace-maker — a broken
  symlink (the developer plugin has no scripts/pace-maker).

Two-layer defense being asserted:
1. The pace-maker CLI script pins PLUGIN_ROOT to its own resolved script dir
   before sourcing bootstrap-plugin.sh.
2. _bootstrap_resolve_plugin_root only honors CLAUDE_PLUGIN_ROOT when that
   directory actually contains scripts/bootstrap-plugin.sh.
"""

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PACE_MAKER_CLI = REPO_ROOT / "scripts" / "pace-maker"
BOOTSTRAP_SH = REPO_ROOT / "scripts" / "bootstrap-plugin.sh"


def _make_foreign_plugin(tmp_path: Path) -> Path:
    """Build a minimal directory that looks like another plugin's root:
    has a scripts/ subdir, BUT NO scripts/bootstrap-plugin.sh. This is
    what CLAUDE_PLUGIN_ROOT points at when a sibling plugin's hook runs."""
    foreign = tmp_path / "foreign_plugin" / "1.0.0"
    (foreign / "scripts").mkdir(parents=True)
    (foreign / "scripts" / "ensure-pace-maker-install.sh").write_text(
        "# stub — emulates a sibling plugin script that may invoke pace-maker\n"
    )
    return foreign


def _seed_pace_maker_install(home: Path) -> Path:
    """Run bootstrap_light against the real plugin under a fresh HOME so
    ~/.local/bin/pace-maker exists and points at the real CLI script."""
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PLUGIN_ROOT"] = str(REPO_ROOT)
    result = subprocess.run(
        ["bash", str(BOOTSTRAP_SH), "--light"],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"seed bootstrap_light must succeed.\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    symlink = home / ".local" / "bin" / "pace-maker"
    assert symlink.is_symlink(), f"seed did not create symlink at {symlink}"
    target = os.readlink(str(symlink))
    assert target == str(
        REPO_ROOT / "scripts" / "pace-maker"
    ), f"seed symlink target should point at the real plugin; got {target}"
    return symlink


class TestPaceMakerCliPreservesSymlinkWithForeignClaudePluginRoot:
    """When the CLI is invoked with CLAUDE_PLUGIN_ROOT set to a different
    plugin's directory (which is what happens during the developer plugin's
    SessionStart wiring), the existing ~/.local/bin/pace-maker symlink must
    remain pointing at the real claude-pace-maker plugin — not get
    overwritten to point inside the foreign plugin."""

    def test_status_does_not_rewrite_symlink_to_foreign_plugin(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        symlink = _seed_pace_maker_install(home)
        original_target = os.readlink(str(symlink))

        foreign = _make_foreign_plugin(tmp_path)

        env = os.environ.copy()
        env["HOME"] = str(home)
        env["CLAUDE_PLUGIN_ROOT"] = str(foreign)
        # PATH must expose ~/.local/bin so the user-facing CLI lookup works.
        env["PATH"] = f"{home}/.local/bin:{env.get('PATH', '/usr/bin:/bin')}"

        # Invoke the CLI via the symlink (the same way the smoke test does).
        # The status command must not be required to fully succeed — what we
        # care about is that the symlink target survives. We run with --help
        # equivalent by going through the resolver and bootstrap_light only;
        # `status` may exit nonzero in an empty HOME with no venv, but the
        # symlink rewrite (the regression) happens BEFORE that.
        result = subprocess.run(
            [str(symlink), "status"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        # We intentionally don't assert on returncode — the regression is
        # about side effects on the symlink target.

        post_target = os.readlink(str(symlink))
        assert post_target == original_target, (
            f"pace-maker CLI corrupted ~/.local/bin/pace-maker.\n"
            f"  before: {original_target}\n"
            f"  after:  {post_target}\n"
            f"  CLAUDE_PLUGIN_ROOT was: {foreign}\n"
            f"  cli stdout: {result.stdout}\n"
            f"  cli stderr: {result.stderr}"
        )
        # Sanity: the symlink target must still resolve to an existing file.
        assert Path(
            post_target
        ).is_file(), f"post-run symlink target does not exist: {post_target}"


class TestBootstrapResolvePluginRootIgnoresForeignClaudePluginRoot:
    """Unit-level check on _bootstrap_resolve_plugin_root itself: when
    CLAUDE_PLUGIN_ROOT points at a directory without
    scripts/bootstrap-plugin.sh, the function must fall back to the
    script-location derivation — NOT silently accept the wrong path."""

    def test_foreign_claude_plugin_root_is_rejected(self, tmp_path):
        foreign = _make_foreign_plugin(tmp_path)

        # Source bootstrap-plugin.sh, clear PLUGIN_ROOT, point
        # CLAUDE_PLUGIN_ROOT at the foreign directory, then call the
        # resolver and print the result.
        script = (
            f"source {BOOTSTRAP_SH}\n"
            "unset PLUGIN_ROOT\n"
            "_bootstrap_resolve_plugin_root\n"
            'echo "PLUGIN_ROOT=$PLUGIN_ROOT"\n'
        )
        result = subprocess.run(
            ["bash", "-c", script],
            env={
                **os.environ,
                "CLAUDE_PLUGIN_ROOT": str(foreign),
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        line = next(
            (ln for ln in result.stdout.splitlines() if ln.startswith("PLUGIN_ROOT=")),
            "",
        )
        resolved = line.split("=", 1)[1] if "=" in line else ""
        assert resolved != str(foreign), (
            f"_bootstrap_resolve_plugin_root must not accept a "
            f"CLAUDE_PLUGIN_ROOT that lacks scripts/bootstrap-plugin.sh. "
            f"Got: {resolved}"
        )
        # When falling through to the script-location derivation, the
        # resolved root must contain the bootstrap script itself.
        assert (Path(resolved) / "scripts" / "bootstrap-plugin.sh").is_file(), (
            f"fallback resolution must point at a real claude-pace-maker "
            f"plugin root (containing scripts/bootstrap-plugin.sh). "
            f"Got: {resolved}"
        )

    def test_valid_claude_plugin_root_is_still_accepted(self, tmp_path):
        """Negative regression: a legitimate CLAUDE_PLUGIN_ROOT pointing
        at a real pace-maker plugin (e.g. the actual hook.sh entry path)
        must still be honored — we did NOT just disable env-based
        override entirely."""
        script = (
            f"source {BOOTSTRAP_SH}\n"
            "unset PLUGIN_ROOT\n"
            "_bootstrap_resolve_plugin_root\n"
            'echo "PLUGIN_ROOT=$PLUGIN_ROOT"\n'
        )
        result = subprocess.run(
            ["bash", "-c", script],
            env={
                **os.environ,
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        line = next(
            (ln for ln in result.stdout.splitlines() if ln.startswith("PLUGIN_ROOT=")),
            "",
        )
        resolved = line.split("=", 1)[1] if "=" in line else ""
        assert resolved == str(REPO_ROOT), (
            f"_bootstrap_resolve_plugin_root must honor a valid "
            f"CLAUDE_PLUGIN_ROOT (one that contains scripts/bootstrap-plugin.sh). "
            f"Expected {REPO_ROOT}, got {resolved}"
        )
