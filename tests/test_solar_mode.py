"""The solar mode shown on the wallbox is set by the service."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.decode import decode_register
from custom_components.mennekes_amtron_rtu.device.enums import SolarChargingMode
from custom_components.mennekes_amtron_rtu.device.errors import DeviceValidationError
from custom_components.mennekes_amtron_rtu.select import SELECTS, AmtronSelect
from tests.ha_fakes import build_runtime
from tests.test_modbus_service import service_fixture


def _solar_writes(transport) -> list[int]:
    return [
        decode_register(R.SOLAR_CHARGING_MODE, words)
        for address, words in transport.writes
        if address == R.SOLAR_CHARGING_MODE.address
    ]


def test_the_service_writes_the_solar_mode_without_waiting(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        await service.heartbeat.async_stop()
        # Right after a state change: the mode switches no charge, so no interval.
        service.control.write_state.mode_change_at = service.control.monotonic()
        snapshot = await service.command({
            "command": "solar", "value": int(SolarChargingMode.SUNSHINE),
            "expected_serial_number": service.identity.serial_number,
        })
        assert _solar_writes(transport) == [int(SolarChargingMode.SUNSHINE)]
        assert service.control.pending_action is None
        assert snapshot["values"]["solar_charging_mode"] == int(SolarChargingMode.SUNSHINE)
        for bad in (4, -1, "1", True):
            with pytest.raises(ValueError, match="outside the allowed range"):
                await service.command({
                    "command": "solar", "value": bad,
                    "expected_serial_number": service.identity.serial_number,
                })
        assert len(_solar_writes(transport)) == 1
        await service.stop()

    asyncio.run(run())


def test_a_read_only_service_refuses_the_solar_mode(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path, read_only=True)
        await service.heartbeat.async_stop()
        with pytest.raises(DeviceValidationError):
            await service.command({
                "command": "solar", "value": 1,
                "expected_serial_number": service.identity.serial_number,
            })
        assert _solar_writes(transport) == []
        await service.stop()

    asyncio.run(run())


def _select(entry) -> AmtronSelect:
    description = next(d for d in SELECTS if d.key == "solar_charging_mode")
    return AmtronSelect(entry, description)


def test_the_select_shows_and_sets_the_mode_through_the_service():
    async def run():
        _hass, entry, transport = await build_runtime()
        select = _select(entry)
        assert set(select.options) == {"not_active", "sunshine", "sunshine_plus", "fast"}
        await select.async_select_option("sunshine_plus")
        assert _solar_writes(transport) == [int(SolarChargingMode.SUNSHINE_PLUS)]
        await entry.runtime_data.coordinator.async_refresh()
        assert select.current_option == "sunshine_plus"
        await entry.runtime_data.client.async_close()
        await entry.service.stop()

    asyncio.run(run())


def test_a_read_only_entry_offers_no_solar_select():
    async def run():
        _hass, entry, _transport = await build_runtime(read_only=True)
        description = next(d for d in SELECTS if d.key == "solar_charging_mode")
        assert not description.exists(entry.runtime_data)
        await entry.runtime_data.client.async_close()
        await entry.service.stop()

    asyncio.run(run())
