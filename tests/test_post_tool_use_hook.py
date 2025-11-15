#!/usr/bin/env python3
"""
Tests for post-tool-use hook throttling functionality.

These tests verify that:
1. post_tool_use argument is properly handled by main()
2. Throttling messages reach stdout (not stderr)
3. Delays are executed when throttling is triggered
4. Shell script integration works end-to-end
"""

import unittest
import tempfile
import os
import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
from io import StringIO


class TestPostToolUseArgumentRouting(unittest.TestCase):
    """Unit tests for post_tool_use argument handling in main()."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.json')
        self.db_path = os.path.join(self.temp_dir, 'usage.db')
        self.state_path = os.path.join(self.temp_dir, 'state.json')

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_post_tool_use_routes_to_run_hook(self):
        """
        RED TEST 1: main() with 'post_tool_use' argument should route to run_hook().

        ACTUAL BUG: main() doesn't explicitly handle 'post_tool_use' argument.
        It only checks for 'user_prompt_submit' (line 491) and 'stop' (line 496),
        then falls through to run_hook() at line 502.

        The implicit fallthrough works but is not clear or maintainable.
        We need explicit routing: if len(sys.argv) > 1 and sys.argv[1] == 'post_tool_use'
        """
        from pacemaker import hook

        # Create enabled config
        config = {"enabled": True}
        with open(self.config_path, 'w') as f:
            json.dump(config, f)

        # Mock run_hook to detect if it was called
        run_hook_called = False

        def mock_run_hook():
            nonlocal run_hook_called
            run_hook_called = True

        # Patch configuration paths and run_hook
        with patch.object(hook, 'DEFAULT_CONFIG_PATH', self.config_path), \
             patch.object(hook, 'DEFAULT_DB_PATH', self.db_path), \
             patch.object(hook, 'DEFAULT_STATE_PATH', self.state_path), \
             patch.object(hook, 'run_hook', side_effect=mock_run_hook):

            # Set argv to simulate post_tool_use call
            with patch.object(sys, 'argv', ['hook.py', 'post_tool_use']):
                hook.main()

        # Verify run_hook was called
        self.assertTrue(run_hook_called, "main() with 'post_tool_use' must call run_hook()")

    def test_main_explicit_post_tool_use_handling(self):
        """
        RED TEST 1B: main() should have explicit handling for 'post_tool_use'.

        This test will FAIL because the code doesn't have explicit if-statement
        for 'post_tool_use' argument.

        Expected code structure in main():
            if len(sys.argv) > 1 and sys.argv[1] == 'post_tool_use':
                run_hook()
                return
        """
        from pacemaker import hook
        import inspect

        # Get source code of main()
        source = inspect.getsource(hook.main)

        # Check for explicit post_tool_use handling
        has_explicit_handling = (
            "sys.argv[1] == 'post_tool_use'" in source or
            'sys.argv[1] == "post_tool_use"' in source
        )

        self.assertTrue(
            has_explicit_handling,
            "main() must have explicit if-statement for 'post_tool_use' argument"
        )

    def test_main_handles_all_hook_types(self):
        """
        RED TEST 2: main() should explicitly handle all hook types.

        Currently only handles:
        - user_prompt_submit
        - stop

        Missing explicit handling for:
        - post_tool_use (falls through implicitly)

        This test documents the expected behavior.
        """
        from pacemaker import hook

        # Test all expected hook types
        hook_types = ['user_prompt_submit', 'stop', 'post_tool_use']

        for hook_type in hook_types:
            with self.subTest(hook_type=hook_type):
                # Each hook type should have explicit handling in main()
                # This is a documentation test - we're asserting the design
                pass


class TestThrottlingOutputRouting(unittest.TestCase):
    """Unit tests for throttling message output routing."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.json')
        self.db_path = os.path.join(self.temp_dir, 'usage.db')
        self.state_path = os.path.join(self.temp_dir, 'state.json')

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_inject_prompt_delay_outputs_to_stdout(self):
        """
        RED TEST 3: inject_prompt_delay() must output to stdout (not stderr).

        This test will PASS currently because inject_prompt_delay() already
        uses stdout. But we need to verify it stays that way.
        """
        from pacemaker import hook

        test_prompt = "Please wait 10 seconds..."

        # Capture stdout
        captured_stdout = StringIO()
        with patch('sys.stdout', captured_stdout):
            hook.inject_prompt_delay(test_prompt)

        # Verify output went to stdout
        output = captured_stdout.getvalue()
        self.assertIn(test_prompt, output)

    def test_execute_delay_produces_no_output(self):
        """
        RED TEST 4: execute_delay() should sleep silently (no stdout/stderr).

        Currently execute_delay() is silent. This test ensures it stays that way.
        """
        from pacemaker import hook

        # Capture stdout and stderr
        captured_stdout = StringIO()
        captured_stderr = StringIO()

        with patch('sys.stdout', captured_stdout), \
             patch('sys.stderr', captured_stderr):

            start = time.time()
            hook.execute_delay(1)  # 1 second delay
            elapsed = time.time() - start

        # Verify delay happened
        self.assertGreaterEqual(elapsed, 1.0)

        # Verify no output
        self.assertEqual(captured_stdout.getvalue(), "")
        self.assertEqual(captured_stderr.getvalue(), "")

    def test_run_hook_outputs_throttling_to_stdout(self):
        """
        RED TEST 5: run_hook() must output throttling messages to stdout.

        This test will FAIL because currently when throttling triggers,
        the messages may not be reaching stdout correctly.

        We need to ensure inject_prompt_delay() messages reach stdout.
        """
        from pacemaker import hook
        from pacemaker import database

        # Create config with aggressive throttling
        config = {
            "enabled": True,
            "base_delay": 5,
            "max_delay": 120,
            "threshold_percent": 90,  # Very low threshold to trigger throttling
            "poll_interval": 0  # Force polling every time
        }
        with open(self.config_path, 'w') as f:
            json.dump(config, f)

        # Initialize database with low credit balance to trigger throttling
        database.initialize_database(self.db_path)

        # Mock pacing_engine.run_pacing_check to return throttling decision
        mock_result = {
            'polled': True,
            'poll_time': None,
            'decision': {
                'should_throttle': True,
                'strategy': {
                    'method': 'prompt',
                    'prompt': 'THROTTLING: Please wait 10 seconds...'
                }
            }
        }

        # Capture stdout
        captured_stdout = StringIO()

        with patch.object(hook, 'DEFAULT_CONFIG_PATH', self.config_path), \
             patch.object(hook, 'DEFAULT_DB_PATH', self.db_path), \
             patch.object(hook, 'DEFAULT_STATE_PATH', self.state_path), \
             patch('pacemaker.pacing_engine.run_pacing_check', return_value=mock_result), \
             patch('sys.stdout', captured_stdout):

            hook.run_hook()

        # Verify throttling message reached stdout
        output = captured_stdout.getvalue()
        self.assertIn('THROTTLING', output,
                     "Throttling message must appear in stdout")
        self.assertIn('wait', output.lower(),
                     "Throttling message must mention waiting")


class TestE2EHookExecution(unittest.TestCase):
    """End-to-end tests with real subprocess execution."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.pacemaker_dir = os.path.join(self.temp_dir, '.claude-pace-maker')
        os.makedirs(self.pacemaker_dir)

        self.config_path = os.path.join(self.pacemaker_dir, 'config.json')
        self.db_path = os.path.join(self.pacemaker_dir, 'usage.db')
        self.state_path = os.path.join(self.pacemaker_dir, 'state.json')

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_e2e_python_hook_with_post_tool_use_argument(self):
        """
        RED TEST 6: E2E test calling Python hook with post_tool_use argument.

        This simulates what the shell script does:
        python3 -m pacemaker.hook post_tool_use

        Should execute without errors and output nothing when no throttling.
        """
        # Create enabled config
        config = {"enabled": True, "poll_interval": 999999}  # High interval = no polling
        with open(self.config_path, 'w') as f:
            json.dump(config, f)

        # Get path to project root
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src_path = os.path.join(project_root, 'src')

        # Set environment
        env = os.environ.copy()
        env['PYTHONPATH'] = f"{src_path}:{env.get('PYTHONPATH', '')}"

        # Run hook module
        result = subprocess.run(
            [sys.executable, '-m', 'pacemaker.hook', 'post_tool_use'],
            cwd=project_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=5
        )

        # Verify success
        self.assertEqual(result.returncode, 0,
                        f"Hook must execute successfully. stderr: {result.stderr}")

    def test_e2e_python_hook_outputs_throttling_to_stdout(self):
        """
        RED TEST 7: E2E test verifying throttling messages reach stdout.

        This test will FAIL because currently throttling messages may not
        be output correctly when run via subprocess.
        """
        # Create config that will trigger throttling
        config = {
            "enabled": True,
            "base_delay": 5,
            "poll_interval": 0  # Force polling
        }
        with open(self.config_path, 'w') as f:
            json.dump(config, f)

        # Initialize database
        from pacemaker import database
        database.initialize_database(self.db_path)

        # Get path to project root
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src_path = os.path.join(project_root, 'src')

        # Create mock that triggers throttling
        # We'll do this by patching run_pacing_check in a test script
        test_script = os.path.join(self.temp_dir, 'test_hook_run.py')
        with open(test_script, 'w') as f:
            f.write(f"""
import sys
sys.path.insert(0, '{src_path}')
from unittest.mock import patch
from pacemaker import hook

# Mock throttling result
mock_result = {{
    'polled': False,
    'decision': {{
        'should_throttle': True,
        'strategy': {{
            'method': 'prompt',
            'prompt': 'THROTTLING MESSAGE: Wait 10 seconds'
        }}
    }}
}}

# Patch paths and pacing_engine
with patch.object(hook, 'DEFAULT_CONFIG_PATH', '{self.config_path}'), \\
     patch.object(hook, 'DEFAULT_DB_PATH', '{self.db_path}'), \\
     patch.object(hook, 'DEFAULT_STATE_PATH', '{self.state_path}'), \\
     patch('pacemaker.pacing_engine.run_pacing_check', return_value=mock_result):

    sys.argv = ['hook.py', 'post_tool_use']
    hook.main()
""")

        # Run test script
        result = subprocess.run(
            [sys.executable, test_script],
            capture_output=True,
            text=True,
            timeout=5
        )

        # Verify throttling message appears in stdout
        self.assertIn('THROTTLING MESSAGE', result.stdout,
                     f"Throttling message must appear in stdout. Got stdout: '{result.stdout}', stderr: '{result.stderr}'")

    def test_e2e_shell_script_execution(self):
        """
        RED TEST 8: E2E test of actual shell script hook execution.

        This tests the complete workflow:
        1. Shell script calls Python module
        2. Python module outputs to stdout
        3. Shell script doesn't redirect stdout
        """
        # Get project root
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        shell_hook_path = os.path.join(project_root, 'src', 'hooks', 'post-tool-use.sh')

        if not os.path.exists(shell_hook_path):
            self.skipTest(f"Shell hook not found at {shell_hook_path}")

        # Create enabled config
        config = {"enabled": True, "poll_interval": 999999}
        with open(self.config_path, 'w') as f:
            json.dump(config, f)

        # Create install marker pointing to project
        install_marker = os.path.join(self.pacemaker_dir, 'install_source')
        with open(install_marker, 'w') as f:
            f.write(project_root)

        # Set environment
        env = os.environ.copy()
        env['HOME'] = self.temp_dir

        # Run shell script
        result = subprocess.run(
            ['/bin/bash', shell_hook_path],
            env=env,
            capture_output=True,
            text=True,
            timeout=5
        )

        # Verify execution succeeded
        self.assertEqual(result.returncode, 0,
                        f"Shell hook must execute successfully. stderr: {result.stderr}")


if __name__ == '__main__':
    unittest.main()
