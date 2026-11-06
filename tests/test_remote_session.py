"""Validate optional app session summaries before publishing any remote state."""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest
from aiohttp import ClientSession

from custom_components.mennekes_amtron_rtu.remote import RemoteClient
from custom_components.mennekes_amtron_rtu.remote_errors import ServiceConnectionError
from tests.test_charging_session import complete
from tests.test_modbus_service import TOKEN, service_fixture


def test_remote_session_metadata_is_optional_stable_and_detached(tmp_path):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            async with ClientSession() as session:
                client = RemoteClient(session, "http://service:8765", TOKEN)
                payload = service.snapshot()
                payload.pop("charging_session", None)
                old = client.apply_snapshot(payload)
                assert not old.session_summary_supported
                assert old.charging_session is None
                payload["charging_session"] = {"supported": True, "latest": None}
                empty = client.apply_snapshot(payload)
                assert empty.session_summary_supported
                assert empty.charging_session is None
                assert old != empty
                completed = complete().latest.as_dict()
                payload["charging_session"]["latest"] = completed
                data = client.apply_snapshot(payload)
                assert data.session_summary_supported
                assert data.charging_session == completed
                assert data == client.apply_snapshot(deepcopy(payload))
                assert empty != data
                completed["wallbox_kwh"] = 20
                assert data.charging_session["wallbox_kwh"] == 10
                payload["charging_session"] = {"supported": False, "latest": None}
                unsupported = client.apply_snapshot(payload)
                assert not unsupported.session_summary_supported
                assert unsupported.charging_session is None
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("summary", [
    None, [], {}, {"supported": True}, {"latest": None},
    {"supported": 1, "latest": None}, {"supported": "true", "latest": None},
    {"supported": True, "latest": None, "unexpected": 1},
    {"supported": True, "latest": {}}, {"supported": True, "latest": []},
    {"supported": False, "latest": complete().latest.as_dict()},
    {"supported": True, "latest": {**complete().latest.as_dict(), "wallbox_kwh": float("nan")}},
    {"supported": True, "latest": {**complete().latest.as_dict(), "wallbox_kwh": 0}},
    {"supported": True, "latest": {**complete().latest.as_dict(), "started_before": 0}},
    {"supported": True, "latest": {**complete().latest.as_dict(), "session_id": "wrong"}},
])
def test_malformed_session_metadata_rejects_snapshot_without_publishing_controls(tmp_path, summary):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            async with ClientSession() as session:
                client = RemoteClient(session, "http://service:8765", TOKEN)
                valid = service.snapshot()
                client.apply_snapshot(valid)
                retained = client.snapshot
                prior_pause = client.write_state.pause_requested
                invalid = deepcopy(valid)
                invalid["control"]["pause_requested"] = not prior_pause
                invalid["charging_session"] = summary
                with pytest.raises(ServiceConnectionError, match=r"snapshot \(charging_session\)"):
                    client.apply_snapshot(invalid)
                assert client.failure_reason == "service_invalid_response"
                assert not client.connected
                assert client.snapshot is retained
                assert client.write_state.pause_requested is prior_pause
        finally:
            await service.stop()

    asyncio.run(run())
