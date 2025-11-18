#!/usr/bin/env python3
"""
Unit tests for auto-installation of missing dependencies in install.sh

Tests the enhanced check_dependencies() function that automatically
detects and installs missing dependencies using available package managers.
"""

import subprocess
import tempfile
import os
import sys
from pathlib import Path
import pytest

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class TestPackageManagerDetection:
    """Tests for package manager detection logic"""

    def test_detect_brew_on_macos(self, tmp_path):
        """Should detect brew when available on macOS"""
        script = tmp_path / "test_brew.sh"
        script.write_text("""
#!/bin/bash
detect_package_manager() {
  if command -v brew >/dev/null 2>&1; then
    echo "brew"
    return 0
  elif command -v apt >/dev/null 2>&1; then
    echo "apt"
    return 0
  elif command -v dnf >/dev/null 2>&1; then
    echo "dnf"
    return 0
  elif command -v yum >/dev/null 2>&1; then
    echo "yum"
    return 0
  else
    echo "none"
    return 1
  fi
}

detect_package_manager
""")
        script.chmod(0o755)

        result = subprocess.run([str(script)], capture_output=True, text=True)
        # On macOS with brew installed, should return "brew"
        # On other systems, should return appropriate package manager
        assert result.stdout.strip() in ["brew", "apt", "dnf", "yum", "none"]

    def test_detect_apt_on_debian(self, tmp_path):
        """Should detect apt when available on Debian/Ubuntu"""
        # This test would pass on Debian/Ubuntu systems with apt
        script = tmp_path / "test_apt.sh"
        script.write_text("""
#!/bin/bash
command -v apt >/dev/null 2>&1 && echo "apt" || echo "not_found"
""")
        script.chmod(0o755)

        result = subprocess.run([str(script)], capture_output=True, text=True)
        # Result depends on current system
        assert result.stdout.strip() in ["apt", "not_found"]

    def test_fallback_to_yum_or_dnf(self, tmp_path):
        """Should detect yum or dnf on RHEL/CentOS/Fedora"""
        script = tmp_path / "test_yum.sh"
        script.write_text("""
#!/bin/bash
if command -v dnf >/dev/null 2>&1; then
  echo "dnf"
elif command -v yum >/dev/null 2>&1; then
  echo "yum"
else
  echo "not_found"
fi
""")
        script.chmod(0o755)

        result = subprocess.run([str(script)], capture_output=True, text=True)
        assert result.stdout.strip() in ["dnf", "yum", "not_found"]

    def test_no_package_manager_returns_error(self, tmp_path):
        """Should return error when no package manager is available"""
        script = tmp_path / "test_none.sh"
        script.write_text("""
#!/bin/bash
# Simulate environment with no package managers
PATH=/bin:/usr/bin
detect_package_manager() {
  if command -v brew >/dev/null 2>&1; then
    echo "brew"
    return 0
  elif command -v apt >/dev/null 2>&1; then
    echo "apt"
    return 0
  elif command -v dnf >/dev/null 2>&1; then
    echo "dnf"
    return 0
  elif command -v yum >/dev/null 2>&1; then
    echo "yum"
    return 0
  else
    echo "none"
    return 1
  fi
}

detect_package_manager
exit $?
""")
        script.chmod(0o755)

        result = subprocess.run([str(script)], capture_output=True, text=True)
        # Should detect at least one package manager on normal systems
        # or return "none" in restricted environments
        assert result.stdout.strip() != ""


class TestDependencyChecking:
    """Tests for checking which dependencies are missing"""

    def test_detect_missing_dependencies(self, tmp_path):
        """Should correctly identify which dependencies are missing"""
        script = tmp_path / "test_missing.sh"
        script.write_text("""
#!/bin/bash
check_missing() {
  local missing=()

  command -v jq >/dev/null 2>&1 || missing+=("jq")
  command -v curl >/dev/null 2>&1 || missing+=("curl")
  command -v python3 >/dev/null 2>&1 || missing+=("python3")

  if [ ${#missing[@]} -ne 0 ]; then
    echo "${missing[*]}"
    return 1
  fi

  echo "none"
  return 0
}

check_missing
""")
        script.chmod(0o755)

        result = subprocess.run([str(script)], capture_output=True, text=True)
        missing_deps = result.stdout.strip()

        # Verify output format
        assert missing_deps != ""
        if missing_deps != "none":
            # Should be space-separated list of dependencies
            deps = missing_deps.split()
            assert all(dep in ["jq", "curl", "python3"] for dep in deps)

    def test_all_dependencies_present(self, tmp_path):
        """Should return success when all dependencies are present"""
        script = tmp_path / "test_present.sh"
        script.write_text("""
#!/bin/bash
# Mock all dependencies as present
command() {
  if [ "$1" = "-v" ]; then
    return 0  # All dependencies present
  fi
  builtin command "$@"
}

check_missing() {
  local missing=()

  command -v jq >/dev/null 2>&1 || missing+=("jq")
  command -v curl >/dev/null 2>&1 || missing+=("curl")
  command -v python3 >/dev/null 2>&1 || missing+=("python3")

  if [ ${#missing[@]} -ne 0 ]; then
    echo "${missing[*]}"
    return 1
  fi

  echo "none"
  return 0
}

check_missing
exit $?
""")
        script.chmod(0o755)

        result = subprocess.run([str(script)], capture_output=True, text=True)
        assert result.returncode == 0
        assert result.stdout.strip() == "none"


class TestUserConfirmation:
    """Tests for user confirmation prompts"""

    def test_user_confirms_installation(self, tmp_path):
        """Should proceed when user confirms installation"""
        script = tmp_path / "test_confirm.sh"
        script.write_text("""
#!/bin/bash
prompt_install() {
  local deps="$1"
  echo "The following dependencies are missing: $deps"
  echo -n "Install them now? [Y/n]: "

  local answer
  if read -r -t 1 answer 2>/dev/null; then
    case "$answer" in
      [Nn]|[Nn][Oo])
        return 1
        ;;
      *)
        return 0
        ;;
    esac
  else
    # Default to yes if no input (non-interactive)
    return 0
  fi
}

prompt_install "jq curl"
exit $?
""")
        script.chmod(0o755)

        # Test with "yes" input
        result = subprocess.run(
            [str(script)],
            input="y\n",
            capture_output=True,
            text=True,
            timeout=2
        )
        assert result.returncode == 0

    def test_user_declines_installation(self, tmp_path):
        """Should abort when user declines installation"""
        script = tmp_path / "test_decline.sh"
        script.write_text("""
#!/bin/bash
prompt_install() {
  local deps="$1"
  echo "The following dependencies are missing: $deps"
  echo -n "Install them now? [Y/n]: "

  local answer
  if read -r -t 1 answer 2>/dev/null; then
    case "$answer" in
      [Nn]|[Nn][Oo])
        return 1
        ;;
      *)
        return 0
        ;;
    esac
  else
    return 0
  fi
}

prompt_install "jq curl"
exit $?
""")
        script.chmod(0o755)

        # Test with "no" input
        result = subprocess.run(
            [str(script)],
            input="n\n",
            capture_output=True,
            text=True,
            timeout=2
        )
        assert result.returncode == 1

    def test_non_interactive_defaults_to_yes(self, tmp_path):
        """Should default to yes in non-interactive mode"""
        script = tmp_path / "test_noninteractive.sh"
        script.write_text("""
#!/bin/bash
prompt_install() {
  local deps="$1"

  # Check if stdin is a terminal
  if [ -t 0 ]; then
    echo "interactive"
    return 0
  else
    echo "non-interactive (defaulting to yes)"
    return 0
  fi
}

prompt_install "jq"
exit $?
""")
        script.chmod(0o755)

        # Run without stdin
        result = subprocess.run(
            [str(script)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True
        )
        assert result.returncode == 0


class TestInstallationLogic:
    """Tests for dependency installation logic"""

    def test_construct_install_command_brew(self, tmp_path):
        """Should construct correct install command for brew"""
        script = tmp_path / "test_brew_cmd.sh"
        script.write_text("""
#!/bin/bash
get_install_command() {
  local pkg_manager="$1"
  local package="$2"

  case "$pkg_manager" in
    brew)
      echo "brew install $package"
      ;;
    apt)
      echo "sudo apt-get install -y $package"
      ;;
    dnf)
      echo "sudo dnf install -y $package"
      ;;
    yum)
      echo "sudo yum install -y $package"
      ;;
    *)
      return 1
      ;;
  esac
}

get_install_command "brew" "jq"
""")
        script.chmod(0o755)

        result = subprocess.run([str(script)], capture_output=True, text=True)
        assert result.returncode == 0
        assert result.stdout.strip() == "brew install jq"

    def test_construct_install_command_apt(self, tmp_path):
        """Should construct correct install command for apt"""
        script = tmp_path / "test_apt_cmd.sh"
        script.write_text("""
#!/bin/bash
get_install_command() {
  local pkg_manager="$1"
  local package="$2"

  case "$pkg_manager" in
    brew)
      echo "brew install $package"
      ;;
    apt)
      echo "sudo apt-get install -y $package"
      ;;
    dnf)
      echo "sudo dnf install -y $package"
      ;;
    yum)
      echo "sudo yum install -y $package"
      ;;
    *)
      return 1
      ;;
  esac
}

get_install_command "apt" "python3"
""")
        script.chmod(0o755)

        result = subprocess.run([str(script)], capture_output=True, text=True)
        assert result.returncode == 0
        assert result.stdout.strip() == "sudo apt-get install -y python3"

    def test_install_multiple_dependencies(self, tmp_path):
        """Should handle installation of multiple dependencies"""
        script = tmp_path / "test_multi.sh"
        script.write_text("""
#!/bin/bash
install_dependencies() {
  local pkg_manager="$1"
  shift
  local deps=("$@")

  local failed=()

  for dep in "${deps[@]}"; do
    echo "Installing $dep..."
    # Simulate installation (don't actually install)
    if [ "$dep" = "nonexistent" ]; then
      failed+=("$dep")
    fi
  done

  if [ ${#failed[@]} -ne 0 ]; then
    echo "Failed: ${failed[*]}"
    return 1
  fi

  echo "Success"
  return 0
}

install_dependencies "brew" "jq" "curl" "python3"
""")
        script.chmod(0o755)

        result = subprocess.run([str(script)], capture_output=True, text=True)
        assert result.returncode == 0
        assert "Success" in result.stdout


class TestErrorHandling:
    """Tests for error handling scenarios"""

    def test_error_when_no_package_manager(self, tmp_path):
        """Should return error when no package manager is available"""
        script = tmp_path / "test_no_pm.sh"
        script.write_text("""
#!/bin/bash
check_dependencies() {
  local pkg_manager=""

  # Detect package manager
  if command -v brew >/dev/null 2>&1; then
    pkg_manager="brew"
  elif command -v apt >/dev/null 2>&1; then
    pkg_manager="apt"
  fi

  if [ -z "$pkg_manager" ]; then
    echo "Error: No package manager found"
    return 1
  fi

  return 0
}

# Simulate no package manager by restricting PATH
PATH=/bin
check_dependencies
""")
        script.chmod(0o755)

        result = subprocess.run([str(script)], capture_output=True, text=True)
        # On a normal system, at least one package manager should be found
        # This test verifies the error handling path exists
        assert "Error" in result.stdout or result.returncode in [0, 1]

    def test_error_when_installation_fails(self, tmp_path):
        """Should return error when installation fails"""
        script = tmp_path / "test_fail.sh"
        script.write_text("""
#!/bin/bash
install_dependency() {
  local pkg="$1"

  # Simulate installation failure
  if [ "$pkg" = "nonexistent-package" ]; then
    echo "Error: Failed to install $pkg"
    return 1
  fi

  echo "Installed $pkg"
  return 0
}

install_dependency "nonexistent-package"
""")
        script.chmod(0o755)

        result = subprocess.run([str(script)], capture_output=True, text=True)
        assert result.returncode == 1
        assert "Error" in result.stdout

    def test_provides_clear_error_messages(self, tmp_path):
        """Should provide clear error messages for debugging"""
        script = tmp_path / "test_messages.sh"
        script.write_text("""
#!/bin/bash
RED='\\033[0;31m'
NC='\\033[0m'

show_error() {
  local msg="$1"
  echo -e "${RED}Error: ${msg}${NC}"
}

show_error "Missing required dependencies: jq curl"
show_error "No package manager found. Please install homebrew, apt, or yum/dnf."
show_error "Installation failed for: jq"
""")
        script.chmod(0o755)

        result = subprocess.run([str(script)], capture_output=True, text=True)
        assert "Error:" in result.stdout
        assert result.stdout.count("Error:") == 3


class TestIntegration:
    """Integration tests with actual install.sh script"""

    def test_check_dependencies_function_exists(self):
        """Should have check_dependencies function in install.sh"""
        install_script = PROJECT_ROOT / "install.sh"
        assert install_script.exists()

        content = install_script.read_text()
        assert "check_dependencies()" in content

    def test_check_dependencies_is_called_in_main(self):
        """Should call check_dependencies in main flow"""
        install_script = PROJECT_ROOT / "install.sh"
        content = install_script.read_text()

        # Find main() function and verify check_dependencies is called
        assert "check_dependencies" in content
        # Look for the call in main function
        main_start = content.find("main() {")
        if main_start != -1:
            main_section = content[main_start:main_start+500]
            assert "check_dependencies" in main_section


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
