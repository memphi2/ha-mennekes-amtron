"""Measure actual SQLite rows for service plans/orders through the HA API client."""
import asyncio
import sqlite3
from contextlib import closing

from aiohttp.test_utils import TestServer
from homeassistant.components.recorder import get_instance
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.recorder import async_initialize_recorder
from homeassistant.setup import async_setup_component

from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.service_departure_runtime import (
    DepartureModbusService,
)
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.entity import entry_entity_unique_id
from tests import service_guest_fixture as runtime
from tests.test_home_assistant_smoke import _boot
from tests.test_service_departure_runtime import hand_over
from tests.test_service_reauth import add_entry


def test_service_plans_and_orders_do_not_record_unchanged_polls(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "GuestModbusService", DepartureModbusService)

    async def run():
        async with runtime.guest_service(tmp_path) as (service, transport, _clock):
            hass = await _boot(tmp_path / "ha")
            async_initialize_recorder(hass)
            db = tmp_path / "recorder.db"
            try:
                assert await async_setup_component(hass, "recorder", {"recorder": {
                    "db_url": f"sqlite:///{db}", "commit_interval": 1, "auto_purge": False,
                }})
                async with TestServer(create_app(service)) as server:
                    entry = await add_entry(hass, str(server.make_url("/")))
                    coordinator = entry.runtime_data.coordinator
                    coordinator.update_interval = None
                    recorder = get_instance(hass)

                    registry = er.async_get(hass)
                    ids = [registry.async_get_entity_id("sensor", DOMAIN, entry_entity_unique_id(entry, key))
                           for key in (*OPERATION_SENSOR_KEYS, "guest_charging")]
                    assert all(ids), ids

                    async def rows():
                        await hass.async_block_till_done()
                        await recorder.async_block_till_done()
                        with closing(sqlite3.connect(db)) as connection:
                            return {entity: count for entity, count in connection.execute(
                                "SELECT m.entity_id, count(*) FROM states s JOIN states_meta m USING(metadata_id) "
                                "GROUP BY m.entity_id"
                            ) if entity in ids}

                    await hand_over(service, hours=10)
                    await service.departure.tick()
                    await coordinator.async_refresh()
                    before = await rows()
                    assert len(before) >= 4, before
                    reads = len(transport.reads)
                    for _ in range(120):
                        await service.departure.tick()
                        await coordinator.async_refresh()
                    assert await rows() == before
                    assert len(transport.reads) == reads

                    await service.command({"command":"guest_start", "expected_serial_number":service.identity.serial_number,
                                           "expected_guest_revision":service.guest_revision})
                    await coordinator.async_refresh()
                    before = await rows()
                    reads = len(transport.reads)
                    for _ in range(120):
                        await service._guest_tick()
                        await service.departure.tick()
                        await coordinator.async_refresh()
                    assert await rows() == before
                    assert len(transport.reads) == reads
            finally:
                await hass.async_stop()
    asyncio.run(run())
