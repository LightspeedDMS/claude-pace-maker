#!/usr/bin/env python3
"""CLI entry point for claude-pace-maker."""

import sys
import subprocess
from pathlib import Path


def main():
    """Run the installer."""
    # Find the install.sh script
    # When installed via pipx, it should be in the package directory
    package_dir = Path(__file__).parent.parent.parent
    install_script = package_dir / "install.sh"

    if not install_script.exists():
        print("Error: install.sh not found")
        print(f"Expected location: {install_script}")
        print("\nPlease run the installer manually:")
        print("  git clone https://github.com/LightspeedDMS/claude-pace-maker")
        print("  cd claude-pace-maker")
        print("  ./install.sh")
        sys.exit(1)

    # Pass arguments to installer
    args = sys.argv[1:]
    result = subprocess.run([str(install_script)] + args)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
