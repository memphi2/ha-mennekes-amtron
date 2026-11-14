"""An acknowledged intent must survive a rename and retry failed durability checks."""

from __future__ import annotations

import asyncio
import json
import os
import stat

import pytest

from custom_components.mennekes_amtron_rtu.device.service_store import IntentStore
from custom_components.mennekes_amtron_rtu.device.write_state import WriteState
from tests.test_modbus_service import service_fixture


@pytest.mark.parametrize("fail_directory_sync", [False, True])
def test_intent_is_committed_only_after_file_and_directory_sync(
    tmp_path, monkeypatch, fail_directory_sync
):
    store = IntentStore(tmp_path / "intent.json", "test-wallbox")
    state = WriteState(pause_requested=True, charging_release_requested=False)
    fsync = os.fsync
    calls = []
    directory_fds = []

    def sync(descriptor):
        directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
        calls.append("directory" if directory else "file")
        assert store._last is None
        if directory:
            directory_fds.append(descriptor)
            restored = WriteState()
            IntentStore(store.path, "test-wallbox").load(restored)
            assert restored.pause_requested is True
            assert restored.charging_release_requested is False
            if fail_directory_sync:
                raise OSError("directory sync failed")
        return fsync(descriptor)

    monkeypatch.setattr(os, "fsync", sync)
    if fail_directory_sync:
        with pytest.raises(OSError, match="directory sync"):
            store.save(state)
        assert calls == ["file", "directory"]
        assert store._last is None
        fail_directory_sync = False
        calls.clear()
    store.save(state)
    assert calls == ["file", "directory"]
    assert store._last is not None
    for descriptor in directory_fds:
        with pytest.raises(OSError, match=r"\[Errno 9\]"):
            os.fstat(descriptor)
    store.save(state)
    assert calls == ["file", "directory"]
    assert list(tmp_path.glob(".intent-*")) == []


@pytest.mark.parametrize("kind", ["energy", "soc"])
def test_cleared_goal_remains_visible_when_directory_sync_fails(tmp_path, monkeypatch, kind):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        sync = os.fsync
        try:
            if kind == "energy":
                await service.control.async_set_energy_goal(12)
                clear = service.control.async_set_energy_goal(0)
            else:
                await service.control.async_configure_soc("sensor.soc")
                await service.control.async_set_soc_goal(80, source="sensor.soc", soc=20)
                clear = service.control.async_set_soc_goal(0, source="sensor.soc")

            def fail_directory(descriptor):
                if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                    raise OSError("directory sync failed")
                return sync(descriptor)

            with monkeypatch.context() as patcher:
                patcher.setattr(os, "fsync", fail_directory)
                with pytest.raises(OSError, match="directory sync failed"):
                    await clear
            current = service.control.energy_goal if kind == "energy" else service.control.soc_goal
            assert current.status == "disabled"
            restored = WriteState()
            IntentStore(service._store.path, service._store.identity).load(restored)
            saved = restored.energy_goal if kind == "energy" else restored.soc_goal
            assert saved.snapshot() == current.snapshot()
            # An explicit repeat retries the directory sync rather than
            # treating the uncertain prior write as durable.
            if kind == "energy":
                assert service._store._last is None
                await service.control.async_set_energy_goal(0)
            else:
                await service.control.async_set_soc_goal(0, source="sensor.soc")
            assert service._store._last is not None
        finally:
            await service.stop()

    asyncio.run(run())


def test_post_rename_failure_invalidates_old_deduplication_cache(tmp_path, monkeypatch):
    path = tmp_path / "intent.json"
    store = IntentStore(path, "wallbox")
    state = WriteState(pause_requested=True)
    store.save(state)
    sync = os.fsync

    def fail_directory(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("directory sync failed")
        return sync(descriptor)

    state.pause_requested = False
    with monkeypatch.context() as patcher:
        patcher.setattr(os, "fsync", fail_directory)
        with pytest.raises(OSError, match="directory sync failed"):
            store.save(state)
    state.pause_requested = True
    store.save(state)
    restored = WriteState()
    IntentStore(path, "wallbox").load(restored)
    assert restored.pause_requested is True


@pytest.mark.parametrize(("field", "value"), [
    ("pause_requested", "yes"), ("charging_release_requested", 1),
    ("charging_setpoint", 3),
])
def test_invalid_current_format_cannot_partially_replace_control_intent(tmp_path, field, value):
    store = IntentStore(tmp_path / "intent.json", "wallbox")
    store.save(WriteState(pause_requested=False, charging_setpoint=16))
    payload = json.loads(store.path.read_text())
    payload[field] = value
    store.path.write_text(json.dumps(payload))
    existing = WriteState(pause_requested=True, charging_release_requested=False, charging_setpoint=8)
    assert store.load(existing) is False
    assert existing.pause_requested is True
    assert existing.charging_release_requested is False
    assert existing.charging_setpoint == 8
