"""Annual Module 3 refresh applies a checked tariff without touching Modbus."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from custom_components.mennekes_amtron_rtu.charging_cost_const import (
    CONF_AUTO_TARIFF_REFRESH,
    CONF_CHARGING_NETWORK_TARIFF,
)
from custom_components.mennekes_amtron_rtu.network_tariff_year import (
    async_refresh_tariff_year,
    needs_year_refresh,
)
from tests.test_charging_cost_options import NETWORK


def _entry():
    year = datetime.now(ZoneInfo("Europe/Berlin")).year
    return SimpleNamespace(options={
        CONF_AUTO_TARIFF_REFRESH: True,
        CONF_CHARGING_NETWORK_TARIFF: {
            "module": "module_3", "tariff_year": year - 1,
            "price_includes_network": True,
        },
        "charging_price": 0.3,
    })


def test_refresh_is_due_only_for_expired_opted_in_module_three():
    entry = _entry()
    assert needs_year_refresh(entry.options)
    entry.options[CONF_AUTO_TARIFF_REFRESH] = False
    assert not needs_year_refresh(entry.options)
    entry.options[CONF_AUTO_TARIFF_REFRESH] = True
    entry.options[CONF_CHARGING_NETWORK_TARIFF]["tariff_year"] += 2
    assert not needs_year_refresh(entry.options)
    entry.options[CONF_CHARGING_NETWORK_TARIFF]["tariff_year"] -= 2
    entry.options[CONF_CHARGING_NETWORK_TARIFF]["module"] = "module_2"
    assert not needs_year_refresh(entry.options)
    entry.options[CONF_CHARGING_NETWORK_TARIFF]["module"] = "module_3"
    entry.options[CONF_CHARGING_NETWORK_TARIFF]["tariff_year"] = "bad"
    assert not needs_year_refresh(entry.options)


def test_new_year_updates_only_tariff_options(monkeypatch):
    async def lookup(_hass):
        return {"_source_status": "vorlaeufig", "_source_url": "https://example.org/2027.pdf", **NETWORK,
                "tariff_year": datetime.now(ZoneInfo("Europe/Berlin")).year}

    monkeypatch.setattr(
        "custom_components.mennekes_amtron_rtu.network_tariff_year.async_lookup_home_tariff",
        lookup,
    )
    entry = _entry()
    updates = []

    def update(_entry, *, options):
        updates.append(options)
        entry.options = options

    hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update))
    assert asyncio.run(async_refresh_tariff_year(hass, entry))
    assert len(updates) == 1
    assert updates[0]["charging_price"] == 0.3
    assert updates[0][CONF_CHARGING_NETWORK_TARIFF]["tariff_year"] == datetime.now(ZoneInfo("Europe/Berlin")).year
    assert not asyncio.run(async_refresh_tariff_year(hass, entry))


def test_missing_new_year_data_keeps_old_options(monkeypatch):
    async def lookup(_hass):
        return None

    monkeypatch.setattr(
        "custom_components.mennekes_amtron_rtu.network_tariff_year.async_lookup_home_tariff",
        lookup,
    )
    entry = _entry()
    hass = SimpleNamespace(config_entries=SimpleNamespace(
        async_update_entry=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected update")),
    ))
    assert not asyncio.run(async_refresh_tariff_year(hass, entry))


def test_invalid_or_wrong_year_import_keeps_old_tariff(monkeypatch):
    entry = _entry()
    updates = []
    hass = SimpleNamespace(config_entries=SimpleNamespace(
        async_update_entry=lambda *_args, **kwargs: updates.append(kwargs),
    ))

    async def invalid(_hass):
        return {**NETWORK, "high_periods": "invalid"}

    monkeypatch.setattr(
        "custom_components.mennekes_amtron_rtu.network_tariff_year.async_lookup_home_tariff",
        invalid,
    )
    assert not asyncio.run(async_refresh_tariff_year(hass, entry))

    async def wrong_year(_hass):
        return {**NETWORK, "tariff_year": datetime.now(ZoneInfo("Europe/Berlin")).year - 1}

    monkeypatch.setattr(
        "custom_components.mennekes_amtron_rtu.network_tariff_year.async_lookup_home_tariff",
        wrong_year,
    )
    assert not asyncio.run(async_refresh_tariff_year(hass, entry))
    assert updates == []


def test_final_new_year_sheet_is_applied_without_provisional_marker(monkeypatch):
    async def lookup(_hass):
        return {"_source_status": "endgueltig", **NETWORK,
                "tariff_year": datetime.now(ZoneInfo("Europe/Berlin")).year}

    monkeypatch.setattr(
        "custom_components.mennekes_amtron_rtu.network_tariff_year.async_lookup_home_tariff",
        lookup,
    )
    entry = _entry()
    hass = SimpleNamespace(config_entries=SimpleNamespace(
        async_update_entry=lambda _entry, *, options: setattr(entry, "options", options),
    ))
    assert asyncio.run(async_refresh_tariff_year(hass, entry))


def test_concurrent_options_edit_is_not_overwritten(monkeypatch):
    entry = _entry()

    async def lookup(_hass):
        entry.options = {**entry.options, "charging_price": 0.4}
        return {**NETWORK, "tariff_year": datetime.now(ZoneInfo("Europe/Berlin")).year}

    monkeypatch.setattr(
        "custom_components.mennekes_amtron_rtu.network_tariff_year.async_lookup_home_tariff",
        lookup,
    )
    hass = SimpleNamespace(config_entries=SimpleNamespace(
        async_update_entry=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected update")),
    ))
    assert not asyncio.run(async_refresh_tariff_year(hass, entry))
    assert entry.options["charging_price"] == 0.4
