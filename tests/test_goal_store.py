"""A restart must retain a limit, and corruption must not silently remove it."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE
from homeassistant.core import CoreState
from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError

from custom_components.mennekes_amtron_rtu import goal_store
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.energy_goal import EnergyGoal
from custom_components.mennekes_amtron_rtu.device.soc_goal import SocGoal
from custom_components.mennekes_amtron_rtu.goal_store import GoalStore, SocGoalStore
from tests.ha_fakes import FakeHass
from tests.test_home_assistant_smoke import _boot


def store_at(path: Path, identity: str = "wallbox") -> GoalStore:
    hass = FakeHass(str(path))
    hass.state = CoreState.running
    hass.async_add_executor_job = asyncio.to_thread
    return GoalStore(hass, "entry", identity)


def test_goal_survives_restart_without_saving_normal_progress(tmp_path):
    async def run():
        store = store_at(tmp_path)
        goal = await store.async_load()
        assert goal.status == "disabled"
        goal.set_target(12, 1200)
        await store.async_save(goal)
        path = store.path
        original = await asyncio.to_thread(path.read_bytes)
        mtime = (await asyncio.to_thread(path.stat)).st_mtime_ns
        assert goal.observe(1206) is False
        await store.async_save(goal)
        assert await asyncio.to_thread(path.read_bytes) == original
        assert (await asyncio.to_thread(path.stat)).st_mtime_ns == mtime
        restarted = store_at(tmp_path)
        restored = await restarted.async_load()
        assert restored.snapshot() == goal.snapshot()
        assert restored.observe(1212)
        await restarted.async_save(restored)
        assert (await store_at(tmp_path).async_load()).stop_requested
        await restarted.async_remove()
        await restarted.async_remove()
        assert not await asyncio.to_thread(path.exists)

    asyncio.run(run())


@pytest.mark.parametrize("raw", [
    "{broken", "[]", "null", " " * 32769,
    '{"version":true,"data":{}}', '{"version":2,"data":{}}',
    '{"version":1,"minor_version":true,"data":{}}',
    '{"version":1,"minor_version":2,"data":{}}',
    '{"version":1,"key":"other","data":{}}',
    '{"version":1,"data":null}',
    '{"version":1,"data":{"identity":"another","goal":{}}}',
    '{"version":1,"data":{"identity":"wallbox","goal":{}}}',
])
def test_invalid_goal_is_retained_and_blocks_loading(tmp_path, raw):
    async def run():
        store = store_at(tmp_path)
        path = store.path
        path.parent.mkdir(parents=True)
        await asyncio.to_thread(path.write_text, raw)
        with pytest.raises(ValueError, match=r"energy goal|saved energy"):
            await store.async_load()
        assert await asyncio.to_thread(path.read_text) == raw
        assert not list(path.parent.glob("*.corrupt*"))

    asyncio.run(run())


def test_reloading_reads_and_validates_the_current_file(tmp_path):
    async def run():
        store = store_at(tmp_path)
        goal = EnergyGoal()
        goal.set_target(1, 100)
        await store.async_save(goal)
        payload = json.loads(await asyncio.to_thread(store.path.read_text))
        payload["data"]["identity"] = "other"
        await asyncio.to_thread(store.path.write_text, json.dumps(payload))
        with pytest.raises(ValueError, match="does not match"):
            await store.async_load()

    asyncio.run(run())


def test_cancel_during_save_finishes_the_atomic_write(tmp_path, monkeypatch):
    async def run():
        store = store_at(tmp_path)
        goal = EnergyGoal()
        goal.set_target(1, 100)
        started, release = threading.Event(), threading.Event()
        real_write = goal_store.write_utf8_file_atomic

        def delayed(*args, **kwargs):
            started.set()
            assert release.wait(5)
            real_write(*args, **kwargs)

        monkeypatch.setattr(goal_store, "write_utf8_file_atomic", delayed)
        task = asyncio.create_task(store.async_save(goal))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        data = json.loads(await asyncio.to_thread(store.path.read_text))["data"]
        assert data["goal"] == goal.snapshot()
        assert store._last == data

    asyncio.run(run())


@pytest.mark.parametrize("error", [WriteError("disk full"), OSError("read-only filesystem")])
def test_failed_atomic_save_keeps_previous_goal_and_propagates(tmp_path, monkeypatch, error):
    async def run():
        store = store_at(tmp_path)
        goal = EnergyGoal()
        goal.set_target(1, 100)
        await store.async_save(goal)
        previous = await asyncio.to_thread(store.path.read_bytes)
        accepted = store._last

        def fail(*args, **kwargs):
            raise error

        monkeypatch.setattr(goal_store, "write_utf8_file_atomic", fail)
        goal.set_target(0)
        with pytest.raises(OSError, match=r"persisted|read-only filesystem"):
            await store.async_save(goal)
        assert await asyncio.to_thread(store.path.read_bytes) == previous
        assert store._last == accepted
        assert (await store_at(tmp_path).async_load()).target_kwh == 1

    asyncio.run(run())


def test_goal_files_use_the_home_assistant_storage_envelope(tmp_path):
    async def run():
        store = store_at(tmp_path)
        goal = EnergyGoal()
        goal.set_target(1, 100)
        await store.async_save(goal)
        native = Store(store._hass, 1, f"{DOMAIN}.energy_goal.entry")
        assert await native.async_load() == {"identity": "wallbox", "goal": goal.snapshot()}
        assert (await asyncio.to_thread(store.path.stat)).st_mode & 0o777 == 0o600

    asyncio.run(run())


def test_soc_and_energy_stores_keep_separate_models_files_and_cached_payloads(tmp_path):
    async def run():
        energy_store = store_at(tmp_path)
        soc_store = SocGoalStore(energy_store._hass, "entry", "wallbox")
        energy = EnergyGoal()
        energy.set_target(12, 100)
        soc = await soc_store.async_load()
        assert isinstance(soc, SocGoal)
        soc.configure_source("sensor.car_soc|number.car_charge_limit")
        soc.set_target(80, soc=20, maximum=90)
        await asyncio.gather(energy_store.async_save(energy), soc_store.async_save(soc))
        assert energy_store.path != soc_store.path
        assert soc_store.path.name == f"{DOMAIN}.soc_goal.entry"
        original_energy = await asyncio.to_thread(energy_store.path.read_bytes)

        # A falling car cap is persistent, even if that entity later disappears.
        soc.observe(40, 70)
        await soc_store.async_save(soc)
        restored = await SocGoalStore(energy_store._hass, "entry", "wallbox").async_load()
        assert restored.target_percent == 70
        assert restored.maximum_percent == 70
        assert restored.observe(75)
        assert restored.stop_requested
        await soc_store.async_save(restored)
        assert (await soc_store.async_load()).status == "reached"
        assert (await energy_store.async_load()).snapshot() == energy.snapshot()
        assert await asyncio.to_thread(energy_store.path.read_bytes) == original_energy

        # The right filename cannot make the wrong model schema acceptable.
        malformed = json.loads(await asyncio.to_thread(soc_store.path.read_text))
        malformed["data"]["goal"] = energy.snapshot()
        await asyncio.to_thread(soc_store.path.write_text, json.dumps(malformed))
        with pytest.raises(ValueError, match="percentage goal"):
            await soc_store.async_load()
        await soc_store.async_remove()
        assert (await energy_store.async_load()).target_kwh == 12
        assert (await soc_store.async_load()).status == "disabled"

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["energy", "soc"])
def test_stopping_save_is_durable_before_final_write_and_failure_is_never_queued(tmp_path, monkeypatch, kind):
    async def run():
        hass = await _boot(tmp_path)
        try:
            store_type = GoalStore if kind == "energy" else SocGoalStore
            store = store_type(hass, "entry", "wallbox")
            if kind == "energy":
                goal = EnergyGoal()
                goal.set_target(1, 100)
            else:
                goal = SocGoal()
                goal.configure_source("sensor.car_soc|number.car_charge_limit")
                goal.set_target(80, soc=20, maximum=90)
            await store.async_save(goal)
            hass.set_state(CoreState.stopping)

            # A goal reached during shutdown must be durable immediately.
            goal.observe(101 if kind == "energy" else 80)
            await store.async_save(goal)
            restarted = store_type(hass, "entry", "wallbox")
            assert (await restarted.async_load()).status == "reached"

            # The rejected disable must not get written later by HA FINAL_WRITE.
            goal.set_target(0)
            with monkeypatch.context() as patch:
                def fail(*args, **kwargs):
                    raise WriteError("disk full")
                patch.setattr(goal_store, "write_utf8_file_atomic", fail)
                with pytest.raises(OSError, match="persisted"):
                    await store.async_save(goal)
            hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
            await hass.async_block_till_done()
            assert (await restarted.async_load()).status == "reached"

            # An accepted disable is likewise durable before HA's final event.
            await store.async_save(goal)
            assert (await restarted.async_load()).status == "disabled"
            hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
            await hass.async_block_till_done()
            assert (await restarted.async_load()).status == "disabled"
        finally:
            hass.set_state(CoreState.running)
            await hass.async_stop()

    asyncio.run(run())
