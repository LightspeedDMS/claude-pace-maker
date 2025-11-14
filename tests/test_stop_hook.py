#!/usr/bin/env python3
"""
Tests for stop hook momentum preservation.

Testing strategy:
1. Unit tests for hook logic (enabled detection, prompt generation)
2. Integration tests for hook execution
3. E2E tests with zero mocking (real config files, real hook execution)
"""

import unittest
import tempfile
import os
import json
import subprocess
import shutil
from pathlib import Path


class TestStopHookUnit(unittest.TestCase):
    """Unit tests for stop hook functionality."""

    def setUp(self):
        """Set up temp environment for each test."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.json')
        self.hook_path = os.path.join(self.temp_dir, 'stop.sh')

    def tearDown(self):
        """Clean up temp environment."""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_hook_script_exists(self):
        """Test 1: Stop hook script file must exist."""
        # This will fail until we create the hook
        hook_location = os.path.expanduser('~/.claude/hooks/stop.sh')
        self.assertTrue(
            os.path.exists(hook_location),
            f"Stop hook must exist at {hook_location}"
        )

    def test_hook_script_is_executable(self):
        """Test 2: Stop hook must be executable."""
        hook_location = os.path.expanduser('~/.claude/hooks/stop.sh')
        self.assertTrue(
            os.access(hook_location, os.X_OK),
            "Stop hook must be executable"
        )

    def test_hook_exits_zero_when_disabled(self):
        """Test 3: Hook must pass through (exit 0) when pace maker is disabled."""
        # Create disabled config
        config = {"enabled": False}
        with open(self.config_path, 'w') as f:
            json.dump(config, f)

        # Copy hook to temp location
        hook_location = os.path.expanduser('~/.claude/hooks/stop.sh')
        if os.path.exists(hook_location):
            shutil.copy(hook_location, self.hook_path)
            os.chmod(self.hook_path, 0o755)

            # Set HOME to use our config
            env = os.environ.copy()
            env['HOME'] = self.temp_dir

            # Create .claude-pace-maker directory
            os.makedirs(os.path.join(self.temp_dir, '.claude-pace-maker'))
            shutil.copy(self.config_path, os.path.join(self.temp_dir, '.claude-pace-maker', 'config.json'))

            # Run hook
            result = subprocess.run(
                [self.hook_path],
                env=env,
                capture_output=True,
                text=True
            )

            # Should exit 0 (success/pass-through)
            self.assertEqual(result.returncode, 0, "Hook must exit 0 when disabled")
            # Should produce no output when disabled
            self.assertEqual(result.stdout.strip(), "", "Hook must produce no output when disabled")

    def test_hook_exits_zero_when_config_missing(self):
        """Test 4: Hook must gracefully handle missing config (treat as disabled)."""
        hook_location = os.path.expanduser('~/.claude/hooks/stop.sh')
        if os.path.exists(hook_location):
            shutil.copy(hook_location, self.hook_path)
            os.chmod(self.hook_path, 0o755)

            # Set HOME to temp dir with no config
            env = os.environ.copy()
            env['HOME'] = self.temp_dir

            # Run hook
            result = subprocess.run(
                [self.hook_path],
                env=env,
                capture_output=True,
                text=True
            )

            # Should exit 0 (graceful degradation)
            self.assertEqual(result.returncode, 0, "Hook must exit 0 when config missing")

    def test_hook_injects_prompt_when_enabled(self):
        """Test 5: Hook must inject continuation prompt when pace maker is enabled."""
        # Create enabled config
        config = {"enabled": True}
        with open(self.config_path, 'w') as f:
            json.dump(config, f)

        hook_location = os.path.expanduser('~/.claude/hooks/stop.sh')
        if os.path.exists(hook_location):
            shutil.copy(hook_location, self.hook_path)
            os.chmod(self.hook_path, 0o755)

            # Set HOME to use our config
            env = os.environ.copy()
            env['HOME'] = self.temp_dir

            # Create .claude-pace-maker directory
            os.makedirs(os.path.join(self.temp_dir, '.claude-pace-maker'))
            shutil.copy(self.config_path, os.path.join(self.temp_dir, '.claude-pace-maker', 'config.json'))

            # Run hook
            result = subprocess.run(
                [self.hook_path],
                env=env,
                capture_output=True,
                text=True
            )

            # Should exit 0
            self.assertEqual(result.returncode, 0, "Hook must exit 0 when enabled")

            # Should output continuation prompt
            output = result.stdout
            self.assertIn("PACE MAKER ACTIVE", output, "Output must mention pace maker is active")
            self.assertIn("acceptance criteria", output.lower(), "Output must reference acceptance criteria")
            self.assertIn("complete", output.lower(), "Output must emphasize completion")
            self.assertIn("DO NOT stop", output, "Output must warn against stopping mid-task")

    def test_hook_performance_under_one_second(self):
        """Test 6: Hook execution must complete within 1 second (minimal performance impact)."""
        import time

        # Create enabled config
        config = {"enabled": True}
        with open(self.config_path, 'w') as f:
            json.dump(config, f)

        hook_location = os.path.expanduser('~/.claude/hooks/stop.sh')
        if os.path.exists(hook_location):
            shutil.copy(hook_location, self.hook_path)
            os.chmod(self.hook_path, 0o755)

            # Set HOME to use our config
            env = os.environ.copy()
            env['HOME'] = self.temp_dir

            # Create .claude-pace-maker directory
            os.makedirs(os.path.join(self.temp_dir, '.claude-pace-maker'))
            shutil.copy(self.config_path, os.path.join(self.temp_dir, '.claude-pace-maker', 'config.json'))

            # Measure execution time
            start = time.time()
            result = subprocess.run(
                [self.hook_path],
                env=env,
                capture_output=True,
                text=True
            )
            elapsed = time.time() - start

            # Should complete in under 1 second
            self.assertLess(elapsed, 1.0, "Hook must complete within 1 second")
            self.assertEqual(result.returncode, 0)


class TestStopHookE2E(unittest.TestCase):
    """End-to-end tests with zero mocking."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_dir = os.path.join(self.temp_dir, '.claude-pace-maker')
        self.hooks_dir = os.path.join(self.temp_dir, '.claude', 'hooks')
        os.makedirs(self.config_dir)
        os.makedirs(self.hooks_dir)

    def tearDown(self):
        """Clean up."""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_e2e_real_hook_execution_when_enabled(self):
        """E2E Test 1: Real hook execution with enabled configuration."""
        # Copy actual hook to temp hooks directory
        real_hook = os.path.expanduser('~/.claude/hooks/stop.sh')
        temp_hook = os.path.join(self.hooks_dir, 'stop.sh')

        if os.path.exists(real_hook):
            shutil.copy(real_hook, temp_hook)
            os.chmod(temp_hook, 0o755)

            # Create enabled config
            config = {"enabled": True}
            config_file = os.path.join(self.config_dir, 'config.json')
            with open(config_file, 'w') as f:
                json.dump(config, f)

            # Execute hook with temp HOME
            env = os.environ.copy()
            env['HOME'] = self.temp_dir

            result = subprocess.run(
                [temp_hook],
                env=env,
                capture_output=True,
                text=True,
                timeout=5  # Ensure hook doesn't hang
            )

            # Verify success
            self.assertEqual(result.returncode, 0, "Hook must execute successfully")

            # Verify continuation messaging
            output = result.stdout
            self.assertGreater(len(output), 0, "Hook must produce output when enabled")
            self.assertIn("PACE MAKER", output)
            self.assertIn("acceptance criteria", output.lower())

    def test_e2e_real_hook_execution_when_disabled(self):
        """E2E Test 2: Real hook execution with disabled configuration."""
        # Copy actual hook to temp hooks directory
        real_hook = os.path.expanduser('~/.claude/hooks/stop.sh')
        temp_hook = os.path.join(self.hooks_dir, 'stop.sh')

        if os.path.exists(real_hook):
            shutil.copy(real_hook, temp_hook)
            os.chmod(temp_hook, 0o755)

            # Create disabled config
            config = {"enabled": False}
            config_file = os.path.join(self.config_dir, 'config.json')
            with open(config_file, 'w') as f:
                json.dump(config, f)

            # Execute hook with temp HOME
            env = os.environ.copy()
            env['HOME'] = self.temp_dir

            result = subprocess.run(
                [temp_hook],
                env=env,
                capture_output=True,
                text=True,
                timeout=5
            )

            # Verify success and silence
            self.assertEqual(result.returncode, 0, "Hook must execute successfully")
            self.assertEqual(result.stdout.strip(), "", "Hook must produce no output when disabled")

    def test_e2e_jq_dependency_availability(self):
        """E2E Test 3: Verify jq is available for JSON processing."""
        result = subprocess.run(
            ['which', 'jq'],
            capture_output=True,
            text=True
        )

        self.assertEqual(
            result.returncode, 0,
            "jq must be installed for hook to function (dependency validation)"
        )


if __name__ == '__main__':
    unittest.main()
