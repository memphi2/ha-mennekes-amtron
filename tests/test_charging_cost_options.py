"""Cost estimation stays optional and section drafts save only on confirmation."""
from __future__ import annotations

import asyncio
from datetime import datetime
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
    COST_SOURCE_FIXED,
    COST_SOURCE_SENSOR,
    NETWORK_MODULE_2,
    NETWORK_MODULE_3,
    NETWORK_MODULE_NONE,
)
from custom_components.mennekes_amtron_rtu.config_schemas import (
    cost_network_schema,
    cost_source_schema,
    cost_windows_schema,
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
WINDOW_KEYS = {CONF_HIGH_PERIODS, CONF_LOW_PERIODS, CONF_ACTIVE_QUARTERS}


def prices(values=None):
    return {key: value for key, value in (NETWORK if values is None else values).items() if key not in WINDOW_KEYS}


def windows(values=None):
    return {key: value for key, value in (NETWORK if values is None else values).items() if key in WINDOW_KEYS}


def _flow(*, options=None, service=False, country="DE"):
    flow = Flow(FakeConfigEntry(
        options=options,
        data={"server_url": "http://service:8765"} if service else None,
    ))
    flow.hass = SimpleNamespace(config=SimpleNamespace(time_zone="Europe/Paris", country=country))
    return flow


async def _module_three(flow, *, imported=False):
    await flow.async_step_costs()
    source = await flow.async_step_cost_fixed({
        CONF_CHARGING_PRICE: 0.3, CONF_CHARGING_NETWORK_MODULE: NETWORK_MODULE_3,
    })
    assert source["step_id"] == "cost_module3_source"
    step = flow.async_step_cost_module3_import if imported else flow.async_step_cost_module3_manual
    return await step()


async def _save_three(flow, values=None):
    values = NETWORK if values is None else values
    price_form = await flow.async_step_cost_module3_prices()
    form = await flow.async_step_cost_module3_prices(price_form["data_schema"](prices(values)))
    assert form["step_id"] == "cost_module3_windows"
    review = await flow.async_step_cost_module3_windows(form["data_schema"](windows(values)))
    assert review["step_id"] == "review"
    return await flow.async_step_save()


def test_network_module_is_hidden_outside_germany():
    async def run():
        flow = _flow(country="FR")
        await flow.async_step_costs()
        form = await flow.async_step_cost_fixed()
        fields = {key.schema for key in form["data_schema"].schema}
        assert CONF_CHARGING_PRICE in fields
        assert CONF_CHARGING_NETWORK_MODULE not in fields
        rejected = await flow.async_step_cost_fixed({
            CONF_CHARGING_PRICE: 0.3, CONF_CHARGING_NETWORK_MODULE: NETWORK_MODULE_3,
        })
        assert rejected["errors"] == {CONF_CHARGING_NETWORK_MODULE: "network_tariff_invalid"}
        assert (await flow.async_step_cost_fixed({CONF_CHARGING_PRICE: 0.3}))["step_id"] == "review"
        assert not flow.config_entry.options
        assert (await flow.async_step_save())["type"] == "create_entry"
    asyncio.run(run())


def test_module_three_import_is_explicit_once_and_displays_source(monkeypatch):
    calls = []

    async def lookup(hass):
        calls.append(hass)
        return {"_source_url": "https://example.org/price.pdf", **NETWORK}

    monkeypatch.setattr("custom_components.mennekes_amtron_rtu._options_estimates.async_lookup_home_tariff", lookup)

    async def run():
        flow = _flow()
        form = await _module_three(flow)
        assert not calls  # Manual setup never sends the HA location anywhere.
        form = await flow.async_step_cost_module3_import()
        assert form["description_placeholders"]["source"] == "https://example.org/price.pdf"
        marker = next(key for key in form["data_schema"].schema if key.schema == CONF_HIGH_NETWORK)
        assert marker.description["suggested_value"] == NETWORK[CONF_HIGH_NETWORK]
        assert len(calls) == 1
        saved = await _save_three(flow, {**NETWORK, CONF_AUTO_TARIFF_REFRESH: True})
        assert saved["data"][CONF_AUTO_TARIFF_REFRESH] is True
        assert len(calls) == 1
    asyncio.run(run())


def test_old_tariff_can_be_explicitly_reimported_without_automatic_lookup(monkeypatch):
    lookup_calls = []

    async def lookup(_hass):
        lookup_calls.append(True)
        return {"_source_url": "https://example.org/new.pdf", **NETWORK}

    monkeypatch.setattr("custom_components.mennekes_amtron_rtu._options_estimates.async_lookup_home_tariff", lookup)

    async def run():
        previous = {CONF_CHARGING_NETWORK_TARIFF: {"module": NETWORK_MODULE_3, CONF_TARIFF_YEAR: 2025}}
        flow = _flow(options=previous.copy())
        await _module_three(flow)
        assert not lookup_calls
        result = await flow.async_step_cost_module3_import()
        assert result["description_placeholders"]["source"] == "https://example.org/new.pdf"
        assert flow.config_entry.options == previous
    asyncio.run(run())


@pytest.mark.parametrize("provisional", [False, True])
def test_import_failure_or_provisional_source_requires_confirmation(monkeypatch, provisional):
    async def lookup(_hass):
        return {"_source_status": "vorlaeufig", "_source_url": "https://example.org/preliminary.pdf", **NETWORK} if provisional else None

    monkeypatch.setattr("custom_components.mennekes_amtron_rtu._options_estimates.async_lookup_home_tariff", lookup)

    async def run():
        flow = _flow()
        form = await _module_three(flow, imported=True)
        if provisional:
            assert form["description_placeholders"]["source"].startswith("⚠ ")
        else:
            assert form["errors"] == {"base": "tariff_lookup_failed"}
        assert flow.config_entry.options == {}
        assert (await _save_three(flow))["type"] == "create_entry"
    asyncio.run(run())


@pytest.mark.parametrize("service", [False, True])
def test_costs_disabled_by_default_and_the_section_menu_exposes_explicit_choices(service):
    async def run():
        flow = _flow(service=service)
        root = await flow.async_step_init()
        assert root["type"] == "menu"
        assert "costs" in root["menu_options"]
        menu = await flow.async_step_costs()
        assert menu["menu_options"] == ["cost_fixed", "cost_sensor", "cost_disable", "back"]
        assert (await flow.async_step_back())["step_id"] == "init"
        assert flow.config_entry.options == {}
    asyncio.run(run())


@pytest.mark.parametrize("service", [False, True])
@pytest.mark.parametrize("source", [COST_SOURCE_FIXED, COST_SOURCE_SENSOR])
def test_source_saves_only_on_confirmation_and_keeps_unrelated_sections(service, source):
    async def run():
        previous = {
            CONF_CHARGING_COST_SOURCE: COST_SOURCE_SENSOR,
            CONF_CHARGING_PRICE: 0.3, CONF_CHARGING_PRICE_ENTITY: "sensor.old_price",
            CONF_CHARGING_NETWORK_TARIFF: {"module": NETWORK_MODULE_2},
            CONF_CURRENT_LIMIT: 12, CONF_CHARGE_LOSS_SOURCE: LOSS_SOURCE_SENSOR,
            CONF_VEHICLE_LAST_CHARGE_ENERGY: "sensor.car_energy",
        }
        flow = _flow(service=service, options=previous.copy())
        await flow.async_step_costs()
        step = flow.async_step_cost_fixed if source == COST_SOURCE_FIXED else flow.async_step_cost_sensor
        form = await step()
        markers = {key.schema: key for key in form["data_schema"].schema}
        key = CONF_CHARGING_PRICE if source == COST_SOURCE_FIXED else CONF_CHARGING_PRICE_ENTITY
        assert markers[key].description["suggested_value"] == previous[key]
        assert markers[CONF_CHARGING_NETWORK_MODULE].default() == NETWORK_MODULE_2
        review = await step({key: -0.05 if source == COST_SOURCE_FIXED else "sensor.price"})
        assert review["step_id"] == "review"
        assert flow.config_entry.options == previous
        result = await flow.async_step_save()
        assert result["data"][CONF_CHARGING_COST_SOURCE] == source
        assert result["data"][CONF_CHARGING_NETWORK_TARIFF] == {"module": NETWORK_MODULE_NONE}
        for unrelated in (CONF_CURRENT_LIMIT, CONF_CHARGE_LOSS_SOURCE, CONF_VEHICLE_LAST_CHARGE_ENERGY):
            assert result["data"][unrelated] == previous[unrelated]
        stale = CONF_CHARGING_PRICE_ENTITY if source == COST_SOURCE_FIXED else CONF_CHARGING_PRICE
        assert stale not in result["data"]
        assert CONF_CHARGING_NETWORK_MODULE not in result["data"]
        assert (await step({key: 1}))["step_id"] == "init"
    asyncio.run(run())


def test_disabling_costs_can_be_cancelled_and_never_removes_other_sections():
    async def run():
        previous = {CONF_CHARGING_COST_SOURCE: COST_SOURCE_FIXED, CONF_CHARGING_PRICE: 0.4,
                    CONF_CHARGING_NETWORK_TARIFF: {"module": NETWORK_MODULE_3},
                    CONF_AUTO_TARIFF_REFRESH: True, CONF_CURRENT_LIMIT: 12}
        flow = _flow(options=previous.copy())
        await flow.async_step_costs()
        assert (await flow.async_step_cost_disable())["step_id"] == "review"
        await flow.async_step_back()
        assert flow.config_entry.options == previous
        await flow.async_step_costs()
        await flow.async_step_cost_disable()
        assert (await flow.async_step_save())["data"] == {CONF_CURRENT_LIMIT: 12}
    asyncio.run(run())


@pytest.mark.parametrize("price", [None, True, "0.3", 10**500, float("inf"), float("nan"), 1001, -1001])
def test_fixed_price_rejects_malformed_values_then_allows_correction(price):
    async def run():
        flow = _flow()
        await flow.async_step_costs()
        failed = await flow.async_step_cost_fixed({CONF_CHARGING_PRICE: price})
        assert failed["errors"] == {CONF_CHARGING_PRICE: "cost_price_invalid"}
        await flow.async_step_cost_fixed({CONF_CHARGING_PRICE: 0})
        assert (await flow.async_step_save())["data"][CONF_CHARGING_PRICE] == 0
    asyncio.run(run())


@pytest.mark.parametrize("entity", [None, False, "bad", "number.price"])
def test_price_sensor_requires_sensor_entity(entity):
    async def run():
        flow = _flow()
        await flow.async_step_costs()
        failed = await flow.async_step_cost_sensor({CONF_CHARGING_PRICE_ENTITY: entity})
        assert failed["errors"] == {CONF_CHARGING_PRICE_ENTITY: "cost_source_invalid"}
    asyncio.run(run())


def test_network_module_must_be_known():
    async def run():
        flow = _flow()
        await flow.async_step_costs()
        failed = await flow.async_step_cost_fixed({CONF_CHARGING_PRICE: 0.3, CONF_CHARGING_NETWORK_MODULE: "module_4"})
        assert failed["errors"] == {CONF_CHARGING_NETWORK_MODULE: "network_tariff_invalid"}
    asyncio.run(run())


@pytest.mark.parametrize("module", [NETWORK_MODULE_2, NETWORK_MODULE_3])
def test_tariff_saves_only_after_all_prices_windows_and_confirmation(module):
    async def run():
        flow = _flow()
        if module == NETWORK_MODULE_3:
            await _module_three(flow)
            saved = await _save_three(flow)
        else:
            await flow.async_step_costs()
            form = await flow.async_step_cost_fixed({CONF_CHARGING_PRICE: 0.3, CONF_CHARGING_NETWORK_MODULE: module})
            assert form["step_id"] == "cost_module2"
            review = await flow.async_step_cost_module2({CONF_REGULAR_NETWORK: 0.1, CONF_PRICE_INCLUDES_NETWORK: False})
            assert review["step_id"] == "review"
            saved = await flow.async_step_save()
        assert not flow.config_entry.options
        tariff = TariffConfig.parse(saved["data"][CONF_CHARGING_NETWORK_TARIFF])
        assert tariff.module == module
        assert tariff.regular_network == 0.1
        if module == NETWORK_MODULE_3:
            assert tariff.timezone == "Europe/Berlin"
            assert tariff.windows[0].as_dict() == {"start": "16:00", "end": "20:00", "rate": "ht"}
            assert tariff.windows[1].contains(60)
            assert tariff.active_quarters == (1, 4)
            assert tariff.tariff_year == 2026
        else:
            assert tariff.price_includes_network is False
    asyncio.run(run())


def test_editing_existing_tariff_populates_both_price_and_window_forms():
    async def run():
        flow = _flow()
        await _module_three(flow)
        saved = await _save_three(flow)
        flow = _flow(options=saved["data"])
        form = await _module_three(flow)
        marker = next(key for key in form["data_schema"].schema if key.schema == CONF_REGULAR_NETWORK)
        assert marker.description["suggested_value"] == 0.1
        form = await flow.async_step_cost_module3_prices(prices())
        markers = {key.schema: key for key in form["data_schema"].schema}
        for key in WINDOW_KEYS:
            assert markers[key].description["suggested_value"] == NETWORK[key]
    asyncio.run(run())


def test_multiple_quarter_hour_periods_are_preserved():
    async def run():
        flow = _flow()
        await _module_three(flow)
        values = {**NETWORK, CONF_HIGH_PERIODS: "06:00-09:00\n17:00-20:00", CONF_LOW_PERIODS: "22:00-06:00\n12:00-13:30"}
        result = await _save_three(flow, values)
        tariff = TariffConfig.parse(result["data"][CONF_CHARGING_NETWORK_TARIFF])
        assert len(tariff.windows) == 4
        assert tariff.windows[0].as_dict() == {"start": "06:00", "end": "09:00", "rate": "ht"}
        assert tariff.windows[-1].as_dict() == {"start": "12:00", "end": "13:30", "rate": "nt"}
    asyncio.run(run())


@pytest.mark.parametrize("replacement", [NETWORK_MODULE_2, NETWORK_MODULE_NONE])
def test_back_from_module_three_discards_partial_changes_before_selecting_another_module(replacement):
    async def run():
        flow = _flow(options={CONF_CURRENT_LIMIT: 12})
        await _module_three(flow)
        await flow.async_step_cost_module3_prices(prices())
        assert (await flow.async_step_back())["step_id"] == "init"
        assert flow.config_entry.options == {CONF_CURRENT_LIMIT: 12}
        await flow.async_step_costs()
        review = await flow.async_step_cost_fixed({CONF_CHARGING_PRICE: 0.3, CONF_CHARGING_NETWORK_MODULE: replacement})
        if replacement == NETWORK_MODULE_2:
            assert review["step_id"] == "cost_module2"
            review = await flow.async_step_cost_module2({CONF_REGULAR_NETWORK: 0.1, CONF_PRICE_INCLUDES_NETWORK: True})
        assert review["step_id"] == "review"
        saved = await flow.async_step_save()
        assert saved["data"][CONF_CHARGING_NETWORK_TARIFF]["module"] == replacement
        assert CONF_AUTO_TARIFF_REFRESH not in saved["data"]
    asyncio.run(run())


@pytest.mark.parametrize("previous", [None, "module_3", {"windows": None}, {"windows": [{}]}])
def test_old_or_partial_tariff_cannot_crash_opening_module_three(previous):
    async def run():
        form = await _module_three(_flow(options={CONF_CHARGING_NETWORK_TARIFF: previous}))
        assert form["step_id"] == "cost_module3_prices"
    asyncio.run(run())


@pytest.mark.parametrize("changes", [
    {CONF_HIGH_PERIODS: "16:00:01-20:00"}, {CONF_HIGH_PERIODS: None},
    {CONF_HIGH_PERIODS: "25:00-20:00"}, {CONF_HIGH_PERIODS: "16:10-20:00"},
    {CONF_HIGH_PERIODS: "06:01-09:00"}, {CONF_HIGH_PERIODS: "16:00-20:01"},
    {CONF_LOW_PERIODS: "22:01-06:00"}, {CONF_LOW_PERIODS: "22:00-06:01"},
    {CONF_HIGH_PERIODS: "16:00-20:00\n17:00-18:00"}, {CONF_HIGH_PERIODS: "16:00-20:00\n" * 9},
    {CONF_LOW_PERIODS: "19:00-21:00"}, {CONF_ACTIVE_QUARTERS: "1,4"},
    {CONF_ACTIVE_QUARTERS: [1, 4]}, {CONF_ACTIVE_QUARTERS: ["1"]}, {CONF_ACTIVE_QUARTERS: ["1", "1"]},
    {CONF_TARIFF_YEAR: 2026.5}, {CONF_PRICE_INCLUDES_NETWORK: "true"},
    {CONF_REGULAR_NETWORK: 0}, {CONF_LOW_NETWORK: -0.1}, {CONF_AUTO_TARIFF_REFRESH: "yes"},
    {CONF_HIGH_NETWORK: None}, {CONF_HIGH_NETWORK: True}, {CONF_HIGH_NETWORK: "0.2"},
    {CONF_HIGH_NETWORK: float("nan")}, {CONF_HIGH_NETWORK: float("inf")}, {CONF_HIGH_NETWORK: 10**1000},
])
def test_invalid_tariffs_keep_draft_without_committing_and_allow_correction(changes):
    async def run():
        flow = _flow()
        await _module_three(flow)
        if set(changes) & WINDOW_KEYS:
            await flow.async_step_cost_module3_prices(prices())
            result = await flow.async_step_cost_module3_windows({**windows(), **changes})
        else:
            result = await flow.async_step_cost_module3_prices({**prices(), **changes})
        assert result["type"] == "form"
        assert result["errors"] == {"base": "network_tariff_invalid"}
        assert not flow.config_entry.options
        assert (await _save_three(flow))["type"] == "create_entry"
    asyncio.run(run())


def test_missing_network_price_is_recoverable():
    async def run():
        flow = _flow()
        await flow.async_step_costs()
        await flow.async_step_cost_fixed({CONF_CHARGING_PRICE: 0.3, CONF_CHARGING_NETWORK_MODULE: NETWORK_MODULE_2})
        assert (await flow.async_step_cost_module2({}))["errors"] == {"base": "network_tariff_invalid"}
    asyncio.run(run())


@pytest.mark.parametrize("step_name", ["cost_disable", "cost_fixed", "cost_sensor", "cost_module2", "cost_module3_source",
                                       "cost_module3_prices", "cost_module3_windows", "cost_module3_import", "cost_module3_manual"])
def test_cost_steps_cannot_skip_required_prior_steps(step_name):
    async def run():
        flow = _flow()
        assert (await getattr(flow, f"async_step_{step_name}")({}))["step_id"] == "init"
        await flow.async_step_losses()
        assert (await getattr(flow, f"async_step_{step_name}")({}))["step_id"] == "init"
    asyncio.run(run())


def test_cost_schema_has_no_invented_tariff_prices_or_windows():
    schema = cost_network_schema({}, module=NETWORK_MODULE_3)
    periods = cost_windows_schema({})
    markers = {marker.schema: marker for form in (schema, periods) for marker in form.schema}
    for key in (CONF_REGULAR_NETWORK, CONF_HIGH_NETWORK, CONF_LOW_NETWORK, CONF_HIGH_PERIODS, CONF_LOW_PERIODS, CONF_TARIFF_YEAR):
        assert not callable(markers[key].default)
        assert markers[key].description["suggested_value"] is None
    assert markers[CONF_PRICE_INCLUDES_NETWORK].default() is True
    for key in (CONF_HIGH_PERIODS, CONF_LOW_PERIODS):
        assert periods.schema[markers[key]].selector_type == "text"
    assert periods.schema[markers[CONF_ACTIVE_QUARTERS]].config["multiple"] is True
    for form in (schema, periods):
        with pytest.raises(probatio.Invalid, match="required key"):
            form({})
    price = cost_source_schema({}, source=COST_SOURCE_FIXED)
    assert price({CONF_CHARGING_PRICE: -0.25})[CONF_CHARGING_PRICE] == -0.25
    with pytest.raises(probatio.Invalid):
        price({CONF_CHARGING_PRICE: -1001})
    sensor = cost_source_schema({}, source=COST_SOURCE_SENSOR)
    assert next(iter(sensor.schema.values())).config["filter"] == [{"domain": ["sensor"]}]


@pytest.mark.parametrize("imported", [False, True], ids=["manual", "import"])
@pytest.mark.parametrize(("month", "offset"), [(1, "+01:00"), (7, "+02:00")])
def test_german_network_windows_keep_local_time_when_ha_uses_utc(monkeypatch, imported, month, offset):
    values = {**NETWORK, CONF_HIGH_PERIODS: "16:30-20:15", CONF_ACTIVE_QUARTERS: ["1", "2", "3", "4"]}

    async def lookup(_hass):
        return {"_source_url": "https://example.org/price.pdf", **values}

    monkeypatch.setattr("custom_components.mennekes_amtron_rtu._options_estimates.async_lookup_home_tariff", lookup)

    async def run():
        flow = _flow()
        flow.hass.config.time_zone = "UTC"
        form = await _module_three(flow, imported=imported)
        if imported:
            supplied = {marker.schema: marker.description["suggested_value"] for marker in form["data_schema"].schema
                        if marker.description and "suggested_value" in marker.description}
            supplied[CONF_PRICE_INCLUDES_NETWORK] = True
            supplied[CONF_AUTO_TARIFF_REFRESH] = True
            form = await flow.async_step_cost_module3_prices(form["data_schema"](supplied))
            supplied = {marker.schema: marker.description["suggested_value"] for marker in form["data_schema"].schema}
            await flow.async_step_cost_module3_windows(form["data_schema"](supplied))
            saved = await flow.async_step_save()
        else:
            saved = await _save_three(flow, values)
        tariff = TariffConfig.parse(saved["data"][CONF_CHARGING_NETWORK_TARIFF])
        assert tariff.timezone == "Europe/Berlin"
        for clock, expected in (("16:15", 0.3), ("16:45", 0.4), ("20:30", 0.3)):
            instant = datetime.fromisoformat(f"2026-{month:02}-10T{clock}:00{offset}")
            assert tariff.price_at(instant.timestamp(), 0.3) == pytest.approx(expected)
    asyncio.run(run())
