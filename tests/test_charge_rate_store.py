"""Advisory history remains bounded/private through failed atomic writes."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from custom_components.mennekes_amtron_rtu.device import charge_rate_store as m
from custom_components.mennekes_amtron_rtu.device.persistence import (
    StorageCommitUncertainError,
)


def rows(rate=9):
    return [{"source": "a" * 64, "current_ma": 12000, "phases": 3,
             "rates_kw": [rate, rate, rate], "updated_at": 1794787200}]


def test_roundtrip_is_private_and_unchanged_history_does_not_write(tmp_path, monkeypatch):
    store = m.RateStore(tmp_path / "rates.json", "fixture")
    assert store.load().snapshot() == []
    store.save(rows())
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.load().snapshot() == rows()
    replace = Mock(side_effect=AssertionError("unchanged history was rewritten"))
    monkeypatch.setattr(Path, "replace", replace)
    store.save(rows())
    replace.assert_not_called()
    assert not list(tmp_path.glob(".rates-*"))


@pytest.mark.parametrize("raw", ["{", "[]", "null", '{"version":true,"identity":"fixture","rates":[]}',
                                 '{"version":1,"identity":"other","rates":[]}',
                                 '{"version":2,"identity":"fixture","rates":[]}',
                                 '{"version":1,"identity":"fixture","rates":[],"resume":true}'])
def test_corrupt_or_wrong_identity_history_is_not_used(tmp_path, raw):
    store = m.RateStore(tmp_path / "rates.json", "fixture")
    store.path.write_text(raw)
    with pytest.raises(ValueError, match="rate history"):
        store.load()


def test_oversized_or_deeply_nested_history_is_rejected(tmp_path):
    store = m.RateStore(tmp_path / "rates.json", "fixture")
    store.path.write_bytes(b" " * (m.MAX_RATE_BYTES + 1))
    with pytest.raises(ValueError, match="size limit"):
        store.load()
    store.path.write_text("[" * 2000 + "]" * 2000)
    with pytest.raises(ValueError, match=r"rate history (JSON|identity or format)"):
        store.load()


def test_encoding_bound_prevents_file_changes(tmp_path, monkeypatch):
    store = m.RateStore(tmp_path / "rates.json", "fixture")
    monkeypatch.setattr(m, "MAX_RATE_BYTES", 10)
    with pytest.raises(ValueError, match="size limit"):
        store.save(rows())
    assert not store.path.exists()


@pytest.mark.parametrize("phase", ["file_sync", "replace", "directory_open", "directory_sync"])
def test_atomic_failure_never_claims_a_new_durable_history(tmp_path, monkeypatch, phase):
    store = m.RateStore(tmp_path / "rates.json", "fixture")
    store.save(rows())
    original_fsync, original_open = m.os.fsync, m.os.open
    count = 0

    def fsync(fd):
        nonlocal count
        count += 1
        if (phase == "file_sync" and count == 1) or (phase == "directory_sync" and count == 2):
            raise OSError("fixture sync failure")
        return original_fsync(fd)

    def open_file(path, flags, *args, **kwargs):
        if phase == "directory_open" and flags & m.os.O_DIRECTORY:
            raise OSError("fixture directory failure")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(m.os, "fsync", fsync)
    monkeypatch.setattr(m.os, "open", open_file)
    if phase == "replace":
        monkeypatch.setattr(Path, "replace", Mock(side_effect=OSError("fixture replace failure")))
    expected = StorageCommitUncertainError if phase.startswith("directory") else OSError
    with pytest.raises(expected, match=r"fixture|directory sync"):
        store.save(rows(10))
    persisted = json.loads(store.path.read_text())["rates"]
    assert persisted == (rows(10) if phase.startswith("directory") else rows())
    if phase.startswith("directory"):
        assert store._last is None
    assert not list(tmp_path.glob(".rates-*"))


def test_json_recursion_failure_is_reported_without_raw_decoder_text(tmp_path, monkeypatch):
    store = m.RateStore(tmp_path / "rates.json", "fixture")
    store.path.write_text("[]")
    monkeypatch.setattr(m.json, "loads", Mock(side_effect=RecursionError("private decoder detail")))
    with pytest.raises(ValueError, match="invalid rate history JSON"):
        store.load()
