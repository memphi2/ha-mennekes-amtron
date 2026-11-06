"""Cost estimation remains optional and tariff changes are saved atomically."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import probatio
import pytest

from custom_components.mennekes_amtron_rtu.charge_loss_const import (
    CONF_CHARGE_LOSS_SOURCE,
    CONF_VEHICLE_LAST_CHARGE_ENERGY,
    LOSS_SOURCE_SENSOR,
)
from custom_components.mennekes_amtron_rtu.charging_cost_const import (
    CONF_ACTIVE_QUARTERS,
    CONF_AUTO_TARIFF_REFRESH,
    CONF_CHARGING_COST_SOURCE,
    CONF_CHARGING_NETWORK_MODULE,
    CONF_CHARGING_NETWORK_TARIFF,
    CONF_CHARGING_PRICE,
    CONF_CHARGING_PRICE_ENTITY,
    CONF_HIGH_NETWORK,
    CONF_HIGH_PERIODS,
    CONF_LOW_NETWORK,
    CONF_LOW_PERIODS,
    CONF_PRICE_INCLUDES_NETWORK,
    CONF_REGULAR_NETWORK,
    CONF_TARIFF_YEAR,
    COST_SOURCE_DISABLED,
    COST_SOURCE_FIXED,
    COST_SOURCE_SENSOR,
    COST_SOURCES,
    NETWORK_MODULE_2,
    NETWORK_MODULE_3,
    NETWORK_MODULE_NONE,
)
from custom_components.mennekes_amtron_rtu.config_schemas import (
    cost_network_schema,
    cost_source_schema,
    options_schema,
)
from custom_components.mennekes_amtron_rtu.device.charging_cost import TariffConfig
from custom_components.mennekes_amtron_rtu.device.const import CONF_CURRENT_LIMIT
from tests.ha_fakes import FakeConfigEntry
from tests.test_options_flow import Flow

NETWORK = {
    CONF_REGULAR_NETWORK: 0.1,
    CONF_PRICE_INCLUDES_NETWORK: True,
    CONF_HIGH_NETWORK: 0.2,
    CONF_LOW_NETWORK: 0.01,
    CONF_HIGH_PERIODS: "16:00-20:00",
    CONF_LOW_PERIODS: "22:00-06:00",
    CONF_ACTIVE_QUARTERS: ["1", "4"],
    CONF_TARIFF_YEAR: 2026,
}


def _values(source=COST_SOURCE_FIXED, *, service=False):
    return options_schema({}, max_current=16, service_mode=service)(
        {CONF_CHARGING_COST_SOURCE: source, CONF_CURRENT_LIMIT: 12}
    )


def _flow(*, options=None, service=False, country="DE"):
    flow = Flow(FakeConfigEntry(
        options=options,
        data={"server_url": "http://service:8765"} if service else None,
    ))
    flow.hass = SimpleNamespace(config=SimpleNamespace(time_zone="Europe/Paris", country=country))
    return flow


def test_network_module_is_hidden_outside_germany():
    async def run():
        flow = _flow(country="FR")
        await flow.async_step_init(_values())
        form = await flow.async_step_cost_fixed()
        fields = {key.schema for key in form["data_schema"].schema}
        assert CONF_CHARGING_PRICE in fields
        assert CONF_CHARGING_NETWORK_MODULE not in fields
        rejected = await flow.async_step_cost_fixed({
            CONF_CHARGING_PRICE: 0.3,
            CONF_CHARGING_NETWORK_MODULE: NETWORK_MODULE_3,
        })
        assert rejected["errors"] == {CONF_CHARGING_NETWORK_MODULE: "network_tariff_invalid"}
        accepted = await flow.async_step_cost_fixed({CONF_CHARGING_PRICE: 0.3})
        assert accepted["type"] == "create_entry"

    asyncio.run(run())


def test_module_three_imports_home_tariff_once_and_shows_source(monkeypatch):
    calls = []

    async def lookup(hass):
        calls.append(hass)
        return {"_source_url": "https://example.org/price.pdf", **NETWORK}

    monkeypatch.setattr(
        "custom_components.mennekes_amtron_rtu.options_flow.async_lookup_home_tariff",
        lookup,
    )

    async def run():
        flow = _flow()
        await flow.async_step_init(_values())
        form = await flow.async_step_cost_fixed({
            CONF_CHARGING_PRICE: 0.3, CONF_CHARGING_NETWORK_MODULE: NETWORK_MODULE_3,
        })
        assert form["step_id"] == "cost_network"
        assert form["description_placeholders"]["source"] == "https://example.org/price.pdf"
        marker = next(key for key in form["data_schema"].schema if key.schema == CONF_HIGH_PERIODS)
        assert marker.description["suggested_value"] == NETWORK[CONF_HIGH_PERIODS]
        await flow.async_step_cost_fixed()
        await flow.async_step_cost_fixed({
            CONF_CHARGING_PRICE: 0.3, CONF_CHARGING_NETWORK_MODULE: NETWORK_MODULE_3,
        })
        assert len(calls) == 1
        saved = await flow.async_step_cost_network({**NETWORK, CONF_AUTO_TARIFF_REFRESH: True})
        assert saved["data"][CONF_AUTO_TARIFF_REFRESH] is True

    asyncio.run(run())


def test_old_module_three_tariff_is_refreshed_when_options_reopen(monkeypatch):
    async def lookup(_hass):
        return {"_source_url": "https://example.org/new.pdf", **NETWORK}

    monkeypatch.setattr(
        "custom_components.mennekes_amtron_rtu.options_flow.async_lookup_home_tariff",
        lookup,
    )

    async def run():
        old = {"module": NETWORK_MODULE_3, CONF_TARIFF_YEAR: 2025}
        flow = _flow(options={
            CONF_CHARGING_COST_SOURCE: COST_SOURCE_FIXED,
            CONF_CHARGING_PRICE: 0.3,
            CONF_CHARGING_NETWORK_TARIFF: old,
        })
        await flow.async_step_init(_values())
        result = await flow.async_step_cost_fixed({
            CONF_CHARGING_PRICE: 0.3, CONF_CHARGING_NETWORK_MODULE: NETWORK_MODULE_3,
        })
        assert result["description_placeholders"]["source"] == "https://example.org/new.pdf"

    asyncio.run(run())


def test_provisional_import_is_clearly_marked_before_confirmation(monkeypatch):
    async def lookup(_hass):
        return {"_source_status": "vorlaeufig", "_source_url": "https://example.org/preliminary.pdf", **NETWORK}

    monkeypatch.setattr(
        "custom_components.mennekes_amtron_rtu.options_flow.async_lookup_home_tariff",
        lookup,
    )

    async def run():
        flow = _flow()
        await flow.async_step_init(_values())
        form = await flow.async_step_cost_fixed({
            CONF_CHARGING_PRICE: 0.3, CONF_CHARGING_NETWORK_MODULE: NETWORK_MODULE_3,
        })
        assert form["description_placeholders"]["source"].startswith("⚠ ")
        assert flow.config_entry.options == {}

    asyncio.run(run())


def test_annual_refresh_option_rejects_non_boolean_value():
    async def run():
        flow = _flow()
        await flow.async_step_init(_values())
        await flow.async_step_cost_fixed({
            CONF_CHARGING_PRICE: 0.3, CONF_CHARGING_NETWORK_MODULE: NETWORK_MODULE_3,
        })
        result = await flow.async_step_cost_network({**NETWORK, CONF_AUTO_TARIFF_REFRESH: "yes"})
        assert result["errors"] == {"base": "network_tariff_invalid"}

    asyncio.run(run())


@pytest.mark.parametrize("service", [False, True])
def test_costs_disabled_by_default_and_only_source_is_on_main_form(service):
    async def run():
        flow = _flow(service=service)
        form = await flow.async_step_init()
        fields = {key.schema: value for key, value in form["data_schema"].schema.items()}
        values = form["data_schema"]({})
        assert values[CONF_CHARGING_COST_SOURCE] == COST_SOURCE_DISABLED
        assert fields[CONF_CHARGING_COST_SOURCE].config["options"] == list(COST_SOURCES)
        assert not {CONF_CHARGING_PRICE, CONF_CHARGING_PRICE_ENTITY, CONF_CHARGING_NETWORK_MODULE} & fields.keys()
        result = await flow.async_step_init(values)
        assert CONF_CHARGING_COST_SOURCE not in result["data"]

    asyncio.run(run())


@pytest.mark.parametrize("service", [False, True])
@pytest.mark.parametrize("source", [COST_SOURCE_FIXED, COST_SOURCE_SENSOR])
def test_cost_source_saves_only_at_last_step_and_clears_unselected_old_details(service, source):
    async def run():
        previous = {
            CONF_CHARGING_COST_SOURCE: COST_SOURCE_SENSOR,
            CONF_CHARGING_PRICE: 0.3,
            CONF_CHARGING_PRICE_ENTITY: "sensor.old_price",
            CONF_CHARGING_NETWORK_TARIFF: {"module": NETWORK_MODULE_2},
        }
        flow = _flow(service=service, options=previous.copy())
        form = await flow.async_step_init(_values(source, service=service))
        assert form["step_id"] == f"cost_{source}"
        assert flow.config_entry.options == previous
        fields = {key.schema: key for key in form["data_schema"].schema}
        pricekey = CONF_CHARGING_PRICE if source == COST_SOURCE_FIXED else CONF_CHARGING_PRICE_ENTITY
        assert fields[pricekey].description["suggested_value"] == previous[pricekey]
        assert fields[CONF_CHARGING_NETWORK_MODULE].default() == NETWORK_MODULE_2
        step = flow.async_step_cost_fixed if source == COST_SOURCE_FIXED else flow.async_step_cost_sensor
        assert (await step())["step_id"] == f"cost_{source}"
        result = await step({pricekey: -0.05 if source == COST_SOURCE_FIXED else "sensor.price"})
        assert result["type"] == "create_entry"
        assert result["data"][CONF_CHARGING_COST_SOURCE] == source
        assert result["data"][CONF_CHARGING_NETWORK_TARIFF] == {"module": NETWORK_MODULE_NONE}
        assert result["data"][CONF_CURRENT_LIMIT] == 12
        stale = CONF_CHARGING_PRICE_ENTITY if source == COST_SOURCE_FIXED else CONF_CHARGING_PRICE
        assert stale not in result["data"]
        assert CONF_CHARGING_NETWORK_MODULE not in result["data"]
        assert flow.config_entry.options == previous
        assert (await step({pricekey: 1}))["step_id"] == ("service" if service else "init")

    asyncio.run(run())


@pytest.mark.parametrize("source", [COST_SOURCE_FIXED, COST_SOURCE_SENSOR])
def test_cost_and_loss_steps_save_the_whole_draft_together(source):
    async def run():
        previous = {CONF_CURRENT_LIMIT: 10}
        flow = _flow(options=previous.copy())
        form = await flow.async_step_init({**_values(source), CONF_CHARGE_LOSS_SOURCE: LOSS_SOURCE_SENSOR})
        assert form["step_id"] == "loss_sensor"
        cost_form = await flow.async_step_loss_sensor({CONF_VEHICLE_LAST_CHARGE_ENERGY: "sensor.car_energy"})
        assert cost_form["step_id"] == f"cost_{source}"
        assert flow.config_entry.options == previous
        pricekey = CONF_CHARGING_PRICE if source == COST_SOURCE_FIXED else CONF_CHARGING_PRICE_ENTITY
        step = flow.async_step_cost_fixed if source == COST_SOURCE_FIXED else flow.async_step_cost_sensor
        result = await step({pricekey: 0.32 if source == COST_SOURCE_FIXED else "sensor.tariff"})
        assert result["data"][CONF_CHARGE_LOSS_SOURCE] == LOSS_SOURCE_SENSOR
        assert result["data"][CONF_VEHICLE_LAST_CHARGE_ENERGY] == "sensor.car_energy"
        assert result["data"][CONF_CHARGING_COST_SOURCE] == source

    asyncio.run(run())


def test_disabling_cost_estimation_removes_the_old_price_and_network_configuration():
    async def run():
        flow = _flow(options={
            CONF_CHARGING_COST_SOURCE: COST_SOURCE_FIXED,
            CONF_CHARGING_PRICE: 0.4,
            CONF_CHARGING_NETWORK_TARIFF: {"module": NETWORK_MODULE_2},
        })
        result = await flow.async_step_init(_values(COST_SOURCE_DISABLED))
        assert not {
            CONF_CHARGING_COST_SOURCE, CONF_CHARGING_PRICE, CONF_CHARGING_NETWORK_TARIFF
        } & result["data"].keys()

    asyncio.run(run())


@pytest.mark.parametrize("source", ["unsupported", None, False, ["fixed"]])
def test_invalid_main_cost_source_is_rejected(source):
    async def run():
        form = await _flow().async_step_init({**_values(), CONF_CHARGING_COST_SOURCE: source})
        assert form["errors"] == {CONF_CHARGING_COST_SOURCE: "cost_source_invalid"}

    asyncio.run(run())


@pytest.mark.parametrize("price", [None, True, "0.3", 10**500, float("inf"), float("nan"), 1001, -1001])
def test_fixed_price_rejects_nonfinite_or_malformed_values_then_allows_correction(price):
    async def run():
        flow = _flow()
        await flow.async_step_init(_values())
        failed = await flow.async_step_cost_fixed({CONF_CHARGING_PRICE: price})
        assert failed["errors"] == {CONF_CHARGING_PRICE: "cost_price_invalid"}
        saved = await flow.async_step_cost_fixed({CONF_CHARGING_PRICE: 0})
        assert saved["data"][CONF_CHARGING_PRICE] == 0

    asyncio.run(run())


@pytest.mark.parametrize("entity", [None, False, "bad", "number.price"])
def test_price_sensor_requires_sensor_entity(entity):
    async def run():
        flow = _flow()
        await flow.async_step_init(_values(COST_SOURCE_SENSOR))
        failed = await flow.async_step_cost_sensor({CONF_CHARGING_PRICE_ENTITY: entity})
        assert failed["errors"] == {CONF_CHARGING_PRICE_ENTITY: "cost_source_invalid"}

    asyncio.run(run())


def test_network_module_must_be_known():
    async def run():
        flow = _flow()
        await flow.async_step_init(_values())
        failed = await flow.async_step_cost_fixed({CONF_CHARGING_PRICE: 0.3, CONF_CHARGING_NETWORK_MODULE: "module_4"})
        assert failed["errors"] == {CONF_CHARGING_NETWORK_MODULE: "network_tariff_invalid"}

    asyncio.run(run())


@pytest.mark.parametrize("module", [NETWORK_MODULE_2, NETWORK_MODULE_3])
def test_network_form_persists_validated_tariff_only_after_every_step(module):
    async def run():
        flow = _flow()
        await flow.async_step_init(_values())
        network = await flow.async_step_cost_fixed({CONF_CHARGING_PRICE: 0.3, CONF_CHARGING_NETWORK_MODULE: module})
        assert network["step_id"] == "cost_network"
        assert network["description_placeholders"]["module"] == module[-1]
        assert not flow.config_entry.options
        details = dict(NETWORK) if module == NETWORK_MODULE_3 else {
            CONF_REGULAR_NETWORK: 0.1, CONF_PRICE_INCLUDES_NETWORK: False,
        }
        validated = network["data_schema"](details)
        saved = await flow.async_step_cost_network(validated)
        tariff = TariffConfig.parse(saved["data"][CONF_CHARGING_NETWORK_TARIFF])
        assert tariff.module == module
        assert tariff.regular_network == 0.1
        if module == NETWORK_MODULE_3:
            assert tariff.timezone == "Europe/Paris"
            assert tariff.windows[0].as_dict() == {"start": "16:00", "end": "20:00", "rate": "ht"}
            assert tariff.windows[1].contains(60)
            assert tariff.active_quarters == (1, 4)
            assert tariff.tariff_year == 2026
        else:
            assert tariff.price_includes_network is False
        assert (await flow.async_step_cost_network(details))["step_id"] == "init"

    asyncio.run(run())


def test_editing_an_existing_network_tariff_populates_all_published_values():
    async def run():
        tariff = {
            "module": NETWORK_MODULE_3,
            "regular_network": 0.1, "price_includes_network": True,
            "high_network": 0.2, "low_network": 0.01,
            "active_quarters": [1, 4], "tariff_year": 2026, "timezone": "Europe/Berlin",
            "windows": [
                {"start": "16:00", "end": "20:00", "rate": "ht"},
                {"start": "22:00", "end": "06:00", "rate": "nt"},
            ],
        }
        flow = _flow(options={CONF_CHARGING_NETWORK_TARIFF: tariff})
        await flow.async_step_init(_values())
        network = await flow.async_step_cost_fixed({CONF_CHARGING_PRICE: 0.3, CONF_CHARGING_NETWORK_MODULE: NETWORK_MODULE_3})
        markers = {marker.schema: marker for marker in network["data_schema"].schema}
        assert markers[CONF_REGULAR_NETWORK].description["suggested_value"] == 0.1
        assert markers[CONF_ACTIVE_QUARTERS].description["suggested_value"] == ["1", "4"]
        assert markers[CONF_HIGH_PERIODS].description["suggested_value"] == "16:00-20:00"
        assert markers[CONF_LOW_PERIODS].description["suggested_value"] == "22:00-06:00"
        assert (await flow.async_step_cost_network(dict(NETWORK)))["type"] == "create_entry"

    asyncio.run(run())


def test_module_three_accepts_multiple_quarter_hour_periods_and_preserves_them():
    async def run():
        flow = _flow()
        await flow.async_step_init(_values())
        await flow.async_step_cost_fixed({
            CONF_CHARGING_PRICE: 0.3, CONF_CHARGING_NETWORK_MODULE: NETWORK_MODULE_3,
        })
        periods = {
            **NETWORK,
            CONF_HIGH_PERIODS: "06:00-09:00\n17:00-20:00",
            CONF_LOW_PERIODS: "22:00-06:00\n12:00-13:30",
        }
        result = await flow.async_step_cost_network(periods)
        assert result["type"] == "create_entry"
        tariff = TariffConfig.parse(result["data"][CONF_CHARGING_NETWORK_TARIFF])
        assert len(tariff.windows) == 4
        assert tariff.windows[0].as_dict() == {"start": "06:00", "end": "09:00", "rate": "ht"}
        assert tariff.windows[-1].as_dict() == {"start": "12:00", "end": "13:30", "rate": "nt"}

    asyncio.run(run())


@pytest.mark.parametrize("replacement", [NETWORK_MODULE_2, NETWORK_MODULE_NONE])
def test_back_from_module_three_can_select_another_network_module(replacement):
    async def run():
        flow = _flow()
        await flow.async_step_init(_values())
        network = await flow.async_step_cost_fixed({
            CONF_CHARGING_PRICE: 0.3, CONF_CHARGING_NETWORK_MODULE: NETWORK_MODULE_3,
        })
        assert network["step_id"] == "cost_network"
        back = await flow.async_step_cost_fixed()
        assert back["step_id"] == "cost_fixed"
        marker = next(key for key in back["data_schema"].schema if key.schema == CONF_CHARGING_NETWORK_MODULE)
        assert marker.default() == NETWORK_MODULE_3
        result = await flow.async_step_cost_fixed({
            CONF_CHARGING_PRICE: 0.3, CONF_CHARGING_NETWORK_MODULE: replacement,
        })
        if replacement == NETWORK_MODULE_2:
            assert result["step_id"] == "cost_network"
            result = await flow.async_step_cost_network({
                CONF_REGULAR_NETWORK: 0.1, CONF_PRICE_INCLUDES_NETWORK: True,
            })
        assert result["type"] == "create_entry"
        assert result["data"][CONF_CHARGING_NETWORK_TARIFF]["module"] == replacement

    asyncio.run(run())


@pytest.mark.parametrize("previous", [None, "module_3", {"windows": None}, {"windows": [{}]}])
def test_old_or_partial_tariff_cannot_crash_opening_module_three(previous):
    async def run():
        flow = _flow(options={CONF_CHARGING_NETWORK_TARIFF: previous})
        await flow.async_step_init(_values())
        result = await flow.async_step_cost_fixed({
            CONF_CHARGING_PRICE: 0.3, CONF_CHARGING_NETWORK_MODULE: NETWORK_MODULE_3,
        })
        assert result["step_id"] == "cost_network"

    asyncio.run(run())


@pytest.mark.parametrize("changes", [
    {CONF_HIGH_PERIODS: "16:00:01-20:00"},
    {CONF_HIGH_PERIODS: None},
    {CONF_HIGH_PERIODS: "25:00-20:00"},
    {CONF_HIGH_PERIODS: "16:10-20:00"},
    {CONF_HIGH_PERIODS: "16:00-20:00\n17:00-18:00"},
    {CONF_HIGH_PERIODS: "16:00-20:00\n" * 9},
    {CONF_LOW_PERIODS: "19:00-21:00"},
    {CONF_ACTIVE_QUARTERS: "1,4"},
    {CONF_ACTIVE_QUARTERS: [1, 4]},
    {CONF_ACTIVE_QUARTERS: ["1"]},
    {CONF_ACTIVE_QUARTERS: ["1", "1"]},
    {CONF_TARIFF_YEAR: 2026.5},
    {CONF_PRICE_INCLUDES_NETWORK: "true"},
    {CONF_REGULAR_NETWORK: 0},
    {CONF_LOW_NETWORK: -0.1},
])
def test_invalid_tariffs_stay_in_form_without_committing_partial_options(changes):
    async def run():
        flow = _flow()
        await flow.async_step_init(_values())
        await flow.async_step_cost_fixed({CONF_CHARGING_PRICE: 0.3, CONF_CHARGING_NETWORK_MODULE: NETWORK_MODULE_3})
        result = await flow.async_step_cost_network({**NETWORK, **changes})
        assert result["type"] == "form"
        assert result["errors"] == {"base": "network_tariff_invalid"}
        assert not flow.config_entry.options
        assert (await flow.async_step_cost_network(dict(NETWORK)))["type"] == "create_entry"

    asyncio.run(run())


def test_missing_network_price_is_recoverable():
    async def run():
        flow = _flow()
        await flow.async_step_init(_values())
        await flow.async_step_cost_fixed({CONF_CHARGING_PRICE: 0.3, CONF_CHARGING_NETWORK_MODULE: NETWORK_MODULE_2})
        assert (await flow.async_step_cost_network({}))["errors"] == {"base": "network_tariff_invalid"}

    asyncio.run(run())


@pytest.mark.parametrize("step_name", ["cost_fixed", "cost_sensor", "cost_network"])
def test_cost_steps_cannot_skip_required_prior_steps(step_name):
    async def run():
        flow = _flow()
        step = getattr(flow, f"async_step_{step_name}")
        assert (await step({}))["step_id"] == "init"
        await flow.async_step_init({**_values(), CONF_CHARGE_LOSS_SOURCE: LOSS_SOURCE_SENSOR})
        assert (await step({}))["step_id"] == "init"

    asyncio.run(run())


def test_cost_schema_has_no_invented_tariff_prices_or_windows():
    schema = cost_network_schema({}, module=NETWORK_MODULE_3)
    markers = {marker.schema: marker for marker in schema.schema}
    for key in (
        CONF_REGULAR_NETWORK, CONF_HIGH_NETWORK, CONF_LOW_NETWORK,
        CONF_HIGH_PERIODS, CONF_LOW_PERIODS, CONF_TARIFF_YEAR,
    ):
        assert not callable(markers[key].default)
        assert markers[key].description["suggested_value"] is None
    assert markers[CONF_PRICE_INCLUDES_NETWORK].default() is True
    for key in (CONF_HIGH_PERIODS, CONF_LOW_PERIODS):
        assert schema.schema[markers[key]].selector_type == "text"
    assert schema.schema[markers[CONF_ACTIVE_QUARTERS]].config["multiple"] is True
    with pytest.raises(probatio.Invalid, match="required key"):
        schema({})
    price = cost_source_schema({}, source=COST_SOURCE_FIXED)
    assert price({CONF_CHARGING_PRICE: -0.25})[CONF_CHARGING_PRICE] == -0.25
    with pytest.raises(probatio.Invalid):
        price({CONF_CHARGING_PRICE: -1001})
    sensor = cost_source_schema({}, source=COST_SOURCE_SENSOR)
    assert next(iter(sensor.schema.values())).config["filter"] == [{"domain": ["sensor"]}]
