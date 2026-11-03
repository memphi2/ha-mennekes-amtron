"""Recorder suggestions resolve renamed/multiple devices without changing HA."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from homeassistant.core import Context
from homeassistant.exceptions import Unauthorized, UnknownUser
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.mennekes_amtron_rtu.const import DOMAIN
from custom_components.mennekes_amtron_rtu.entity import entry_entity_unique_id
from tests.fakes import FakeModbusClient, device_bank
from tests.test_home_assistant_smoke import _boot


def test_profiles_use_actual_ids_and_read_permissions_without_writes(tmp_path):
    async def run():
        transports = []

        def factory(port, **_kwargs):
            transport = FakeModbusClient(device_bank(serial=f"ABC12345678{port[-1]}"))
            transports.append(transport)
            return transport

        with patch("custom_components.mennekes_amtron_rtu.client.AsyncModbusSerialClient", side_effect=factory):
            hass = await _boot(tmp_path / "config")
            try:
                for index in (1, 2):
                    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
                    await hass.config_entries.flow.async_configure(flow["flow_id"], {
                        "port": f"/dev/fake{index}", "device_id": 50, "baudrate": 57600,
                        "frame": "8n2", "search": "off",
                    })
                    await hass.async_block_till_done()
                entries = hass.config_entries.async_entries(DOMAIN)
                assert len(entries) == 2
                registry = er.async_get(hass)
                original = registry.async_get_entity_id("sensor", DOMAIN, entry_entity_unique_id(entries[0], "current_l1"))
                registry.async_update_entity(original, new_entity_id="sensor.garage_custom_current")
                devices = [dr.async_entries_for_config_entry(dr.async_get(hass), e.entry_id)[0].id for e in entries]
                for profile, count in (("full", 0), ("detail", 20), ("lean", 30)):
                    response = await hass.services.async_call(DOMAIN, "get_recorder_exclusions", {
                        "device_id": devices + devices[:1], "profile": profile,
                    }, blocking=True, return_response=True)
                    assert len(response["entities"]) == count
                    assert yaml.safe_load(response["yaml"])["recorder"]["exclude"]["entities"] == response["entities"]
                    if count:
                        assert "sensor.garage_custom_current" in response["entities"]
                    for entry in entries:
                        for key in ("power_total", "energy_total"):
                            entity_id = registry.async_get_entity_id("sensor", DOMAIN, entry_entity_unique_id(entry, key))
                            assert entity_id not in response["entities"]
                single = await hass.services.async_call(DOMAIN, "get_recorder_exclusions", {
                    "device_id": devices[0], "profile": "lean",
                }, blocking=True, return_response=True)
                assert len(single["entities"]) == 15
                assert all(not transport.writes for transport in transports)
                assert not hass.services.has_service("recorder", "purge")
                denied = SimpleNamespace(id="denied", permissions=SimpleNamespace(check_entity=lambda *_: False))
                hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=denied))
                with pytest.raises(Unauthorized):
                    await hass.services.async_call(DOMAIN, "get_recorder_exclusions", {
                        "device_id": devices[0], "profile": "lean",
                    }, blocking=True, return_response=True, context=Context(user_id="denied"))
                hass.auth.async_get_user.return_value = None
                with pytest.raises(UnknownUser):
                    await hass.services.async_call(DOMAIN, "get_recorder_exclusions", {
                        "device_id": devices[0], "profile": "lean",
                    }, blocking=True, return_response=True, context=Context(user_id="missing"))
            finally:
                await hass.async_stop()

    asyncio.run(run())


@pytest.mark.parametrize("target", ["missing", "foreign", "gone", "wallbox"])
def test_profile_ignores_other_integrations_and_missing_registry_entries(monkeypatch, target):
    from homeassistant.exceptions import ServiceValidationError

    from custom_components.mennekes_amtron_rtu import recorder_profile
    from tests.ha_fakes import FakeConfigEntry, build_runtime

    async def run():
        hass, entry, _transport = await build_runtime()
        foreign = FakeConfigEntry(entry_id="foreign")
        foreign.domain = "other_integration"
        originals = hass.config_entries.async_get_entry
        monkeypatch.setattr(hass.config_entries, "async_get_entry", lambda key: foreign if key == "foreign" else originals(key))
        devices = {
            "foreign": SimpleNamespace(config_entries={"foreign"}),
            "gone": SimpleNamespace(config_entries={"deleted"}),
            "wallbox": SimpleNamespace(config_entries={entry.entry_id}),
        }
        registry = SimpleNamespace(
            async_get_entity_id=lambda _kind, _domain, key: (
                "sensor.wrong_device" if key.endswith("current_l1") else
                "sensor.deleted" if key.endswith("current_l2") else None
            ),
            async_get=lambda entity: SimpleNamespace(device_id="other") if entity == "sensor.wrong_device" else None,
        )
        monkeypatch.setattr(recorder_profile.dr, "async_get", lambda _: SimpleNamespace(async_get=devices.get))
        monkeypatch.setattr(recorder_profile.er, "async_get", lambda _: registry)
        recorder_profile.async_setup_recorder_service(hass)
        handler, _schema = hass.services.registered[(DOMAIN, "get_recorder_exclusions")]
        call = SimpleNamespace(context=Context(), data={"device_id": [target], "profile": "detail"})
        if target == "wallbox":
            assert (await handler(call))["entities"] == []
        else:
            with pytest.raises(ServiceValidationError):
                await handler(call)

    asyncio.run(run())
