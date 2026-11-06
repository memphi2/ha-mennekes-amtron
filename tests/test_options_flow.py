from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import probatio
import pytest

from custom_components.mennekes_amtron_rtu._flow_serial import PortOption
from custom_components.mennekes_amtron_rtu.config_schemas import (
    options_schema,
    user_schema,
)
from custom_components.mennekes_amtron_rtu.device.const import (
    CONF_CONTROL_MODE,
    CONF_CURRENT_LIMIT,
    CONF_MEASUREMENT_INTERVAL_SECONDS,
    CONF_SCAN_INTERVAL_SECONDS,
    CONF_STATE_CHANGE_INTERVAL_SECONDS,
    CONTROL_MODE_MASTER,
    CONTROL_MODE_READ_ONLY,
)
from custom_components.mennekes_amtron_rtu.options_flow import (
    MennekesAmtronOptionsFlow,
    _max_current,
    control_mode_is_master,
)
from tests.ha_fakes import FakeConfigEntry


class Flow(MennekesAmtronOptionsFlow):
    def __init__(self, entry: FakeConfigEntry) -> None:
        self._entry = entry

    @property
    def config_entry(self) -> Any:
        return self._entry

    def async_show_form(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": "create_entry", **kwargs}


def test_the_control_mode_decides_whether_writes_are_allowed() -> None:
    assert control_mode_is_master({CONF_CONTROL_MODE: CONTROL_MODE_MASTER})
    assert not control_mode_is_master({CONF_CONTROL_MODE: CONTROL_MODE_READ_ONLY})
    assert not control_mode_is_master({})


def test_the_maximum_comes_from_the_device_when_it_is_loaded() -> None:
    entry = FakeConfigEntry()
    assert _max_current(entry) == 16.0
    entry.runtime_data = SimpleNamespace(
        identity=SimpleNamespace(max_evse_current=32.0)
    )
    assert _max_current(entry) == 32.0
    entry.runtime_data = SimpleNamespace(identity=SimpleNamespace(max_evse_current=2))
    assert _max_current(entry) == 16.0


def test_the_form_defaults_to_the_stored_options() -> None:
    async def run() -> None:
        entry = FakeConfigEntry(
            options={
                CONF_CONTROL_MODE: CONTROL_MODE_MASTER,
                CONF_SCAN_INTERVAL_SECONDS: 10,
                CONF_CURRENT_LIMIT: 12.0,
                CONF_STATE_CHANGE_INTERVAL_SECONDS: 175,
            }
        )
        result = await Flow(entry).async_step_init()
        assert result["type"] == "form"
        defaults = {
            marker.schema: marker.default() for marker in result["data_schema"].schema
        }
        assert defaults[CONF_CONTROL_MODE] == CONTROL_MODE_MASTER
        assert defaults[CONF_SCAN_INTERVAL_SECONDS] == 10
        assert defaults[CONF_STATE_CHANGE_INTERVAL_SECONDS] == 175

    asyncio.run(run())


def test_submitting_normalises_the_values() -> None:
    async def run() -> None:
        result = await Flow(FakeConfigEntry()).async_step_init(
            {
                CONF_CONTROL_MODE: CONTROL_MODE_MASTER,
                CONF_SCAN_INTERVAL_SECONDS: "7",
                CONF_CURRENT_LIMIT: "11.5",
            }
        )
        assert result["data"] == {
            CONF_CONTROL_MODE: CONTROL_MODE_MASTER,
            CONF_SCAN_INTERVAL_SECONDS: 7,
            CONF_MEASUREMENT_INTERVAL_SECONDS: 30,
            CONF_CURRENT_LIMIT: 11.5,
            CONF_STATE_CHANGE_INTERVAL_SECONDS: 300,
        }

    asyncio.run(run())


@pytest.mark.parametrize("stale_mode", [None, CONTROL_MODE_READ_ONLY, CONTROL_MODE_MASTER])
def test_service_options_configure_only_current_cap_and_cache_refresh(stale_mode) -> None:
    async def run() -> None:
        entry = FakeConfigEntry(
            data={"server_url": "http://service:8765"},
            options={CONF_CONTROL_MODE: CONTROL_MODE_READ_ONLY},
        )
        flow = Flow(entry)
        result = await flow.async_step_init()
        assert result["step_id"] == "service"
        assert (await flow.async_step_service())["step_id"] == "service"
        fields = {
            marker.schema: selector
            for marker, selector in result["data_schema"].schema.items()
        }
        assert set(fields) == {
            CONF_CURRENT_LIMIT,
            CONF_SCAN_INTERVAL_SECONDS,
        }
        submitted = {
            CONF_CURRENT_LIMIT: 8,
            CONF_SCAN_INTERVAL_SECONDS: 10,
            CONF_MEASUREMENT_INTERVAL_SECONDS: 60,
            CONF_STATE_CHANGE_INTERVAL_SECONDS: 123,
        }
        if stale_mode is not None:
            submitted[CONF_CONTROL_MODE] = stale_mode
        result = await flow.async_step_service(submitted)
        assert result["data"] == {
            CONF_CURRENT_LIMIT: 8,
            CONF_SCAN_INTERVAL_SECONDS: 10,
        }

    asyncio.run(run())


@pytest.mark.parametrize("seconds", [0, 1, 175, 300, 3600])
def test_state_change_interval_is_saved_as_whole_seconds(seconds: int) -> None:
    async def run() -> None:
        form = await Flow(FakeConfigEntry()).async_step_init()
        values = form["data_schema"]({CONF_STATE_CHANGE_INTERVAL_SECONDS: float(seconds)})
        result = await Flow(FakeConfigEntry()).async_step_init(values)
        stored = result["data"][CONF_STATE_CHANGE_INTERVAL_SECONDS]
        assert stored == seconds
        assert type(stored) is int

    asyncio.run(run())


@pytest.mark.parametrize(
    "value", [-1, 3601, 1.5, True, None, "300", float("nan"), float("inf")]
)
def test_state_change_interval_rejects_invalid_ui_values(value: Any) -> None:
    schema = options_schema({}, max_current=16)
    with pytest.raises(probatio.Invalid):
        schema({CONF_STATE_CHANGE_INTERVAL_SECONDS: value})


def test_state_change_interval_default_is_300_seconds() -> None:
    values = options_schema({}, max_current=16)({})
    assert values[CONF_STATE_CHANGE_INTERVAL_SECONDS] == 300


BUS = {"port": "/dev/a", "device_id": 50, "baudrate": 57600, "frame": "8n2"}


def test_the_user_schema_bounds_the_device_address() -> None:
    schema = user_schema([PortOption(value="/dev/a", label="a")])
    assert schema(dict(BUS))["device_id"] == 50
    try:
        schema({**BUS, "device_id": 9})
    except probatio.Invalid:
        pass
    else:  # pragma: no cover - the schema must reject this
        raise AssertionError("device id 9 must be rejected")


def test_the_setup_form_asks_for_five_things() -> None:
    """The device documents three frames, so it is one field, not three."""

    fields = {str(marker.schema) for marker in user_schema([]).schema}
    assert fields == {"port", "device_id", "baudrate", "frame", "search"}
    assert "bytesize" not in fields
    assert "parity" not in fields
    assert "stopbits" not in fields


def test_the_user_schema_falls_back_to_free_text_without_ports() -> None:
    assert user_schema([])({**BUS, "port": "/dev/custom"})["port"] == "/dev/custom"


def test_the_options_schema_never_offers_less_than_six_ampere() -> None:
    schema = options_schema({}, max_current=4.0)
    marker = next(
        marker for marker in schema.schema if marker.schema == CONF_CURRENT_LIMIT
    )
    assert schema.schema[marker].config["max"] == 6
