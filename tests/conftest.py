"""Shared test setup."""

from __future__ import annotations

import pytest

from custom_components.mennekes_amtron_rtu.device import server


@pytest.fixture(autouse=True)
def keep_the_test_process_privileges(monkeypatch):
    """The service's start gives up privileges for good; a test run must not."""
    monkeypatch.setattr(server, "drop_capabilities", lambda: None)
