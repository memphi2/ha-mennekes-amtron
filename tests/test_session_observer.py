"""Optional session persistence must never break direct charging or polling."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.mennekes_amtron_rtu import session_observer
from custom_components.mennekes_amtron_rtu.charge_loss_const import (
    CONF_CHARGE_LOSS_SOURCE,
)
from custom_components.mennekes_amtron_rtu.charging_cost_const import (
    CONF_CHARGING_COST_SOURCE,
)
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.data import DeviceIdentity
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.goal_store import SessionHistoryStore
from custom_components.mennekes_amtron_rtu.session_observer import SessionObserver
from tests.ha_fakes import FakeConfigEntry, FakeHass
from tests.test_charging_session import complete, reading


def observer_at(path, *, source="sensor", layout=R.LAYOUT_V01_03, serial="wallbox", cost="disabled"):
    hass = FakeHass(str(path))
    hass.async_add_executor_job = asyncio.to_thread
    options = {CONF_CHARGE_LOSS_SOURCE: source} if source is not None else {}
    options[CONF_CHARGING_COST_SOURCE] = cost
    entry = FakeConfigEntry(options=options)
    coordinator = SimpleNamespace(observe_session=None)
    clock = [1000.0]
    observer = SessionObserver(
        hass, entry, DeviceIdentity(layout_version=layout, serial_number=serial), coordinator,
        wall_clock=lambda: clock[0], monotonic=lambda: clock[0],
    )
    coordinator.observe_session = observer.async_observe
    return observer, clock


async def observe(observer, clock, at, state=EvseState.IDLE, total=100, **kwargs):
    clock[0] = at
    data = reading(state, total, **kwargs)
    await observer.async_observe(data)
    return data


async def cycle(observer, clock, *, offset=0, baseline=100):
    await observe(observer, clock, offset + 1000, total=baseline)
    await observe(observer, clock, offset + 1010, EvseState.CHARGING, baseline + 1)
    return await observe(observer, clock, offset + 1030, total=baseline + 10)


def test_complete_session_survives_restart_without_storing_partial_progress(tmp_path):
    async def run():
        observer, clock = observer_at(tmp_path)
        await observer.async_setup()
        assert not await asyncio.to_thread(observer._store.path.exists)
        await observe(observer, clock, 1000)
        active = await observe(observer, clock, 1010, EvseState.CHARGING, 101)
        assert active.session_summary_supported
        assert active.charging_session is None
        assert not await asyncio.to_thread(observer._store.path.exists)
        finished = await observe(observer, clock, 1030, total=110)
        assert finished.charging_session["wallbox_kwh"] == 10
        saved = await asyncio.to_thread(observer._store.path.read_bytes)
        stamp = (await asyncio.to_thread(observer._store.path.stat)).st_mtime_ns
        again = await observe(observer, clock, 1040, total=110)
        assert again.charging_session == finished.charging_session
        assert await asyncio.to_thread(observer._store.path.read_bytes) == saved
        assert (await asyncio.to_thread(observer._store.path.stat)).st_mtime_ns == stamp
        assert json.loads(saved)["data"]["identity"] == "wallbox"
        await observer.async_close()
        assert observer._coordinator.observe_session is None
        restarted, clock = observer_at(tmp_path)
        await restarted.async_setup()
        after = await observe(restarted, clock, 1050, total=110)
        assert after.charging_session == finished.charging_session
        await restarted.async_close()

    asyncio.run(run())


@pytest.mark.parametrize(("source", "layout", "serial"), [
    (None, R.LAYOUT_V01_03, "wallbox"),
    ("disabled", R.LAYOUT_V01_03, "wallbox"),
    ("sensor", R.LAYOUT_V01_00, "wallbox"),
    ("bavariandata", R.LAYOUT_V01_01, "wallbox"),
    ("sensor", R.LAYOUT_V01_03, None),
])
def test_disabled_or_unsupported_session_capture_never_touches_storage(tmp_path, source, layout, serial):
    async def run():
        observer, clock = observer_at(tmp_path, source=source, layout=layout, serial=serial)
        observer._store.async_load = AsyncMock(side_effect=AssertionError("must not read"))
        observer._store.async_save = AsyncMock(side_effect=AssertionError("must not save"))
        await observer.async_setup()
        data = await cycle(observer, clock)
        assert not data.session_summary_supported
        assert data.charging_session is None
        await observer.async_observe(None)
        await observer.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("interruption", ["poll_failure", "startup", "meter_failure", "invalid_cp"])
def test_incomplete_direct_session_is_never_reported_as_complete(tmp_path, interruption):
    async def run():
        observer, clock = observer_at(tmp_path)
        await observer.async_setup()
        if interruption != "startup":
            await observe(observer, clock, 1000)
        await observe(observer, clock, 1010, EvseState.CHARGING, 101)
        if interruption == "poll_failure":
            await observer.async_observe(None)
        elif interruption == "meter_failure":
            await observe(observer, clock, 1020, EvseState.CHARGING, None)
        elif interruption == "invalid_cp":
            await observe(observer, clock, 1020, EvseState.CHARGING, 105, cp=CpState.A1)
        data = await observe(observer, clock, 1030, total=110)
        assert data.session_summary_supported
        assert data.charging_session is None
        assert not await asyncio.to_thread(observer._store.path.exists)
        await observer.async_close()

    asyncio.run(run())


def test_layout_cp_capability_is_reapplied_after_restore(tmp_path, monkeypatch):
    async def run():
        # Current supported energy layouts all have CP. Keep the model's
        # fallback explicitly bound to capability, including after restore.
        monkeypatch.setattr(session_observer, "CP_STATE", SimpleNamespace(min_layout=999))
        observer, clock = observer_at(tmp_path, layout=R.LAYOUT_V01_02)
        await observer.async_setup()
        idle = reading()
        idle.values.pop("cp_state")
        await observer.async_observe(idle)
        active = reading(EvseState.CHARGING, 101)
        active.values.pop("cp_state")
        clock[0] = 1010
        await observer.async_observe(active)
        clock[0] = 1030
        idle.values["energy_total"] = 110
        await observer.async_observe(idle)
        assert idle.charging_session["wallbox_kwh"] == 10
        await observer.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", [OSError("private path"), ValueError("private identity")])
def test_failed_load_retries_bounded_and_cannot_erase_existing_state(tmp_path, caplog, failure):
    async def run():
        observer, clock = observer_at(tmp_path)
        saved = complete()
        observer._store.async_load = AsyncMock(side_effect=[failure, failure, saved])
        observer._store.async_save = AsyncMock()
        await observer.async_setup()
        data = await observe(observer, clock, 1001)
        assert not data.session_summary_supported
        assert data.charging_session is None
        assert observer._store.async_load.await_count == 1
        await observe(observer, clock, 1059)
        assert observer._store.async_load.await_count == 1
        await observe(observer, clock, 1060)
        assert observer._store.async_load.await_count == 2
        data = await observe(observer, clock, 1120, total=110)
        assert data.session_summary_supported
        assert data.charging_session == saved.latest.as_dict()
        observer._store.async_save.assert_not_awaited()
        assert caplog.text.count("Optional charging-session storage") == 1
        assert "private" not in caplog.text
        await observer.async_close()

    asyncio.run(run())


def test_wrong_identity_is_preserved_and_never_published(tmp_path):
    async def run():
        observer, clock = observer_at(tmp_path)
        foreign = SessionHistoryStore(observer._store._hass, "entry-1", "another-wallbox")
        await foreign.async_save(complete())
        original = await asyncio.to_thread(foreign.path.read_bytes)
        await observer.async_setup()
        data = await cycle(observer, clock)
        assert not data.session_summary_supported
        assert data.charging_session is None
        await observer.async_close()
        assert await asyncio.to_thread(foreign.path.read_bytes) == original

    asyncio.run(run())


@pytest.mark.parametrize("failure", [OSError("disk full"), ValueError("invalid state")])
def test_failed_save_does_not_fail_poll_and_retries_only_latest_state(tmp_path, caplog, failure):
    async def run():
        observer, clock = observer_at(tmp_path)
        await observer.async_setup()
        real_save = observer._store.async_save
        observer._store.async_save = AsyncMock(side_effect=failure)
        first = await cycle(observer, clock)
        assert first.charging_session["wallbox_kwh"] == 10
        assert first.session_summary_supported
        assert observer._dirty
        await observe(observer, clock, 1031, total=110)
        assert observer._store.async_save.await_count == 1
        await observe(observer, clock, 1090, total=110)
        assert observer._store.async_save.await_count == 2
        assert caplog.text.count("Optional charging-session storage") == 1
        observer._store.async_save = AsyncMock(wraps=real_save)
        # A second finished cycle while retrying replaces the unsaved first.
        second = await cycle(observer, clock, offset=100, baseline=110)
        assert not observer._store.async_save.await_count
        data = await observe(observer, clock, 1150, total=120)
        assert observer._store.async_save.await_count == 1
        assert not observer._dirty
        restored = await observer._store.async_load()
        assert restored.latest.as_dict() == second.charging_session == data.charging_session
        assert restored.latest.session_id != first.charging_session["session_id"]
        await observer.async_close()

    asyncio.run(run())


def test_close_flushes_once_and_does_not_clear_a_replacement_callback(tmp_path):
    async def run():
        observer, clock = observer_at(tmp_path)
        await observer.async_setup()
        observer._store.async_save = AsyncMock(side_effect=[OSError(), None])
        completed = await cycle(observer, clock)
        replacement = AsyncMock()
        observer._coordinator.observe_session = replacement
        await observer.async_close()
        assert observer._store.async_save.await_count == 2
        assert not observer._dirty
        assert observer._coordinator.observe_session is replacement
        await observer.async_close()
        await observer.async_setup()
        await observer.async_observe(None)
        assert observer._store.async_save.await_count == 2
        assert observer._history.latest.as_dict() == completed.charging_session

    asyncio.run(run())


def test_cancellation_propagates_instead_of_becoming_a_storage_failure(tmp_path):
    async def run():
        observer, clock = observer_at(tmp_path)
        observer._store.async_load = AsyncMock(side_effect=asyncio.CancelledError)
        with pytest.raises(asyncio.CancelledError):
            await observer.async_setup()
        observer._store.async_load = AsyncMock(return_value=complete())
        await observer.async_setup()
        observer._store.async_save = AsyncMock(side_effect=asyncio.CancelledError)
        with pytest.raises(asyncio.CancelledError):
            await cycle(observer, clock, baseline=110)
        assert observer._dirty
        observer._store.async_save = AsyncMock()
        await observer.async_close()

    asyncio.run(run())


def test_observe_can_load_once_if_setup_was_not_called(tmp_path):
    async def run():
        observer, clock = observer_at(tmp_path)
        observer._store.async_load = AsyncMock(wraps=observer._store.async_load)
        await observe(observer, clock, 1000)
        await observer.async_setup()
        observer._store.async_load.assert_awaited_once()
        await observer.async_close()

    asyncio.run(run())


def test_costs_enable_session_capture_without_vehicle_loss_source(tmp_path):
    async def run():
        observer, clock = observer_at(tmp_path, source="disabled", cost="fixed")
        await observer.async_setup()
        data = await cycle(observer, clock)
        assert data.session_summary_supported
        assert data.charging_session["wallbox_kwh"] == 10
        await observer.async_close()

    asyncio.run(run())
