"""Transient ambiguity is presentation only, counted once per bus observation."""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest
from aiohttp import ClientSession

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.charging_display import (
    ChargingDisplay,
)
from custom_components.mennekes_amtron_rtu.device.data import (
    ConnectionState,
    WallboxData,
)
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.poller import DevicePoller
from custom_components.mennekes_amtron_rtu.device.service_overview import (
    overview_snapshot,
)
from custom_components.mennekes_amtron_rtu.remote_errors import ServiceConnectionError
from custom_components.mennekes_amtron_rtu.service_client import ServiceClient
from tests.fakes import put
from tests.test_charging_status import _data
from tests.test_control import Harness
from tests.test_modbus_service import TOKEN, service_fixture


def test_ambiguity_needs_elapsed_time_and_a_second_observation():
    display = ChargingDisplay()
    data = _data(evse_state=EvseState.IDLE)
    before = deepcopy(data)
    for now, expected in [(100, "checking_status"), (105, "checking_status"),
                          (109.99, "checking_status"), (110, "state_uncertain"),
                          (3600, "state_uncertain")]:
        assert display.observe(data, cp_supported=True, now=now) == expected
    assert data == before
    display.reset()
    assert display.observe(data, cp_supported=True, now=10000) == "checking_status"
    assert ChargingDisplay().observe(data, cp_supported=True, now=10000) == "checking_status"


@pytest.mark.parametrize("interruption", ["recovery", "missing", "held", "fault"])
def test_gap_or_proven_state_resets_ambiguity_without_replaying_old_status(interruption):
    display = ChargingDisplay()
    data = _data(evse_state=EvseState.IDLE)
    assert display.observe(data, cp_supported=True, now=0) == "checking_status"
    assert display.observe(data, cp_supported=True, now=10) == "state_uncertain"
    observed = _data()
    expected = "preparing"
    if interruption == "missing":
        observed.values.pop("evse_state")
        expected = "data_missing"
    elif interruption == "held":
        observed.held = frozenset({"evse_state"})
        expected = "data_missing"
    elif interruption == "fault":
        observed.values["error_code"] = 200
        expected = "wallbox_error"
    assert display.observe(observed, cp_supported=True, now=11) == expected
    assert display.observe(data, cp_supported=True, now=1000) == "checking_status"


def test_layout_without_cp_does_not_wait_for_an_unsupported_register():
    data = _data(evse_state=EvseState.IDLE)
    data.values.pop("cp_state")
    display = ChargingDisplay()
    assert display.observe(data, cp_supported=False, now=0) == "no_vehicle"
    assert display.observe(data, cp_supported=True, now=10) == "data_missing"
    # Missing control data on a non-charging vehicle stays explicitly missing.
    data = _data(charging_release=None)
    assert display.observe(data, cp_supported=False, now=20) == "data_missing"


def test_poller_counts_actual_samples_and_resets_after_a_disconnect():
    async def run():
        h = Harness()
        await h.connect()
        put(h.transport.bank, R.EVSE_STATE, EvseState.IDLE)
        put(h.transport.bank, R.CP_STATE, CpState.B1)
        poller = DevicePoller(h.client, h.identity, connection_state=ConnectionState(),
                              monotonic=lambda: h.clock[0])
        data = await poller.async_poll(WallboxData(), last_success=True)
        assert data.charging_display == "checking_status"
        h.clock[0] += 10
        data = await poller.async_poll(data, last_success=True)
        assert data.charging_display == "state_uncertain"
        data = await poller.async_poll(data, last_success=False)
        assert data.charging_display == "checking_status"
        put(h.transport.bank, R.CP_STATE, CpState.A1)
        data = await poller.async_poll(data, last_success=True)
        assert data.charging_display == "no_vehicle"
        assert not h.transport.writes
    asyncio.run(run())


def test_shared_display_survives_remote_transport_without_counting_api_reads(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path, read_only=True)
        try:
            async with ClientSession() as session:
                client = ServiceClient(session, "http://service:8765", TOKEN)
                assert service.snapshot()["charging_display"] == service.data.charging_display
                reads = list(transport.reads)
                for state, reason in [("checking_status", "checking_status"),
                                      ("state_uncertain", "inconsistent_readings"),
                                      ("data_missing", "missing_readings")]:
                    service.data.charging_display = state
                    payload = service.snapshot()
                    for _ in range(3):
                        assert client.apply_snapshot(payload).charging_display == state
                        overview = overview_snapshot(payload)
                        assert (overview["state"], overview["reason"]) == (state, reason)
                payload["available"] = False
                assert overview_snapshot(payload)["state"] == "connection_lost"
                assert transport.reads == reads
                assert not transport.writes
                for value in ("secret value", True, [], {}, 123):
                    payload["charging_display"] = value
                    with pytest.raises(ServiceConnectionError, match=r"snapshot \(charging_display\)"):
                        client.apply_snapshot(payload)
        finally:
            await service.stop()
    asyncio.run(run())


@pytest.mark.parametrize("language", ["de", "en", "fr", "nl", "it"])
@pytest.mark.parametrize(("state", "color"), [
    ("unknown", "grey"), ("unavailable", "red"), ("checking_status", "grey"),
    ("state_uncertain", "amber"), ("data_missing", "amber"),
])
def test_mushroom_status_templates_render_without_unknown_measurements(language, state, color):
    from pathlib import Path

    import yaml
    from jinja2 import Environment, StrictUndefined

    card = yaml.safe_load(Path(f"examples/dashboard/app.{language}.yaml").read_text())["cards"][0]
    entity = card["entity"]
    values = {entity: state}
    environment = Environment(undefined=StrictUndefined, autoescape=True)
    environment.globals.update(
        states=lambda key, **_: values.get(key, "unknown"),
        has_value=lambda key: values.get(key, "unknown") not in ("unknown", "unavailable"),
        is_state=lambda key, expected: values.get(key, "unknown") == expected,
        state_translated=lambda _: "translated status",
    )
    rendered = {key: environment.from_string(card[key]).render(entity=entity).strip()
                for key in ("primary", "secondary", "color")}
    assert rendered["primary"] == "AMTRON · —"
    assert "unknown" not in rendered["secondary"]
    assert "unavailable" not in rendered["secondary"]
    assert rendered["color"] == color


@pytest.mark.parametrize("state", [None, 99, EvseState.EV_CONNECTED])
def test_fresh_fault_is_immediate_even_when_evse_read_failed(state):
    display = ChargingDisplay()
    data = _data(evse_state=state, error_code=200)
    data.held = frozenset({"evse_state"})
    assert display.observe(data, cp_supported=True, now=0) == "wallbox_error"
    # A held error is not a newly confirmed fault, either.
    data.held = frozenset({"evse_state", "error_code"})
    assert display.observe(data, cp_supported=True, now=1) == "data_missing"
