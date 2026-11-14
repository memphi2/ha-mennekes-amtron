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
from custom_components.mennekes_amtron_rtu.device.service_job_http import create_job_app
from custom_components.mennekes_amtron_rtu.entity import entry_entity_unique_id
from custom_components.mennekes_amtron_rtu.sensor_descriptions import (
    OPERATION_SENSOR_KEYS,
)
from tests import test_service_charge_runtime as runtime
from tests.test_home_assistant_smoke import _boot
from tests.test_service_departure_runtime import hand_over
from tests.test_service_reauth import add_entry


def test_service_plans_and_orders_do_not_record_unchanged_polls(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "JobModbusService", DepartureModbusService)

    async def run():
        async with runtime.job_service(tmp_path) as (service, transport, _clock):
            hass = await _boot(tmp_path / "ha")
            async_initialize_recorder(hass)
            db = tmp_path / "recorder.db"
            try:
                assert await async_setup_component(hass, "recorder", {"recorder": {
                    "db_url": f"sqlite:///{db}", "commit_interval": 1, "auto_purge": False,
                }})
                async with TestServer(create_job_app(service)) as server:
                    entry = await add_entry(hass, str(server.make_url("/")))
                    coordinator = entry.runtime_data.coordinator
                    coordinator.update_interval = None
                    recorder = get_instance(hass)

                    registry = er.async_get(hass)
                    ids = [registry.async_get_entity_id("sensor", DOMAIN, entry_entity_unique_id(entry, key))
                           for key in (*OPERATION_SENSOR_KEYS, "charging_job")]
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

                    current = service.jobs.snapshot()
                    await service.jobs.create(runtime.request(future=True), revision=current["revision"], scope=current["scope"])
                    await service.departure.tick()
                    await coordinator.async_refresh()
                    before = await rows()
                    reads = len(transport.reads)
                    for _ in range(120):
                        await service.jobs.tick()
                        await service.departure.tick()
                        await coordinator.async_refresh()
                    assert await rows() == before
                    assert len(transport.reads) == reads
            finally:
                await hass.async_stop()
    asyncio.run(run())
