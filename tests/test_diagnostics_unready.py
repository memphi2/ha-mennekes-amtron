"""Diagnostics and repairs of an entry that is not running."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from homeassistant.helpers import issue_registry as ir

import custom_components.mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu import diagnostics
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN


def test_an_entry_whose_setup_never_finished_still_gives_diagnostics():
    """The service is down at the start of Home Assistant: exactly when a dump is wanted."""
    entry = SimpleNamespace(
        entry_id="entry", data={"server_url": "http://service:8765"},
        state=SimpleNamespace(value="setup_retry"),
    )
    result = asyncio.run(diagnostics.async_get_config_entry_diagnostics(None, entry))
    assert result == {"setup": "setup_retry", "bus": {"transport": "service"}}


def test_a_disabled_entry_takes_its_repairs_along(monkeypatch):
    deleted = []
    issues = {(DOMAIN, "entry_write_rejected"): 1, (DOMAIN, "other_write_rejected"): 1, ("else", "entry_x"): 1}
    monkeypatch.setattr(ir, "async_get", lambda _hass: SimpleNamespace(issues=issues))
    monkeypatch.setattr(ir, "async_delete_issue", lambda _hass, domain, issue: deleted.append((domain, issue)))
    integration._delete_issues(None, SimpleNamespace(entry_id="entry"))
    assert deleted == [(DOMAIN, "entry_write_rejected")]


def test_disabling_an_entry_that_never_finished_its_setup_removes_its_repairs(monkeypatch):
    """Home Assistant never unloads such an entry; its change is the only notice."""
    from tests.ha_fakes import FakeHass

    listeners = []
    deleted = []
    monkeypatch.setattr(integration, "async_dispatcher_connect", lambda _hass, _signal, target: listeners.append(target))
    monkeypatch.setattr(integration, "_delete_issues", lambda _hass, entry: deleted.append(entry.entry_id))

    async def run():
        assert await integration.async_setup(FakeHass(), {})

    asyncio.run(run())
    (changed,) = listeners
    changed("updated", SimpleNamespace(entry_id="a", domain=DOMAIN, disabled_by=None))
    changed("updated", SimpleNamespace(entry_id="b", domain="else", disabled_by="user"))
    changed("updated", SimpleNamespace(entry_id="c", domain=DOMAIN, disabled_by="user"))
    assert deleted == ["c"]
