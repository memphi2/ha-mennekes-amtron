"""Passive service presentation must not become another command source."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from custom_components.mennekes_amtron_rtu.device import app_settings as ui
from custom_components.mennekes_amtron_rtu.device.command_events import CommandEvents
from custom_components.mennekes_amtron_rtu.device.service_overview import (
    overview_snapshot,
    service_overview,
)
from tests.test_app_settings import HEADERS, FixtureSettings
from tests.test_modbus_service import service_fixture


def snapshot():
    events = CommandEvents()
    events.record("executed", "pause")
    events.record("uncertain", "resume", "connection_error")
    return {
        "available": True, "identity": {"layout_version": 259, "serial_number": "private-serial"},
        "values": {"evse_state": 5, "cp_state": 28, "error_code": 0,
                   "lock_evse": 0, "charging_release": 1, "charging_current_ems": 16.0,
                   "authorization_status": 1, "power_total": 11040.0, "session_energy": 12.5,
                   "serial_number": "private-serial"},
        "control": {"read_only": False, "pause_requested": False,
                    "energy_goal": {"target_kwh": 20}, "soc_goal": {"target_percent": 75},
                    "plug_charge": {"enabled": True, "authorized": True, "session": "private-token"}},
        "heartbeat": {"healthy": True}, "command_events": events.snapshot(),
    }


def test_overview_is_allowlisted_and_newest_first():
    raw = snapshot()
    before = deepcopy(raw)
    result = overview_snapshot(raw)
    assert result["state"] == "charging"
    assert result["heartbeat"] == "ok"
    assert result["measurements"] == {"power_w": 11040, "session_kwh": 12.5, "current_limit_a": 16}
    assert result["targets"] == {"energy_kwh": 20, "soc_percent": 75}
    assert [row["sequence"] for row in result["commands"]] == [2, 1]
    assert "private" not in json.dumps(result)
    assert "boot_id" not in json.dumps(result)
    assert raw == before


@pytest.mark.parametrize("condition", ["setup", "starting", "offline", "held"])
def test_unknown_or_old_measurements_are_not_live(condition):
    raw = snapshot()
    if condition == "setup":
        result = overview_snapshot(None)
        expected = "setup_required"
    elif condition == "starting":
        result = overview_snapshot(raw, ready=False)
        expected = "service_starting"
    elif condition == "offline":
        raw["available"] = False
        result = overview_snapshot(raw)
        expected = "wallbox_unavailable"
    else:
        result = overview_snapshot(raw, held=frozenset(raw["values"]))
        expected = "state_unknown"
    assert result["reason"] == expected
    assert all(value is None for value in result["measurements"].values())


@pytest.mark.parametrize(("change", "expected"), [
    ({"pause_requested": True}, "pause_requested"),
    ({"charging_release_requested": False}, "release_stop_requested"),
    ({"plug_charge": {"enabled": True, "authorized": False}}, "plug_charge_waiting"),
])
def test_intent_is_not_reported_as_observed_stop(change, expected):
    raw = snapshot()
    raw["control"].update(change)
    result = overview_snapshot(raw)
    assert result["state"] == "charging"
    assert result["reason"] == expected
    assert overview_snapshot(raw, goal_stop=True)["reason"] == "goal_stop_requested"


@pytest.mark.parametrize("value", [None, True, -1, float("nan"), float("inf"), "12", 10**1000])
def test_unusable_measurements_are_not_displayed(value):
    raw = snapshot()
    raw["values"]["power_total"] = value
    assert overview_snapshot(raw)["measurements"]["power_w"] is None


@pytest.mark.parametrize("stamp", [None, "invalid", "2026-11-12T20:00:00", "x" * 65])
def test_pending_time_requires_a_real_timezone(stamp):
    raw = snapshot()
    raw["control"].update(pending_action="pause", pending_until=stamp)
    assert overview_snapshot(raw)["pending"] == {"action": "pause", "not_before": None}


def test_real_service_overview_does_not_poll_write_or_save(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            await service.heartbeat.async_stop()
            before = (list(transport.reads), list(transport.writes))
            assert (await service_overview(service))["configured"]
            assert (await service_overview(service))["configured"]
            assert (transport.reads, transport.writes) == before
            assert (await service_overview(None))["reason"] == "setup_required"
        finally:
            await service.stop()
    asyncio.run(run())


def test_ingress_overview_requires_admin_and_never_accepts_writes(monkeypatch):
    async def run():
        settings = FixtureSettings()
        settings._overview = AsyncMock(return_value=overview_snapshot(snapshot()))
        async with TestClient(TestServer(ui.create_settings_app(settings))) as client:
            assert (await client.get("/api/overview", headers=HEADERS)).status == 403
            settings._overview.assert_not_awaited()
            monkeypatch.setattr(ui, "SUPERVISOR_PEER", "127.0.0.1")
            assert (await client.get("/api/overview")).status == 403
            response = await client.get("/api/overview", headers=HEADERS)
            assert response.status == 200
            assert (await response.json())["state"] == "charging"
            assert response.headers["Cache-Control"] == "no-store"
            assert (await client.post("/api/overview", headers=HEADERS, json={})).status == 405
            assert (await client.get("/api/overview?refresh=1", headers=HEADERS)).status == 400
            settings._overview = None
            assert (await client.get("/api/overview", headers=HEADERS)).status == 503
            assert not any(method == "POST" for method, _path, _payload in settings.calls)
    asyncio.run(run())


def test_the_services_summary_is_passed_on_only_when_it_holds_up():
    summary = {
        "controller": "charging_job", "waiting": "until_job_start",
        "next": {"event": "job_start", "at": "2026-11-14T02:00:00+00:00"},
        "target": {"kind": "energy", "value": 12.0, "deadline": "2026-11-14T06:00:00+00:00"},
        "vehicle": "connected",
    }
    raw = snapshot()
    assert overview_snapshot(raw)["operation"] is None
    raw["control"]["operation"] = summary
    assert overview_snapshot(raw)["operation"] == summary
    # Until the service has started there is nothing to summarize.
    assert overview_snapshot(raw, ready=False)["operation"] is None
    assert overview_snapshot(None)["operation"] is None
    for broken in ({**summary, "controller": "private-serial"}, {**summary, "source": "x"}, "text"):
        raw["control"]["operation"] = broken
        assert overview_snapshot(raw)["operation"] is None
