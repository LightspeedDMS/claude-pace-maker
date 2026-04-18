"""CLI helper for memory localization commands.

Dispatches `pace-maker localize-memory` and
`pace-maker memory-localization <on|off|status|unlink>` to the appropriate
functions in memory_localization/core.py. Keeps user_commands.py thin.
"""

import json
import os
import sys

# Config path — overridable via PACEMAKER_CONFIG_PATH env var for testing.
DEFAULT_CONFIG_PATH = os.getenv(
    "PACEMAKER_CONFIG_PATH",
    os.path.expanduser("~/.claude-pace-maker/config.json"),
)


def _load_main_config() -> dict:
    """Load the main pace-maker config.json.

    Returns empty dict on missing file or parse error, logging to stderr
    so callers can still proceed with defaults.
    """
    try:
        with open(DEFAULT_CONFIG_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        print(
            "[pace-maker] Warning: config.json not found, using defaults",
            file=sys.stderr,
        )
        return {}
    except json.JSONDecodeError as e:
        print(f"[pace-maker] Warning: config.json parse error: {e}", file=sys.stderr)
        return {}
    except OSError as e:
        print(f"[pace-maker] Warning: cannot read config.json: {e}", file=sys.stderr)
        return {}


def _save_main_config(config: dict) -> None:
    """Save the main pace-maker config.json atomically."""
    dirname = os.path.dirname(DEFAULT_CONFIG_PATH)
    os.makedirs(dirname, exist_ok=True)
    temp_path = DEFAULT_CONFIG_PATH + ".tmp"
    try:
        with open(temp_path, "w") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        os.replace(temp_path, DEFAULT_CONFIG_PATH)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def _set_memory_localization_enabled(enabled: bool) -> int:
    """Toggle memory_localization_enabled in config.json.

    Returns exit code (0 success, 1 error).
    """
    try:
        config = _load_main_config()
        config["memory_localization_enabled"] = enabled
        _save_main_config(config)
        state = "enabled" if enabled else "disabled"
        print(f"memory localization: {state}")
        return 0
    except Exception as e:
        state = "enabling" if enabled else "disabling"
        print(f"Error {state} memory localization: {e}", file=sys.stderr)
        return 1


def localize_memory_cmd(cwd: str) -> int:
    """Handle `pace-maker localize-memory`.

    Seeds .claude-memory/ in the current repo and links it to the central
    Claude memory folder. Returns exit code (0 success, non-zero error).
    """
    if not cwd or not isinstance(cwd, str):
        print("Error: invalid working directory", file=sys.stderr)
        return 1
    try:
        from .memory_localization.core import seed_and_link

        seed_and_link(cwd)
        return 0
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error during localize-memory: {e}", file=sys.stderr)
        return 1


def memory_localization_cmd(subcommand: str, cwd: str) -> int:
    """Handle `pace-maker memory-localization <on|off|status|unlink>`.

    Returns exit code (0 success, non-zero error).
    """
    if not cwd or not isinstance(cwd, str):
        print("Error: invalid working directory", file=sys.stderr)
        return 1

    if subcommand == "on":
        return _set_memory_localization_enabled(True)

    elif subcommand == "off":
        return _set_memory_localization_enabled(False)

    elif subcommand == "status":
        config = _load_main_config()
        enabled = config.get("memory_localization_enabled", True)
        state = "enabled" if enabled else "disabled"
        print(f"memory localization: {state}")
        return 0

    elif subcommand == "unlink":
        try:
            from .memory_localization.core import unlink_and_restore

            unlink_and_restore(cwd)
            return 0
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        except Exception as e:
            print(f"Unexpected error during unlink: {e}", file=sys.stderr)
            return 1

    else:
        print(
            f"Unknown subcommand: {subcommand!r}\n"
            "Usage: pace-maker memory-localization <on|off|status|unlink>",
            file=sys.stderr,
        )
        return 2
