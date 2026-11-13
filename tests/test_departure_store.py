"""The departure plan file: bounded, bound to the wallbox, replaced atomically."""

from __future__ import annotations

import json
import os

import pytest

from custom_components.mennekes_amtron_rtu.device import departure_store as store_module
from custom_components.mennekes_amtron_rtu.device import persistence
from custom_components.mennekes_amtron_rtu.device.departure_store import (
    MAX_PLAN_BYTES,
    DepartureStore,
)
from custom_components.mennekes_amtron_rtu.device.persistence import (
    StorageCommitUncertainError,
)


def test_a_plan_round_trips_privately_and_unchanged_saves_skip_the_disk(tmp_path, monkeypatch):
    path = tmp_path / "departure-plan.json"
    store = DepartureStore(path, "fixture")
    assert store.load() is None
    store.save(None)
    assert not path.exists()
    store.save({"trip": 1})
    assert DepartureStore(path, "fixture").load() == {"trip": 1}
    assert path.stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(store_module, "replace_file", lambda *_a, **_k: pytest.fail("rewrote an unchanged plan"))
    store.save({"trip": 1})
    reader = DepartureStore(path, "fixture")
    reader.load()
    reader.save({"trip": 1})
    assert not list(tmp_path.glob(".departure-*"))


@pytest.mark.parametrize(("content", "message"), [
    (b"x" * (MAX_PLAN_BYTES + 1), "size limit"), (b"{", "JSON"), (b"[]", "format"),
    (b'{"version":2,"identity":"fixture","plan":null}', "format"),
    (b'{"version":true,"identity":"fixture","plan":null}', "format"),
    (b'{"version":1,"identity":"other","plan":null}', "identity"),
    (b'{"version":1,"identity":"fixture","plan":[]}', "format"),
    (b'{"version":1,"identity":"fixture"}', "format"),
])
def test_a_foreign_or_damaged_file_is_refused(tmp_path, content, message):
    path = tmp_path / "departure-plan.json"
    path.write_bytes(content)
    with pytest.raises(ValueError, match=message):
        DepartureStore(path, "fixture").load()


def test_an_oversized_plan_is_not_written(tmp_path):
    path = tmp_path / "departure-plan.json"
    with pytest.raises(ValueError, match="size limit"):
        DepartureStore(path, "fixture").save({"source": "x" * MAX_PLAN_BYTES})
    assert not path.exists()


def test_an_unconfirmed_directory_sync_writes_again(tmp_path, monkeypatch):
    path = tmp_path / "departure-plan.json"
    store = DepartureStore(path, "fixture")
    real_open = os.open

    def no_directory(name, flags, *args):
        if flags & os.O_DIRECTORY:
            raise OSError("sync refused")
        return real_open(name, flags, *args)

    monkeypatch.setattr(persistence.os, "open", no_directory)
    with pytest.raises(StorageCommitUncertainError):
        store.save({"trip": 2})
    assert json.loads(path.read_bytes())["plan"] == {"trip": 2}
    monkeypatch.undo()
    writes = []
    monkeypatch.setattr(store_module, "replace_file", lambda *args, **kwargs: writes.append(args))
    store.save({"trip": 2})
    assert len(writes) == 1
    assert not list(tmp_path.glob(".departure-*"))
