"""
Issue #177 (re-review B1): unit tests must never read the real macOS Keychain.

tests/conftest.py stubs api_client._load_token_from_keychain to None for every
test and makes subprocess.run fail the test when asked to run `security`
without a test-level patch. These tests prove both layers. HOME is the
per-test fake home set by conftest's autouse fixture, so Path.home() here never
points at the real home.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from pacemaker import api_client

# Captured at import (collection) time, before any fixture stubs it.
ORIGINAL_LOAD_TOKEN_FROM_KEYCHAIN = api_client._load_token_from_keychain

FILE_TOKEN = "fake-file-token-A"
SUBPROCESS_TIMEOUT_SECONDS = 5


def _write_creds_file(token: str) -> None:
    path = Path.home() / ".claude" / ".credentials.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"claudeAiOauth": {"accessToken": token}}))


def test_keychain_loader_is_stubbed_by_default():
    assert api_client._load_token_from_keychain is not (
        ORIGINAL_LOAD_TOKEN_FROM_KEYCHAIN
    )
    assert api_client._load_token_from_keychain() is None


def test_keychain_loader_is_stubbed_on_the_src_prefixed_module_copy_too():
    """Some tests import the package as `src.pacemaker...`; that is a distinct
    module object from `pacemaker.api_client` and must be stubbed as well."""
    import importlib

    src_api_client = importlib.import_module("src.pacemaker.api_client")
    assert src_api_client is not api_client
    assert src_api_client._load_token_from_keychain() is None


def test_darwin_load_access_token_uses_file_without_touching_keychain():
    _write_creds_file(FILE_TOKEN)
    with patch("pacemaker.api_client.platform.system", return_value="Darwin"):
        assert api_client.load_access_token() == FILE_TOKEN


def test_guard_fails_test_when_security_is_run_without_a_patch():
    with pytest.raises(pytest.fail.Exception, match="security"):
        subprocess.run(
            ["security", "help"], capture_output=True, timeout=SUBPROCESS_TIMEOUT_SECONDS
        )


def test_guard_trips_through_the_original_keychain_loader(monkeypatch):
    """pytest.fail is a BaseException, so the loader's `except Exception` cannot swallow it."""
    monkeypatch.setattr(
        api_client, "_load_token_from_keychain", ORIGINAL_LOAD_TOKEN_FROM_KEYCHAIN
    )
    with patch("pacemaker.api_client.platform.system", return_value="Darwin"):
        with pytest.raises(pytest.fail.Exception, match="security"):
            api_client.load_access_token()


def test_guard_allows_other_commands():
    """The guard is specific to `security`; it must not block unrelated commands."""
    result = subprocess.run(
        ["echo", "ok"],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )
    assert result.stdout.strip() == "ok"
