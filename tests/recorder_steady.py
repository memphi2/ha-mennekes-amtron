"""Observe real SQLite Recorder writes and maintenance during serial operation."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from contextlib import closing, suppress
from datetime import timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.tasks import PurgeTask, SynchronizeTask
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.recorder import async_initialize_recorder
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from sqlalchemy import event


class RecorderMeasurement:
    """Keep bounded counters; every database belongs to this disposable test HA."""

    def __init__(self, hass, database):
        self.hass = hass
        self.database = database
        self.recorder = get_instance(hass)
        self.deleted = 0
        self.vacuums = 0
        self.purges = 0
        self.maintenance_max = 0.0
        self.queue_peak = 0
        self.database_peak = 0
        self.wal_peak = 0
        self.entities = 0
        self.baseline_rows = 0
        self.baseline_attributes = 0
        self.queue_after_drain = None
        self.integrity = None
        self.vacuum_done = asyncio.Event()
        event.listen(self.recorder.engine, "after_cursor_execute", self._note_sql)
        self.watcher = asyncio.create_task(self._watch(), name="amtron_recorder_measurement")

    @classmethod
    async def start(cls, hass, database):
        async_initialize_recorder(hass)
        assert await async_setup_component(hass, "recorder", {"recorder": {
            "db_url": f"sqlite:///{database}", "commit_interval": 1, "auto_purge": False,
        }})
        await get_instance(hass).async_block_till_done()
        return cls(hass, database)

    def _note_sql(self, _conn, cursor, statement, _parameters, _context, _many):
        statement = statement.strip().upper()
        if statement.startswith("DELETE FROM STATES WHERE"):
            # Actual deleted rows compensate snapshots for retention. Unlike
            # MAX(state_id), this still works if SQLite reuses deleted IDs.
            assert cursor.rowcount >= 0
            self.deleted += cursor.rowcount
        elif statement == "VACUUM":
            self.vacuums += 1
            self.hass.loop.call_soon_threadsafe(self.vacuum_done.set)

    def _sizes(self):
        sizes = []
        for path in (self.database, self.database.with_name(self.database.name + "-wal")):
            try:
                sizes.append(path.stat().st_size)
            except FileNotFoundError:
                sizes.append(0)
        return sizes

    async def _watch(self):
        while True:
            self.queue_peak = max(self.queue_peak, self.recorder.backlog)
            database, wal = await self.hass.async_add_executor_job(self._sizes)
            self.database_peak = max(self.database_peak, database)
            self.wal_peak = max(self.wal_peak, wal)
            await asyncio.sleep(0.25)

    async def committed(self):
        future = self.hass.loop.create_future()
        self.recorder.queue_task(SynchronizeTask(future))
        await asyncio.wait_for(future, 10)

    def _rows(self):
        with closing(sqlite3.connect(self.database, timeout=5)) as connection:
            rows = connection.execute("SELECT COUNT(*) FROM states").fetchone()[0]
            attributes = connection.execute("SELECT COUNT(*) FROM state_attributes").fetchone()[0]
            energy, power = (
                connection.execute(
                    "SELECT COUNT(*) FROM states s JOIN states_meta m USING(metadata_id) "
                    "WHERE m.entity_id=? AND s.state NOT IN ('unknown', 'unavailable')",
                    (entity_id,),
                ).fetchone()[0]
                for entity_id in self.energy_power_entities
            )
        return rows, attributes, energy, power

    async def baseline(self, entry):
        registry = er.async_get(self.hass)
        entities = er.async_entries_for_config_entry(registry, entry.entry_id)
        self.entities = sum(self.hass.states.get(entity.entity_id) is not None for entity in entities)
        self.energy_power_entities = [
            next(entity.entity_id for entity in entities if entity.unique_id.endswith(suffix))
            for suffix in ("_energy_total", "_power_total")
        ]
        await self.committed()
        rows, attributes, _energy, _power = await self.hass.async_add_executor_job(self._rows)
        self.baseline_rows = rows + self.deleted
        self.baseline_attributes = attributes

    async def maintain(self, retention):
        await self.committed()
        self.vacuum_done.clear()
        started = time.monotonic()
        self.recorder.queue_task(PurgeTask(
            dt_util.utcnow() - timedelta(seconds=retention), repack=True, apply_filter=False,
        ))
        await asyncio.wait_for(self.vacuum_done.wait(), 15)
        await self.committed()
        self.purges += 1
        self.maintenance_max = max(self.maintenance_max, time.monotonic() - started)

    async def sample(self):
        await self.committed()
        rows, attributes, energy, power = await self.hass.async_add_executor_job(self._rows)
        database, wal = await self.hass.async_add_executor_job(self._sizes)
        return {
            "recorder_states": rows, "recorder_inserted": rows + self.deleted - self.baseline_rows,
            "recorder_attribute_growth": max(0, attributes - self.baseline_attributes),
            "recorded_energy_rows": energy, "recorded_power_rows": power,
            "database_bytes": database, "wal_bytes": wal,
        }

    def report(self):
        return {
            "entities": self.entities, "queue_peak": self.queue_peak,
            "queue_after_drain": self.queue_after_drain,
            "database_peak_bytes": self.database_peak, "wal_peak_bytes": self.wal_peak,
            "purges": self.purges, "vacuums": self.vacuums, "deleted_rows": self.deleted,
            "maintenance_max_seconds": self.maintenance_max, "integrity": self.integrity,
        }

    def _integrity(self):
        with closing(sqlite3.connect(self.database, timeout=5)) as connection:
            return connection.execute("PRAGMA integrity_check").fetchone()[0]

    async def finish(self):
        await self.committed()
        self.queue_after_drain = self.recorder.backlog
        self.integrity = await self.hass.async_add_executor_job(self._integrity)

    async def close(self):
        self.watcher.cancel()
        with suppress(asyncio.CancelledError):
            await self.watcher
        event.remove(self.recorder.engine, "after_cursor_execute", self._note_sql)
