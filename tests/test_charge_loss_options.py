"""Optional charging-loss configuration is explicit and committed atomically."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import probatio
import pytest
from homeassistant.helpers import device_registry as dr

from custom_components.mennekes_amtron_rtu.charge_loss_const import (
    CONF_CHARGE_LOSS_SOURCE,
    CONF_LOSS_AUTO_MATCH,
    CONF_LOSS_BAVARIAN_DEVICE,
    CONF_VEHICLE_LAST_CHARGE_ENERGY,
    LOSS_SOURCE_BAVARIANDATA,
    LOSS_SOURCE_DISABLED,
    LOSS_SOURCE_SENSOR,
    LOSS_SOURCES,
)
from custom_components.mennekes_amtron_rtu.charging_cost_const import (
    CONF_CHARGING_COST_SOURCE,
)
from custom_components.mennekes_amtron_rtu.config_schemas import (
    loss_bavariandata_schema,
    loss_sensor_schema,
    options_schema,
)
from custom_components.mennekes_amtron_rtu.device.const import (
    CONF_CURRENT_LIMIT,
    CONF_SCAN_INTERVAL_SECONDS,
)
from tests.ha_fakes import FakeConfigEntry
from tests.test_bavarian_history import vehicle_registry
from tests.test_options_flow import Flow as BaseFlow


class Flow(BaseFlow):
    def __init__(self, entry):
        super().__init__(entry)
        self.hass = SimpleNamespace(data={dr.DATA_REGISTRY: vehicle_registry()})


def _values(*, source: str, service: bool = False) -> dict[str, Any]:
    return options_schema({}, max_current=16, service_mode=service)(
        {CONF_CHARGE_LOSS_SOURCE: source, CONF_CURRENT_LIMIT: 12}
    )


@pytest.mark.parametrize("service", [False, True])
def test_loss_source_is_disabled_by_default_and_has_no_details_on_main_form(service):
    schema = options_schema({}, max_current=16, service_mode=service)
    fields = {marker.schema: selector for marker, selector in schema.schema.items()}
    assert schema({})[CONF_CHARGE_LOSS_SOURCE] == LOSS_SOURCE_DISABLED
    assert fields[CONF_CHARGE_LOSS_SOURCE].config["options"] == list(LOSS_SOURCES)
    assert fields[CONF_CHARGE_LOSS_SOURCE].config["translation_key"] == "charge_loss_source"
    assert not {
        CONF_LOSS_BAVARIAN_DEVICE, CONF_VEHICLE_LAST_CHARGE_ENERGY, CONF_LOSS_AUTO_MATCH
    } & fields.keys()


@pytest.mark.parametrize("service", [False, True])
@pytest.mark.parametrize("source", [LOSS_SOURCE_BAVARIANDATA, LOSS_SOURCE_SENSOR])
def test_options_wait_for_source_details_and_preserve_general_settings(service, source):
    async def run():
        previous = {
            CONF_SCAN_INTERVAL_SECONDS: 40,
            CONF_CHARGE_LOSS_SOURCE: source,
            CONF_LOSS_BAVARIAN_DEVICE: "car-device",
            CONF_VEHICLE_LAST_CHARGE_ENERGY: "sensor.car_last_charge",
            CONF_LOSS_AUTO_MATCH: True,
        }
        entry = FakeConfigEntry(
            data={"server_url": "http://service:8765"} if service else None,
            options=previous.copy(),
        )
        flow = Flow(entry)
        main = await flow.async_step_init()
        assert main["data_schema"]({})[CONF_CHARGE_LOSS_SOURCE] == source
        pending = await flow.async_step_init(_values(source=source, service=service))
        assert pending["type"] == "form"
        assert pending["step_id"] == f"loss_{source}"
        assert entry.options == previous
        markers = {marker.schema: marker for marker in pending["data_schema"].schema}
        if source == LOSS_SOURCE_BAVARIANDATA:
            step = flow.async_step_loss_bavariandata
            details = {CONF_LOSS_BAVARIAN_DEVICE: "car-device"}
            assert set(markers) == {CONF_LOSS_BAVARIAN_DEVICE}
        else:
            step = flow.async_step_loss_sensor
            details = {CONF_VEHICLE_LAST_CHARGE_ENERGY: "sensor.car_last_charge"}
            assert pending["data_schema"](details)[CONF_LOSS_AUTO_MATCH] is True
        key, value = next(iter(details.items()))
        assert markers[key].description["suggested_value"] == value
        # Revisiting the source form does not save or discard the pending choices.
        assert (await step())["type"] == "form"
        result = await step(details)
        assert result["type"] == "create_entry"
        expected = {**_values(source=source, service=service), **details}
        expected.pop(CONF_CHARGING_COST_SOURCE)
        if source == LOSS_SOURCE_SENSOR:
            expected[CONF_LOSS_AUTO_MATCH] = False
        assert result["data"] == expected
        assert entry.options == previous
        # A completed step cannot accidentally create another partial entry.
        assert (await step(details))["step_id"] == ("service" if service else "init")

    asyncio.run(run())


@pytest.mark.parametrize("source", [LOSS_SOURCE_BAVARIANDATA, LOSS_SOURCE_SENSOR])
def test_disabling_loss_clears_old_source_fields_without_opening_extra_step(source):
    async def run():
        flow = Flow(FakeConfigEntry(options={
            CONF_CHARGE_LOSS_SOURCE: source,
            CONF_LOSS_BAVARIAN_DEVICE: "old-car",
            CONF_VEHICLE_LAST_CHARGE_ENERGY: "sensor.old_energy",
            CONF_LOSS_AUTO_MATCH: True,
        }))
        result = await flow.async_step_init(_values(source=LOSS_SOURCE_DISABLED))
        assert result["type"] == "create_entry"
        assert not {
            CONF_CHARGE_LOSS_SOURCE, CONF_LOSS_BAVARIAN_DEVICE,
            CONF_VEHICLE_LAST_CHARGE_ENERGY, CONF_LOSS_AUTO_MATCH,
        } & result["data"].keys()

    asyncio.run(run())


def test_sensor_automatic_matching_is_explicit_opt_in():
    async def run():
        flow = Flow(FakeConfigEntry())
        form = await flow.async_step_init(_values(source=LOSS_SOURCE_SENSOR))
        details = form["data_schema"]({CONF_VEHICLE_LAST_CHARGE_ENERGY: "sensor.last_charge"})
        assert details[CONF_LOSS_AUTO_MATCH] is False
        result = await flow.async_step_loss_sensor({**details, CONF_LOSS_AUTO_MATCH: True})
        assert result["data"][CONF_LOSS_AUTO_MATCH] is True

    asyncio.run(run())


@pytest.mark.parametrize("invalid", ["unknown", None, False, ["sensor"]])
def test_invalid_source_is_a_field_error_without_committing(invalid):
    async def run():
        flow = Flow(FakeConfigEntry())
        result = await flow.async_step_init({**_values(source=LOSS_SOURCE_SENSOR), CONF_CHARGE_LOSS_SOURCE: invalid})
        assert result["type"] == "form"
        assert result["errors"] == {CONF_CHARGE_LOSS_SOURCE: "loss_source_invalid"}

    asyncio.run(run())


@pytest.mark.parametrize("invalid", [None, "", " ", False, 1])
def test_bavarian_device_must_be_selected_before_saving(invalid):
    async def run():
        flow = Flow(FakeConfigEntry())
        await flow.async_step_init(_values(source=LOSS_SOURCE_BAVARIANDATA))
        failed = await flow.async_step_loss_bavariandata({CONF_LOSS_BAVARIAN_DEVICE: invalid})
        assert failed["type"] == "form"
        assert failed["errors"] == {CONF_LOSS_BAVARIAN_DEVICE: "loss_source_invalid"}
        saved = await flow.async_step_loss_bavariandata({CONF_LOSS_BAVARIAN_DEVICE: "offline-car"})
        assert saved["data"][CONF_CURRENT_LIMIT] == 12

    asyncio.run(run())


@pytest.mark.parametrize("invalid", [None, "", False, "invalid", "number.last_charge"])
def test_sensor_must_be_a_sensor_entity_and_can_be_corrected(invalid):
    async def run():
        flow = Flow(FakeConfigEntry())
        await flow.async_step_init(_values(source=LOSS_SOURCE_SENSOR))
        failed = await flow.async_step_loss_sensor({CONF_VEHICLE_LAST_CHARGE_ENERGY: invalid})
        assert failed["errors"] == {CONF_VEHICLE_LAST_CHARGE_ENERGY: "loss_source_invalid"}
        saved = await flow.async_step_loss_sensor({CONF_VEHICLE_LAST_CHARGE_ENERGY: "sensor.offline_car"})
        assert saved["data"][CONF_CURRENT_LIMIT] == 12

    asyncio.run(run())


def test_auto_assignment_rejects_non_boolean_instead_of_silently_enabling():
    async def run():
        flow = Flow(FakeConfigEntry())
        await flow.async_step_init(_values(source=LOSS_SOURCE_SENSOR))
        failed = await flow.async_step_loss_sensor({
            CONF_VEHICLE_LAST_CHARGE_ENERGY: "sensor.last_charge", CONF_LOSS_AUTO_MATCH: "false",
        })
        assert failed["errors"] == {CONF_LOSS_AUTO_MATCH: "loss_source_invalid"}

    asyncio.run(run())


@pytest.mark.parametrize("source", [LOSS_SOURCE_BAVARIANDATA, LOSS_SOURCE_SENSOR])
def test_source_steps_cannot_bypass_main_form_or_switch_pending_mode(source):
    async def run():
        flow = Flow(FakeConfigEntry())
        step = flow.async_step_loss_bavariandata if source == LOSS_SOURCE_BAVARIANDATA else flow.async_step_loss_sensor
        assert (await step({}))["step_id"] == "init"
        other = LOSS_SOURCE_SENSOR if source == LOSS_SOURCE_BAVARIANDATA else LOSS_SOURCE_BAVARIANDATA
        await flow.async_step_init(_values(source=other))
        assert (await step({}))["step_id"] == "init"

    asyncio.run(run())


def test_source_schemas_filter_devices_and_entities_and_require_a_source():
    bavarian = loss_bavariandata_schema({})
    selector = next(iter(bavarian.schema.values()))
    assert selector.config["integration"] == "bavariandata"
    assert bavarian({CONF_LOSS_BAVARIAN_DEVICE: "car-device"}) == {CONF_LOSS_BAVARIAN_DEVICE: "car-device"}
    sensor = loss_sensor_schema({})
    assert next(iter(sensor.schema.values())).config["filter"] == [{"domain": ["sensor"]}]
    with pytest.raises(probatio.Invalid, match="required key"):
        bavarian({})
    with pytest.raises(probatio.Invalid, match="required key"):
        sensor({})


def test_bavarian_account_device_is_rejected_before_saving():
    async def run():
        flow = Flow(FakeConfigEntry())
        await flow.async_step_init(_values(source=LOSS_SOURCE_BAVARIANDATA))
        flow.hass.data[dr.DATA_REGISTRY] = vehicle_registry(identifiers={("bavariandata", "account-entry")})
        result = await flow.async_step_loss_bavariandata({CONF_LOSS_BAVARIAN_DEVICE: "account-device"})
        assert result["type"] == "form"
        assert result["errors"] == {CONF_LOSS_BAVARIAN_DEVICE: "loss_source_invalid"}
        flow.hass.data[dr.DATA_REGISTRY] = vehicle_registry()
        result = await flow.async_step_loss_bavariandata({CONF_LOSS_BAVARIAN_DEVICE: "real-car"})
        assert result["type"] == "create_entry"
    asyncio.run(run())
