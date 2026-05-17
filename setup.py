#!/usr/bin/env python3
"""Setup script for Claude Pace Maker."""

from setuptools import setup, find_packages


setup(
    name="claude-pace-maker",
    version="2.24.0",
    description="Intelligent credit consumption throttling for Claude Code",
    author="Lightspeed DMS",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    python_requires=">=3.9",
    install_requires=[
        "requests>=2.31.0",
        "pyyaml>=6.0",
        "claude-agent-sdk>=0.1.0",
    ],
    entry_points={
        "console_scripts": [
            "claude-pace-maker=pacemaker.installer:main",
            "pace-maker=pacemaker.user_commands:main",
        ],
    },
    # Include install.sh and hook scripts as data files
    data_files=[
        ("share/claude-pace-maker", ["install.sh"]),
        ("share/claude-pace-maker/hooks", [
            "src/hooks/post-tool-use.sh",
            "src/hooks/pre-tool-use.sh",
            "src/hooks/stop.sh",
            "src/hooks/user-prompt-submit.sh",
            "src/hooks/session-start.sh",
            "src/hooks/subagent-start.sh",
            "src/hooks/subagent-stop.sh",
        ]),
    ],
    include_package_data=True,
)
