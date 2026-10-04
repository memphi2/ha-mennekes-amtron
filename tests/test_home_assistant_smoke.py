"""Set the integration up inside a real Home Assistant.

Everything else in this suite replaces Home Assistant's runtime with fakes.
That is fast and precise, and it missed two defects that only a real instance
shows: the config entry title becomes the device name and is prefixed to
every entity id, and a form only pre-fills through ``suggested_value``, which
a fake flow handler never exercises.

So this test boots Home Assistant, drives the real config and options flows,
and talks to a real Modbus RTU server over a virtual serial link. It skips
itself when Home Assistant's bootstrap internals are not available in the
installed version rather than failing on them.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

import pytest

from custom_components.mennekes_amtron import registers as R
from custom_components.mennekes_amtron.const import (
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_CONTROL_MODE,
    CONF_DEVICE_ID,
    CONF_PARITY,
    CONF_PORT,
    CONF_SCAN_INTERVAL_SECONDS,
    CONF_STOPBITS,
    CONTROL_MODE_MASTER,
    DOMAIN,
)
from tests.fakes import device_bank
from tests.test_serial_loopback import (
    REGISTER_COUNT,
    _server_context,
    _signed,
    _virtual_serial_link,
)

REPO = Path(__file__).resolve().parents[1]
SERIAL_NUMBER = "ABC123456789"


async def _boot(config_dir: Path) -> Any:
    """Return a started Home Assistant that can see the integration."""

    from homeassistant import bootstrap, config_entries, core, loader
    from homeassistant.setup import async_setup_component

    (config_dir / "custom_components").mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        REPO / "custom_components" / DOMAIN,
        config_dir / "custom_components" / DOMAIN,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    hass = core.HomeAssistant(str(config_dir))
    hass.config.config_dir = str(config_dir)
    hass.config.latitude = 0.0
    hass.config.longitude = 0.0
    hass.config.time_zone = "UTC"
    loader.async_setup(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await bootstrap.async_load_base_functionality(hass)
    await async_setup_component(hass, "homeassistant", {})
    await hass.async_start()
    return hass


async def _start_wallbox(port: str, bank: dict[int, int]) -> Any:
    from pymodbus import FramerType
    from pymodbus.server import ModbusSerialServer

    words = [_signed(bank.get(address, 0)) for address in range(REGISTER_COUNT)]
    server = ModbusSerialServer(
        _server_context(words),
        framer=FramerType.RTU,
        port=port,
        baudrate=57600,
        bytesize=8,
        parity="N",
        stopbits=2,
    )
    task = asyncio.get_running_loop().create_task(server.serve_forever())
    await asyncio.sleep(1.0)
    return server, task


def test_home_assistant_sets_the_integration_up(tmp_path: Path) -> None:
    """Set up, switch to master mode, reconfigure and unload again."""

    async def run() -> None:
        bank = device_bank(error_code=200, fallback_current=0)
        device_port, client_port = _virtual_serial_link()
        server, serving = await _start_wallbox(device_port, bank)
        try:
            hass = await _boot(tmp_path / "config")
        except (AttributeError, ImportError) as err:  # pragma: no cover
            await server.shutdown()
            serving.cancel()
            pytest.skip(f"Home Assistant bootstrap is not usable here: {err}")

        from homeassistant.helpers import (
            device_registry as dr,
        )
        from homeassistant.helpers import (
            entity_registry as er,
        )
        from homeassistant.helpers import (
            issue_registry as ir,
        )

        try:
            flow = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}
            )
            assert flow["step_id"] == "user"
            result = await hass.config_entries.flow.async_configure(
                flow["flow_id"],
                {
                    CONF_PORT: client_port,
                    CONF_DEVICE_ID: 50,
                    CONF_BAUDRATE: 57600,
                    CONF_BYTESIZE: 8,
                    CONF_PARITY: "N",
                    CONF_STOPBITS: 2,
                },
            )
            assert result["type"] == "create_entry", result
            await hass.async_block_till_done()
            entry = hass.config_entries.async_entries(DOMAIN)[0]

            # The title becomes the device name and prefixes every entity id,
            # so it carries no serial number.
            assert entry.title == "MENNEKES AMTRON"
            assert entry.unique_id == SERIAL_NUMBER

            registry = er.async_get(hass)
            entities = er.async_entries_for_config_entry(registry, entry.entry_id)
            assert len(entities) > 40
            assert all(
                SERIAL_NUMBER.lower() not in entity.entity_id for entity in entities
            )
            platforms = {entity.domain for entity in entities}
            assert platforms == {"sensor", "binary_sensor"}

            device = dr.async_entries_for_config_entry(
                dr.async_get(hass), entry.entry_id
            )[0]
            assert device.serial_number == SERIAL_NUMBER
            assert device.sw_version == "2023.21.11024"
            assert device.model == "1313201205"

            # A wallbox in error state 200 without a configured fallback.
            issues = {
                issue.issue_id.removeprefix(f"{entry.entry_id}_")
                for issue in ir.async_get(hass).issues.values()
                if issue.domain == DOMAIN
            }
            assert issues == {"ems_heartbeat_lost", "ems_fallback_not_configured"}

            assert hass.services.has_service(DOMAIN, "set_charging_current")

            options = await hass.config_entries.options.async_init(entry.entry_id)
            await hass.config_entries.options.async_configure(
                options["flow_id"],
                {
                    CONF_CONTROL_MODE: CONTROL_MODE_MASTER,
                    CONF_SCAN_INTERVAL_SECONDS: 5,
                    "current_limit": 16.0,
                },
            )
            await hass.async_block_till_done()
            entry = hass.config_entries.async_entries(DOMAIN)[0]
            entities = er.async_entries_for_config_entry(registry, entry.entry_id)
            assert {"number", "switch", "select", "button"} <= {
                entity.domain for entity in entities
            }
            assert entry.runtime_data.heartbeat is not None
            assert entry.runtime_data.heartbeat.running

            number = next(
                entity.entity_id for entity in entities if entity.domain == "number"
            )
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": number, "value": 10.0},
                blocking=True,
            )
            await hass.async_block_till_done()
            assert hass.states.get(number).state == "10.0"

            # Reconfiguring starts from the entry, not from the factory values.
            reconfigure = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "reconfigure", "entry_id": entry.entry_id},
            )
            suggested = {
                str(marker): (marker.description or {}).get("suggested_value")
                for marker in reconfigure["data_schema"].schema
            }
            assert suggested[CONF_PORT] == client_port
            assert suggested[CONF_DEVICE_ID] == 50

            heartbeat = entry.runtime_data.heartbeat
            assert await hass.config_entries.async_unload(entry.entry_id)
            assert not heartbeat.running
        finally:
            await hass.async_stop()
            await server.shutdown()
            serving.cancel()

    asyncio.run(asyncio.wait_for(run(), timeout=180))


def test_the_integration_manifest_matches_what_home_assistant_loads() -> None:
    assert R.VALIDATED_LAYOUT == R.LAYOUT_V01_03
