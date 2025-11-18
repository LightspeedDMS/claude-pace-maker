#!/usr/bin/env python3
"""Installer wrapper for pipx installation."""

import sys
import subprocess
from pathlib import Path


def find_install_script():
    """Find install.sh in the installed package."""
    # Try common locations
    locations = [
        # Data files location (pipx/pip install)
        Path(sys.prefix) / "share" / "claude-pace-maker" / "install.sh",
        # Development mode
        Path(__file__).parent.parent.parent / "install.sh",
    ]

    for loc in locations:
        if loc.exists():
            return loc

    return None


def main():
    """Run the installer."""
    install_script = find_install_script()

    if not install_script:
        print("Error: install.sh not found in package")
        print("\nSearched locations:")
        print(f"  - {Path(sys.prefix) / 'share' / 'claude-pace-maker' / 'install.sh'}")
        print(f"  - {Path(__file__).parent.parent.parent / 'install.sh'}")
        print("\nAlternative installation:")
        print("  git clone https://github.com/LightspeedDMS/claude-pace-maker")
        print("  cd claude-pace-maker")
        print("  ./install.sh")
        sys.exit(1)

    print(f"Running installer from: {install_script}")

    # Pass arguments to installer
    args = sys.argv[1:]
    result = subprocess.run([str(install_script)] + args)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
