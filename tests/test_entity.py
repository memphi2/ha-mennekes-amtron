from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.const import CONF_SERIAL_NUMBER, DOMAIN
from custom_components.mennekes_amtron_rtu.entity import (
    MennekesAmtronEntity,
    entry_device_info,
    entry_device_key,
    entry_entity_unique_id,
)
from tests.ha_fakes import FakeConfigEntry, build_runtime


def test_the_serial_number_is_the_device_key() -> None:
    entry = FakeConfigEntry(data={CONF_SERIAL_NUMBER: "ABC123"})
    assert entry_device_key(entry) == "ABC123"
    assert entry_entity_unique_id(entry, "evse_state") == "ABC123_evse_state"


def test_without_a_serial_number_the_entry_id_is_used() -> None:
    entry = FakeConfigEntry(entry_id="xyz", data={})
    assert entry_device_key(entry) == "xyz"
    assert entry_entity_unique_id(entry, "charging") == "xyz_charging"


def test_device_info_carries_the_identity() -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        info = entry_device_info(entry)
        assert info["identifiers"] == {(DOMAIN, "ABC123456789")}
        assert info["manufacturer"] == "Mennekes"
        assert info["model"] == "Amtron 4You 310 11 C2"
        assert info["sw_version"] == "2023.21.11024"
        assert info["serial_number"] == "ABC123456789"
        assert info["model_id"] == "1313201205"

    asyncio.run(run())


@pytest.mark.parametrize("article", ["______1313201205", "____1313201205BK"])
def test_padding_is_removed_only_from_display_and_known_articles_are_mapped(article: str) -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        serial = "____00TEST_123"
        entry.data[CONF_SERIAL_NUMBER] = serial
        entry.runtime_data.identity.serial_number = serial
        entry.runtime_data.identity.article_number = article
        info = entry_device_info(entry)
        assert info["serial_number"] == "00TEST_123"
        assert info["model"] == "Amtron 4You 310 11 C2"
        assert info["model_id"] == article.lstrip("_")
        assert info["identifiers"] == {(DOMAIN, serial)}
        assert entry_entity_unique_id(entry, "evse_state") == f"{serial}_evse_state"
        assert entry.runtime_data.identity.serial_number == serial

    asyncio.run(run())


@pytest.mark.parametrize("article", ["__UNKNOWN_123", "1313201205OTHER", "____________", None])
def test_unknown_articles_are_not_mistaken_for_a_known_model(article: str | None) -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        entry.runtime_data.identity.article_number = article
        entry.runtime_data.identity.serial_number = "________________"
        info = entry_device_info(entry)
        assert info["model"] == "Amtron"
        assert "serial_number" not in info
        if article and article.lstrip("_"):
            assert info["model_id"] == article.lstrip("_")
        else:
            assert "model_id" not in info

    asyncio.run(run())


def test_device_info_falls_back_to_a_generic_model() -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        entry.runtime_data.identity.article_number = None
        entry.runtime_data.identity.firmware_version = None
        entry.runtime_data.identity.serial_number = None
        info = entry_device_info(entry)
        assert info["model"] == "Amtron"
        assert "sw_version" not in info
        assert "serial_number" not in info

    asyncio.run(run())


def test_an_entity_is_unavailable_without_its_register() -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        coordinator = entry.runtime_data.coordinator
        entity = MennekesAmtronEntity(entry, coordinator, "evse_state")
        assert entity.unique_id == "ABC123456789_evse_state"
        assert entity.available
        assert entity.register_value == 1
        assert entity.runtime_data is entry.runtime_data
        assert entity.wallbox_data.has("evse_state")

        missing = MennekesAmtronEntity(
            entry, coordinator, "nothing", register_key="temperature"
        )
        coordinator.data.values["temperature"] = None
        assert not missing.available

        coordinator.last_update_success = False
        assert not entity.available

    asyncio.run(run())
