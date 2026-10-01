"""
Shared fixtures for the legacy scripted e2e suites under tests/e2e/.

Story #155 (review L7): the ``test_install*.py`` files run the REAL
``install.sh``. Since #155 that script registers the declare_intent MCP server
(``claude mcp add``) and allow-lists its tool. These suites exist to check
hooks/config/snapshot installation inside a fake HOME, so they must never reach
the real ``claude mcp`` CLI: ``PACEMAKER_SKIP_MCP_REGISTRATION=1`` makes
``install.sh`` skip both steps. The registration and permission logic have
their own tests (tests/test_intent_mcp_registration.py,
tests/test_intent_mcp_permissions.py), against a recording stand-in for the
CLI and tmp settings files.

Set through ``monkeypatch.setenv`` so every ``os.environ.copy()`` those suites
hand their subprocesses carries it.
"""

import pytest


@pytest.fixture(autouse=True)
def _install_suites_skip_mcp_registration(request, monkeypatch):
    if request.fspath.basename.startswith("test_install"):
        monkeypatch.setenv("PACEMAKER_SKIP_MCP_REGISTRATION", "1")
