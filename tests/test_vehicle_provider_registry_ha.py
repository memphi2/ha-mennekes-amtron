"""A real device registry offers BavarianData for a registered car."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from homeassistant import config_entries
from homeassistant.helpers import device_registry as dr

from custom_components.mennekes_amtron_rtu._options_profiles import ProfileOptionsFlow
from tests.test_home_assistant_smoke import _boot

VIN = "WBA21AW0X0FB12345"


def test_a_registered_car_offers_its_provider_on_a_real_registry(tmp_path):
    async def run():
        hass = await _boot(tmp_path / "config")
        try:
            flow = SimpleNamespace(hass=hass)
            assert ProfileOptionsFlow._profile_providers(flow) == ["none"]
            entry = config_entries.ConfigEntry(
                data={}, domain="bavariandata", minor_version=1, options={}, source="user",
                title="BMW", unique_id="account", version=1, discovery_keys={}, subentries_data=None,
            )
            await hass.config_entries.async_add(entry)
            registry = dr.async_get(hass)
            # The account's debug device alone is not a car.
            registry.async_get_or_create(
                config_entry_id=entry.entry_id, identifiers={("bavariandata", entry.entry_id)},
            )
            assert ProfileOptionsFlow._profile_providers(flow) == ["none"]
            registry.async_get_or_create(
                config_entry_id=entry.entry_id, identifiers={("bavariandata", VIN)}, name="i4",
            )
            assert ProfileOptionsFlow._profile_providers(flow) == ["none", "bavariandata"]
        finally:
            await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=60))
