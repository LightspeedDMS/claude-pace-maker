#!/usr/bin/env python3
"""Setup script for Claude Pace Maker."""

import os
import subprocess
from setuptools import setup, find_packages
from setuptools.command.install import install


class PostInstallCommand(install):
    """Post-installation command to run install.sh."""

    def run(self):
        """Run standard installation and then execute install.sh."""
        install.run(self)
        
        # Get the installation directory
        install_dir = os.path.dirname(os.path.abspath(__file__))
        install_script = os.path.join(install_dir, "install.sh")
        
        # Run the installer
        if os.path.exists(install_script):
            print("\nRunning Claude Pace Maker installer...")
            subprocess.run([install_script], check=False)


setup(
    name="claude-pace-maker",
    version="1.0.0",
    description="Intelligent credit consumption throttling for Claude Code",
    author="Lightspeed DMS",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    python_requires=">=3.9",
    install_requires=[
        "requests>=2.31.0",
    ],
    entry_points={
        "console_scripts": [
            "claude-pace-maker=pacemaker.hook:main",
        ],
    },
    cmdclass={
        "install": PostInstallCommand,
    },
    include_package_data=True,
)
