"""An acknowledged intent must survive a rename and retry failed durability checks."""

from __future__ import annotations

import os
import stat

import pytest

from custom_components.mennekes_amtron_rtu.device.service_store import IntentStore
from custom_components.mennekes_amtron_rtu.device.write_state import WriteState


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
