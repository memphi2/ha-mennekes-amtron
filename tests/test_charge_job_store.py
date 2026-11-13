"""A failed job save is never an acknowledged persistent charging permission."""

import json
import stat

import pytest

from custom_components.mennekes_amtron_rtu.device import charge_job_store as m
from custom_components.mennekes_amtron_rtu.device.persistence import StorageCommitUncertainError
from tests.test_charge_job import job


def test_missing_store_does_not_create_or_write_files(tmp_path):
    path = tmp_path / "nested" / "charge-job.json"
    store = m.ChargeJobStore(path, "wallbox")
    assert store.load() is None
    store.save(None)
    assert not path.exists()


def test_atomic_roundtrip_has_private_permissions_and_no_resume(tmp_path):
    path = tmp_path / "charge-job.json"
    store = m.ChargeJobStore(path, "wallbox")
    value = job()
    store.save(value)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    restored = m.ChargeJobStore(path, "wallbox").load()
    assert restored.reason == "service_restarted"
    assert restored.energy.baseline_kwh == 100
    before = path.stat().st_mtime_ns
    store.save(value)
    assert path.stat().st_mtime_ns == before
    store.save(None)
    assert store.load() is None
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("payload", [[], {}, {"version": True, "identity": "wallbox", "job": None}, {"version": 2, "identity": "wallbox", "job": None}, {"version": 1, "identity": "other", "job": None}])
def test_bad_identity_or_format_never_discards_a_stop(tmp_path, payload):
    path = tmp_path / "charge-job.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="identity or format"):
        m.ChargeJobStore(path, "wallbox").load()


def test_size_limit_applies_on_read_and_write(tmp_path, monkeypatch):
    path = tmp_path / "charge-job.json"
    path.write_bytes(b" " * (m.MAX_JOB_BYTES + 1))
    store = m.ChargeJobStore(path, "wallbox")
    with pytest.raises(ValueError, match="size limit"):
        store.load()
    monkeypatch.setattr(store, "_encode", lambda _job: b"x" * (m.MAX_JOB_BYTES + 1))
    with pytest.raises(ValueError, match="size limit"):
        store.save(job())


@pytest.mark.parametrize("point", ["file_sync", "replace", "directory_open", "directory_sync"])
def test_failure_cleanup_and_uncertain_commit(tmp_path, monkeypatch, point):
    path = tmp_path / "charge-job.json"
    store = m.ChargeJobStore(path, "wallbox")
    store.save(None)
    old = path.read_bytes()
    real_sync, real_open = m.os.fsync, m.os.open
    calls = 0

    def sync(fd):
        nonlocal calls
        calls += 1
        if (point == "file_sync" and calls == 1) or (point == "directory_sync" and calls == 2):
            raise OSError("simulated sync failure")
        return real_sync(fd)

    def open_directory(name, flags, *args, **kwargs):
        if point == "directory_open" and flags & m.os.O_DIRECTORY:
            raise OSError("simulated directory failure")
        return real_open(name, flags, *args, **kwargs)

    def replace(_self, _target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(m.os, "fsync", sync)
    monkeypatch.setattr(m.os, "open", open_directory)
    if point == "replace":
        monkeypatch.setattr(m.Path, "replace", replace)
    error = StorageCommitUncertainError if point.startswith("directory") else OSError
    with pytest.raises(error, match="simulated|job replaced"):
        store.save(job())
    assert not list(tmp_path.glob(".job-*"))
    if point.startswith("directory"):
        assert json.loads(path.read_text())["job"]["state"] == "scheduled"
        assert store._last is None
    else:
        assert path.read_bytes() == old
