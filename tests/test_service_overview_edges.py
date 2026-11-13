"""Overview edge cases retain unknown states and the existing access boundary."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.mennekes_amtron_rtu.device.service_overview import (
    overview_snapshot,
    service_overview,
)
from tests.test_app_settings import FixtureSettings, body, request
from tests.test_service_overview import snapshot


@pytest.mark.parametrize(("read_only", "healthy", "expected"), [
    (True, True, "disabled"), (True, False, "disabled"),
    (False, False, "problem"), (False, None, "unknown"),
    (None, None, "unknown"), ("true", None, "unknown"),
])
def test_heartbeat_state_is_not_assumed_from_process_liveness(read_only, healthy, expected):
    raw = snapshot()
    raw["control"]["read_only"] = read_only
    raw["heartbeat"]["healthy"] = healthy
    result = overview_snapshot(raw)
    assert result["heartbeat"] == expected
    assert result["read_only"] is (read_only if type(read_only) is bool else None)


def test_pending_time_retains_timezone_and_unknown_actions_are_not_exposed():
    raw = snapshot()
    stamp = "2026-11-12T22:00:00+01:00"
    raw["control"].update(pending_action="pause", pending_until=stamp)
    assert overview_snapshot(raw)["pending"] == {"action": "pause", "not_before": stamp}
    raw["control"]["pending_action"] = "unrecognized-action-with-private-data"
    assert overview_snapshot(raw)["pending"] is None


@pytest.mark.parametrize("field", ["control", "heartbeat", "identity", "values"])
def test_missing_optional_mappings_do_not_leak_untrusted_values(field):
    raw = snapshot()
    raw[field] = "private-unavailable-field"
    result = overview_snapshot(raw)
    assert "private" not in str(result)
    if field == "values":
        assert result["state"] == "unknown"
        assert result["reason"] == "state_unknown"


def test_manual_grant_and_disabled_gate_do_not_claim_vehicle_detection_is_pending():
    raw = snapshot()
    raw["control"]["plug_charge"] = {"enabled": True, "authorized": False}
    raw["control"]["plug_charge_manual"] = True
    assert overview_snapshot(raw)["reason"] == "charging"
    raw["control"]["plug_charge_manual"] = False
    raw["control"]["plug_charge"]["enabled"] = False
    assert overview_snapshot(raw)["reason"] == "charging"


def test_service_without_control_is_starting_not_live():
    async def run():
        service = SimpleNamespace(
            stable_snapshot=AsyncMock(return_value=snapshot()), control=None,
            data=SimpleNamespace(held=frozenset()),
        )
        result = await service_overview(service)
        assert result["reason"] == "service_starting"
        assert result["available"] is False
        assert all(value is None for value in result["measurements"].values())
        service.stable_snapshot.assert_awaited_once_with()
    asyncio.run(run())


def test_overview_timeout_is_sanitized_without_writing_configuration():
    async def run():
        settings = FixtureSettings()
        settings._overview = AsyncMock(side_effect=TimeoutError("private-timeout-detail"))
        response = await settings.guard(request(), settings.read_overview)
        assert response.status == 503
        assert body(response) == {"error": "overview_unavailable"}
        assert not any(method == "POST" for method, _path, _payload in settings.calls)
    asyncio.run(run())
