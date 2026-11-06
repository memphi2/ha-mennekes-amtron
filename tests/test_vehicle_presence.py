"""Presence must not publish a false disconnect during faults or startup."""

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.vehicle import vehicle_connected
from tests.test_binary_sensor import _sensors


@pytest.mark.parametrize(("evse", "cp", "expected"), [
    (EvseState.IDLE, CpState.A1, False), (EvseState.IDLE, CpState.A2, False),
    (EvseState.IDLE, CpState.B1, None), (EvseState.IDLE, None, None),
    (EvseState.IDLE, True, None), (EvseState.IDLE, CpState.E, None),
    (EvseState.ERROR, CpState.B1, True), (EvseState.ERROR, CpState.C2, True),
    (EvseState.ERROR, CpState.A1, None), (EvseState.ERROR, CpState.F, None),
    (EvseState.ERROR, None, None), (EvseState.SERVICE_MODE, CpState.B2, True),
    (EvseState.SERVICE_MODE, CpState.INIT, None),
    (EvseState.CHARGING, CpState.C2, True), (EvseState.CHARGING, None, True),
    (EvseState.CHARGING, CpState.A2, None), (EvseState.CHARGING, CpState.F, True),
    (EvseState.NOT_INITIALIZED, CpState.B1, None), (99, CpState.B1, None),
    (None, CpState.B1, None), (True, CpState.B1, None),
])
def test_cp_and_evse_presence_agree_or_remain_unknown(evse, cp, expected):
    assert vehicle_connected(evse, cp, cp_supported=True) is expected


@pytest.mark.parametrize(("evse", "expected"), [
    (EvseState.IDLE, False), (EvseState.EV_CONNECTED, True),
    (EvseState.CHARGING, True), (EvseState.ERROR, None),
    (EvseState.SERVICE_MODE, None), (EvseState.NOT_INITIALIZED, None),
])
def test_legacy_layouts_never_infer_unplugging_from_faults(evse, expected):
    assert vehicle_connected(evse, None, cp_supported=False) is expected


def test_entity_keeps_vehicle_connected_during_fault_then_unknown_on_lost_cp():
    async def run():
        sensors, entry = await _sensors()
        values = entry.runtime_data.coordinator.data.values
        sensor = sensors["vehicle_connected"]
        values.update(evse_state=EvseState.CHARGING, cp_state=CpState.C2)
        assert sensor.is_on is True
        values["evse_state"] = EvseState.ERROR
        assert sensor.is_on is True
        values["cp_state"] = None
        assert sensor.is_on is None
        values.update(evse_state=EvseState.IDLE, cp_state=CpState.A1)
        assert sensor.is_on is False
    asyncio.run(run())
