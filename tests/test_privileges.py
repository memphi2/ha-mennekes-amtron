"""The service holds no capability from its start."""

from __future__ import annotations

import ctypes
import errno
import logging
import struct

from custom_components.mennekes_amtron_rtu.device import privileges, server


class Libc:
    def __init__(self, failing=()):
        self.calls, self.failing = [], failing

    def _result(self, name):
        if name not in self.failing:
            return 0
        # A refusal sets errno, as the real call does: the error is then
        # this one, whatever an earlier call left behind.
        ctypes.set_errno(errno.EPERM)
        return -1

    def prctl(self, *arguments):
        self.calls.append(("prctl", arguments))
        return self._result("prctl")

    def capset(self, header, data):
        self.calls.append(("capset", struct.unpack("=Ii", header), data))
        return self._result("capset")


def test_all_capability_sets_are_emptied_and_none_can_be_gained(monkeypatch, caplog):
    libc = Libc()
    monkeypatch.setattr(ctypes, "CDLL", lambda *_a, **_k: libc)
    privileges.drop_capabilities()
    assert libc.calls == [
        ("prctl", (38, 1, 0, 0, 0)),
        ("capset", (0x20080522, 0), bytes(24)),
    ]
    assert not caplog.records


def test_a_refused_drop_is_reported_and_the_service_still_starts(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    # Left over from something before: it must not decide the error.
    ctypes.set_errno(errno.ENOENT)
    for failing in ("prctl", "capset"):
        monkeypatch.setattr(ctypes, "CDLL", lambda *_a, failing=failing, **_k: Libc((failing,)))
        privileges.drop_capabilities()
    monkeypatch.setattr(ctypes, "CDLL", lambda *_a, **_k: object())
    privileges.drop_capabilities()
    assert caplog.messages == [
        "Service: privileges kept (PermissionError)", "Service: privileges kept (PermissionError)",
        "Service: privileges kept (AttributeError)",
    ]


def test_other_systems_have_nothing_to_drop(monkeypatch):
    monkeypatch.setattr(privileges.sys, "platform", "darwin")
    monkeypatch.setattr(ctypes, "CDLL", lambda *_a, **_k: 1 / 0)
    privileges.drop_capabilities()


def test_the_service_drops_them_before_it_reads_anything(monkeypatch, tmp_path):
    order = []
    monkeypatch.setattr(server, "drop_capabilities", lambda: order.append("drop"))
    monkeypatch.setattr(server.sys, "argv", ["service", "--config", str(tmp_path / "missing.json")])
    try:
        server.main()
    except SystemExit as stop:
        order.append(stop.code)
    assert order == ["drop", 2]
