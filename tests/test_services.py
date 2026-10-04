from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.mennekes_amtron import registers as R
from custom_components.mennekes_amtron import services
from custom_components.mennekes_amtron.const import (
    ATTR_ALLOW_UNLIMITED,
    ATTR_CURRENT,
    DOMAIN,
    SERVICE_SET_CHARGING_CURRENT,
)
from tests.ha_fakes import build_runtime


class FakeDeviceRegistry:
    def __init__(self, devices: dict[str, object]) -> None:
        self._devices = devices

    def async_get(self, device_id: str) -> object:
        return self._devices.get(device_id)


def _patch_registry(monkeypatch: pytest.MonkeyPatch, devices: dict[str, object]) -> None:
    monkeypatch.setattr(
        services.dr, "async_get", lambda _hass: FakeDeviceRegistry(devices)
    )


def test_the_action_is_registered_once() -> None:
    async def run() -> None:
        hass, _entry, _transport = await build_runtime()
        services.async_setup_services(hass)
        services.async_setup_services(hass)
        assert hass.services.has_service(DOMAIN, SERVICE_SET_CHARGING_CURRENT)
        assert len(hass.services.registered) == 1

    asyncio.run(run())


def test_the_schema_rejects_the_invalid_band_through_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        hass, entry, transport = await build_runtime()
        _patch_registry(
            monkeypatch, {"dev": SimpleNamespace(config_entries={entry.entry_id})}
        )
        services.async_setup_services(hass)
        handler, _schema = hass.services.registered[
            (DOMAIN, SERVICE_SET_CHARGING_CURRENT)
        ]

        await handler(
            SimpleNamespace(
                data={
                    "device_id": ["dev"],
                    ATTR_CURRENT: 10.0,
                    ATTR_ALLOW_UNLIMITED: False,
                }
            )
        )
        assert any(
            address == R.CHARGING_CURRENT_EMS.address
            for address, _ in transport.writes
        )

        with pytest.raises(ServiceValidationError):
            await handler(
                SimpleNamespace(
                    data={
                        "device_id": ["dev"],
                        ATTR_CURRENT: 0.0,
                        ATTR_ALLOW_UNLIMITED: False,
                    }
                )
            )

    asyncio.run(run())


def test_the_schema_accepts_the_documented_fields() -> None:
    validated = services.SET_CHARGING_CURRENT_SCHEMA(
        {"device_id": "dev", ATTR_CURRENT: 6}
    )
    assert validated[ATTR_CURRENT] == 6.0
    assert validated[ATTR_ALLOW_UNLIMITED] is False


def test_an_unknown_device_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        hass, _entry, _transport = await build_runtime()
        _patch_registry(monkeypatch, {})
        with pytest.raises(ServiceValidationError) as excinfo:
            services.async_resolve_controls(hass, ["missing"])
        assert excinfo.value.translation_key == "unknown_device"

    asyncio.run(run())


def test_an_unloaded_entry_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        entry.runtime_data = None
        _patch_registry(
            monkeypatch, {"dev": SimpleNamespace(config_entries={entry.entry_id})}
        )
        with pytest.raises(ServiceValidationError) as excinfo:
            services.async_resolve_controls(hass, ["dev"])
        assert excinfo.value.translation_key == "entry_not_loaded"

    asyncio.run(run())


def test_a_device_of_another_integration_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        entry.domain = "other"
        _patch_registry(
            monkeypatch, {"dev": SimpleNamespace(config_entries={entry.entry_id})}
        )
        with pytest.raises(ServiceValidationError) as excinfo:
            services.async_resolve_controls(hass, ["dev"])
        assert excinfo.value.translation_key == "no_wallbox_target"

    asyncio.run(run())


def test_an_unknown_entry_id_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        hass, _entry, _transport = await build_runtime()
        _patch_registry(
            monkeypatch, {"dev": SimpleNamespace(config_entries={"nope"})}
        )
        with pytest.raises(ServiceValidationError):
            services.async_resolve_controls(hass, ["dev"])

    asyncio.run(run())
