from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import probatio
import pytest

from custom_components.mennekes_amtron_rtu._flow_serial import PortOption
from custom_components.mennekes_amtron_rtu.config_schemas import (
    advanced_schema,
    options_schema,
    user_schema,
)
from custom_components.mennekes_amtron_rtu.options_flow import (
    MennekesAmtronOptionsFlow,
    _max_current,
    control_mode_is_master,
)
from tests.ha_fakes import FakeConfigEntry


class Flow(MennekesAmtronOptionsFlow):
    def __init__(self, entry: FakeConfigEntry) -> None:
        super().__init__()
        self._entry = entry
        self.hass = SimpleNamespace(config=SimpleNamespace(country="DE"))

    @property
    def config_entry(self) -> Any:
        return self._entry

    def async_show_form(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": "form", **kwargs}

    def async_show_menu(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": "menu", **kwargs}

    def async_create_entry(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": "create_entry", **kwargs}


def fields(result):
    return {
        marker.schema: selector
        for marker, selector in result["data_schema"].schema.items()
    }


def test_control_mode_and_hardware_current():
    assert control_mode_is_master({"control_mode": "master"})
    assert not control_mode_is_master({"control_mode": "read_only"})
    assert not control_mode_is_master({})
    entry = FakeConfigEntry()
    assert _max_current(entry) == 16
    entry.runtime_data = SimpleNamespace(identity=SimpleNamespace(max_evse_current=32))
    assert _max_current(entry) == 32
    entry.runtime_data.identity.max_evse_current = 2
    assert _max_current(entry) == 16


def test_obsolete_review_and_save_routes_are_not_available():
    flow = Flow(FakeConfigEntry())
    for step in ("review", "edit", "save"):
        assert not hasattr(flow, f"async_step_{step}")


@pytest.mark.parametrize("service", [False, True])
def test_five_independent_sections_and_scoped_wallbox_save(service):
    async def run():
        entry = FakeConfigEntry(
            data={"server_url": "http://app:8765"} if service else None,
            options={
                "charging_price": 0.3,
                "timed_capacity_kwh": 80,
                "state_change_interval_seconds": 175,
            },
        )
        flow = Flow(entry)
        root = await flow.async_step_init()
        assert root["menu_options"] == [
            "wallbox",
            "vehicle",
            "departure",
            "costs",
            "losses",
        ]
        menu = await flow.async_step_wallbox()
        assert ("wallbox_advanced" in menu["menu_options"]) is not service
        form = await flow.async_step_wallbox_basic()
        assert set(fields(form)) == {"scan_interval_seconds", "current_limit"} | (
            set() if service else {"control_mode"}
        )
        values = form["data_schema"](
            {"scan_interval_seconds": 7, "current_limit": 11.5}
        )
        assert form["last_step"] is True
        result = await flow.async_step_wallbox_basic(values)
        assert result["type"] == "create_entry"
        assert entry.options["charging_price"] == 0.3
        assert result["data"]["scan_interval_seconds"] == 7
        assert result["data"]["state_change_interval_seconds"] == 175
        assert result["data"]["timed_capacity_kwh"] == 80
        assert ("control_mode" in result["data"]) is not service

    asyncio.run(run())


def test_wallbox_independent_basic_advanced_and_back_discard():
    async def run():
        entry = FakeConfigEntry()
        entry.runtime_data = SimpleNamespace(
            identity=SimpleNamespace(phase_options_hw=2)
        )
        flow = Flow(entry)
        await flow.async_step_wallbox()
        basic = await flow.async_step_wallbox_basic()
        saved = await flow.async_step_wallbox_basic(basic["data_schema"]({"current_limit": 10}))
        entry.options = saved["data"]
        await flow.async_step_wallbox()
        advanced = await flow.async_step_wallbox_advanced()
        assert advanced["last_step"] is True
        result = await flow.async_step_wallbox_advanced(
            advanced["data_schema"]({"state_change_interval_seconds": 175})
        )
        assert result["data"]["current_limit"] == 10
        assert result["data"]["state_change_interval_seconds"] == 175
        await flow.async_step_wallbox()
        await flow.async_step_wallbox_basic()
        assert (await flow.async_step_back())["step_id"] == "init"
        assert entry.options["current_limit"] == 10

    asyncio.run(run())


def test_live_unrelated_changes_merge_but_section_changes_conflict():
    async def run():
        entry = FakeConfigEntry(options={"current_limit": 16, "charging_price": 0.3})
        flow = Flow(entry)
        await flow.async_step_wallbox()
        form = await flow.async_step_wallbox_basic()
        entry.options["charging_price"] = 0.4
        assert (await flow.async_step_wallbox_basic(form["data_schema"]({"current_limit": 10})))["data"]["charging_price"] == 0.4
        await flow.async_step_wallbox()
        form = await flow.async_step_wallbox_basic()
        entry.options["current_limit"] = 12
        conflict = await flow.async_step_wallbox_basic(form["data_schema"]({"current_limit": 8}))
        assert conflict["step_id"] == "options_changed"
        assert (await flow.async_step_options_changed({}))["step_id"] == "init"
        assert entry.options["current_limit"] == 12

    asyncio.run(run())


@pytest.mark.parametrize(
    "step",
    [
        "wallbox_basic",
        "wallbox_advanced",
        "vehicle_disable",
        "vehicle_soc",
        "vehicle_limit",
        "departure_disable",
        "departure_configure",
        "departure_capacity_fixed",
        "departure_capacity_entity",
        "departure_calendar",
        "departure_entity",
        "departure_vehicle",
        "departure_vehicle_details",
    ],
)
def test_missing_section_cannot_skip_into_detail_or_save(step):
    async def run():
        flow = Flow(FakeConfigEntry())
        assert (await getattr(flow, f"async_step_{step}")())["step_id"] == "init"

    asyncio.run(run())


def test_service_cannot_open_direct_only_advanced_step():
    async def run():
        flow = Flow(FakeConfigEntry(data={"server_url": "http://app"}))
        await flow.async_step_wallbox()
        assert (await flow.async_step_wallbox_advanced())["step_id"] == "init"

    asyncio.run(run())


@pytest.mark.parametrize("limit", [False, True])
def test_vehicle_source_limit_is_conditional_and_disable_resets_departure(limit):
    async def run():
        entry = FakeConfigEntry(
            options={"timed_goal_enabled": True, "timed_capacity_kwh": 80}
        )
        flow = Flow(entry)
        await flow.async_step_vehicle()
        assert set(fields(await flow.async_step_vehicle_soc())) == {
            "vehicle_soc_entity",
            "use_vehicle_limit",
        }
        result = await flow.async_step_vehicle_soc(
            {"vehicle_soc_entity": "sensor.car", "use_vehicle_limit": limit}
        )
        if limit:
            assert result["step_id"] == "vehicle_limit"
            result = await flow.async_step_vehicle_limit(
                {"vehicle_charge_limit_entity": "number.car_limit"}
            )
        assert result["type"] == "create_entry"
        stored = result["data"]
        assert ("vehicle_charge_limit_entity" in stored) is limit
        entry.options = stored
        await flow.async_step_vehicle()
        disabled = (await flow.async_step_vehicle_disable())["data"]
        assert "vehicle_soc_entity" not in disabled
        assert disabled["timed_goal_enabled"] is False
        assert disabled["timed_goal_reset_pending"] is True
        assert disabled["timed_capacity_kwh"] == 80

    asyncio.run(run())


@pytest.mark.parametrize("value", [None, True, "switch.car", "wrong"])
def test_vehicle_validation_and_correction(value):
    async def run():
        flow = Flow(FakeConfigEntry())
        await flow.async_step_vehicle()
        assert (await flow.async_step_vehicle_soc({"vehicle_soc_entity": value}))[
            "errors"
        ]
        await flow.async_step_vehicle_soc(
            {"vehicle_soc_entity": "sensor.car", "use_vehicle_limit": True}
        )
        assert (
            await flow.async_step_vehicle_limit({"vehicle_charge_limit_entity": value})
        )["errors"]
        assert (
            await flow.async_step_vehicle_limit(
                {"vehicle_charge_limit_entity": "number.limit"}
            )
        )["type"] == "create_entry"

    asyncio.run(run())


@pytest.mark.parametrize("seconds", [0, 1, 175, 300, 3600])
def test_state_interval_whole_seconds(seconds):
    values = advanced_schema({}, phase_switch=True)(
        {"state_change_interval_seconds": float(seconds)}
    )
    assert values["state_change_interval_seconds"] == seconds
    assert type(values["state_change_interval_seconds"]) is int


@pytest.mark.parametrize(
    "value", [-1, 3601, 1.5, True, None, "300", float("nan"), float("inf")]
)
def test_state_interval_rejects_invalid(value):
    with pytest.raises(probatio.Invalid):
        advanced_schema({}, phase_switch=True)({"state_change_interval_seconds": value})


def test_basic_and_setup_schema_bounds():
    assert (
        advanced_schema({}, phase_switch=False)({})["state_change_interval_seconds"]
        == 300
    )
    assert "phase_switch_grace_seconds" not in advanced_schema({}, phase_switch=False)(
        {}
    )
    schema = options_schema({}, max_current=4)
    assert (
        next(
            value
            for key, value in schema.schema.items()
            if key.schema == "current_limit"
        ).config["max"]
        == 6
    )
    bus = {"port": "/dev/a", "device_id": 50, "baudrate": 57600, "frame": "8n2"}
    assert user_schema([PortOption(value="/dev/a", label="a")])(bus)["device_id"] == 50
    with pytest.raises(probatio.Invalid):
        user_schema([])({**bus, "device_id": 9})
    assert set(fields({"data_schema": user_schema([])})) == {
        "port",
        "device_id",
        "baudrate",
        "frame",
        "search",
    }
    assert user_schema([])({**bus, "port": "/dev/custom"})["port"] == "/dev/custom"
