"""Manual service selection can reuse authenticated Supervisor discovery."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from custom_components.mennekes_amtron_rtu import config_flow
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.supervisor_discovery import (
    async_app_discoveries,
)
from tests.ha_fakes import FakeHass
from tests.test_config_flow import Flow


@pytest.mark.parametrize("scenario", ["core", "app", "unavailable", "other_service", "other_app"])
def test_only_trusted_supervisor_announcements_are_used(monkeypatch, scenario):
    class SupervisorError(Exception):
        pass

    async def run():
        hass = FakeHass()
        if scenario != "core":
            hass.config.components.add("hassio")
        item = SimpleNamespace(
            config={"host": "a87b2ffe-mennekes-amtron-modbus", "port": 8765, "token": "x" * 32},
            service="other" if scenario == "other_service" else DOMAIN,
            addon="a87b2ffe_other" if scenario == "other_app" else "a87b2ffe_mennekes_amtron_modbus",
            uuid=UUID(int=1),
        )
        listing = AsyncMock(return_value=[item])
        if scenario == "unavailable":
            listing.side_effect = SupervisorError
        supervisor = SimpleNamespace(discovery=SimpleNamespace(list=listing))
        monkeypatch.setitem(sys.modules, "aiohasupervisor", SimpleNamespace(SupervisorError=SupervisorError))
        monkeypatch.setitem(sys.modules, "homeassistant.components.hassio.handler", SimpleNamespace(get_supervisor_client=lambda _hass: supervisor))
        result = await async_app_discoveries(hass)
        if scenario == "app":
            assert len(result) == 1
            assert result[0].config == item.config
            assert result[0].slug == item.addon
            assert result[0].uuid == item.uuid.hex
        else:
            assert result == []
        assert listing.await_count == (0 if scenario == "core" else 1)

    asyncio.run(run())


def test_multiple_apps_require_selecting_the_discovered_device(monkeypatch):
    async def run():
        flow = Flow(FakeHass())
        monkeypatch.setattr(config_flow, "async_app_discoveries", AsyncMock(return_value=[object(), object()]))
        result = await flow.async_step_server()
        assert result["type"] == "abort"
        assert result["reason"] == "multiple_service_apps"

    asyncio.run(run())
