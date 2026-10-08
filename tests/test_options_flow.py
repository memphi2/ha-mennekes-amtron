from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import probatio

from custom_components.mennekes_amtron._flow_serial import PortOption
from custom_components.mennekes_amtron.config_schemas import (
    options_schema,
    user_schema,
)
from custom_components.mennekes_amtron.const import (
    CONF_CONTROL_MODE,
    CONF_CURRENT_LIMIT,
    CONF_SCAN_INTERVAL_SECONDS,
    CONTROL_MODE_MASTER,
    CONTROL_MODE_READ_ONLY,
)
from custom_components.mennekes_amtron.options_flow import (
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
            }
        )
        result = await Flow(entry).async_step_init()
        assert result["type"] == "form"
        defaults = {
            marker.schema: marker.default()
            for marker in result["data_schema"].schema
        }
        assert defaults[CONF_CONTROL_MODE] == CONTROL_MODE_MASTER
        assert defaults[CONF_SCAN_INTERVAL_SECONDS] == 10

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
            CONF_CURRENT_LIMIT: 11.5,
        }

    asyncio.run(run())


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
