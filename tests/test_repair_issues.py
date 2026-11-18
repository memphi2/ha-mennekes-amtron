from __future__ import annotations

import asyncio

from custom_components.mennekes_amtron_rtu.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.data import DeviceIdentity
from custom_components.mennekes_amtron_rtu.device.registers import (
    LAYOUT_V01_02,
    LAYOUT_V01_03,
)
from custom_components.mennekes_amtron_rtu.repair_issues import (
    fallback_not_configured,
    heartbeat_lost,
    layout_placeholders,
    unsupported_layout,
    write_rejected,
)
from tests.ha_fakes import build_runtime


def test_error_200_raises_the_heartbeat_issue() -> None:
    assert heartbeat_lost(WallboxData(values={"error_code": 200}))
    assert not heartbeat_lost(WallboxData(values={"error_code": 0}))
    assert not heartbeat_lost(WallboxData())


def test_a_zero_fallback_register_raises_the_fallback_issue() -> None:
    assert fallback_not_configured(WallboxData(values={"ems_fallback_current": 0}))
    assert not fallback_not_configured(
        WallboxData(values={"ems_fallback_current": 6})
    )
    assert not fallback_not_configured(WallboxData())


def test_an_older_layout_raises_the_layout_issue() -> None:
    assert unsupported_layout(DeviceIdentity(layout_version=LAYOUT_V01_02))
    assert not unsupported_layout(DeviceIdentity(layout_version=LAYOUT_V01_03))


def test_layout_placeholders_use_the_vendor_notation() -> None:
    placeholders = layout_placeholders(DeviceIdentity(layout_version=LAYOUT_V01_02))
    assert placeholders == {"layout": "v01.02", "validated_layout": "v01.03"}


def test_a_refused_write_raises_the_satellite_issue() -> None:
    async def run() -> None:
        _hass, entry, _wallbox = await build_runtime()
        client, diagnostics = entry.runtime_data.client, entry.service.diagnostics
        assert not write_rejected(client.snapshot)
        diagnostics.writes_rejected = 1
        diagnostics.write_errors.add("charging_release")
        await entry.runtime_data.coordinator.async_refresh()
        assert write_rejected(client.snapshot)
        diagnostics.write_errors.clear()
        await entry.runtime_data.coordinator.async_refresh()
        assert not write_rejected(client.snapshot)
        assert diagnostics.writes_rejected == 1

    asyncio.run(run())
