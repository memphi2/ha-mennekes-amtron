"""Admin app controls share durable HA choices without arbitrary service writes."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from aiohttp.test_utils import TestClient, TestServer

from custom_components.mennekes_amtron_rtu.device import app_settings
from custom_components.mennekes_amtron_rtu.device.service_departure_runtime import (
    DepartureModbusService,
)
from custom_components.mennekes_amtron_rtu.device.service_job_http import (
    create_job_console,
)
from tests import test_service_charge_runtime as runtime
from tests.test_service_charge_http import HEADERS, TOKEN, Console
from tests.test_service_departure_runtime import CAR, hand_over


def test_console_targets_auth_cas_and_vehicle_maximum(tmp_path, monkeypatch):
    async def body():
        monkeypatch.setattr(app_settings, "SUPERVISOR_PEER", "127.0.0.1")
        monkeypatch.setattr(runtime, "JobModbusService", DepartureModbusService)
        async with (
            runtime.job_service(tmp_path) as (service, _transport, _clock),
            TestClient(TestServer(create_job_console(Console("supervisor-fixture", TOKEN, service=service)))) as client,
        ):
            assert (await client.get("/api/charging")).status == 403
            response = await client.get("/api/charging", headers=HEADERS)
            state = await response.json()
            assert response.status == 200
            headers = {**HEADERS, "X-Amtron-CSRF": state["csrf"]}
            choice = {"action": "energy", "value": 12, "revision": state["revision"], "confirm": True}
            assert (await client.post("/api/charging", headers=HEADERS, json=choice)).status == 403
            saved = await client.post("/api/charging", headers=headers, json=choice)
            assert saved.status == 200
            assert (await saved.json())["energy"]["target"] == 12
            assert service.control.energy_goal.target_kwh == 12
            assert (await client.post("/api/charging", headers=headers, json={**choice, "value": 30})).status == 409
            assert service.control.energy_goal.target_kwh == 12
            await service.command({"command": "soc_source", "source": CAR, "maximum_required": True, "expected_serial_number": service.identity.serial_number})
            state = await (await client.get("/api/charging", headers=HEADERS)).json()
            choice = {"action": "soc", "value": 80, "revision": state["revision"], "confirm": True}
            refused = await client.post("/api/charging", headers=headers, json=choice)
            assert refused.status == 409
            assert (await refused.json())["error"] == "soc_goal_limit_unavailable"
            await service.command({"command": "soc_observation", "source": CAR, "soc": 40, "maximum": 75, "expected_serial_number": service.identity.serial_number})
            choice["revision"] = service.choice_revision
            assert (await client.post("/api/charging", headers=headers, json=choice)).status != 200
            reading = service._soc_reading
            choice.update(value=70, revision=service.choice_revision)
            saved = await client.post("/api/charging", headers=headers, json=choice)
            assert saved.status == 200
            assert service._soc_reading == reading  # An app edit cannot refresh old vehicle telemetry.
            assert service.control.soc_goal.target_percent == 70
            assert service.control.soc_goal.maximum_percent == 75
            assert service.control.soc_goal.source == CAR
            for bad in [{**choice, "source": "foreign"}, {**choice, "confirm": False}, {**choice, "action": "write_register"}]:
                assert (await client.post("/api/charging", headers=headers, json=bad)).status == 400
    asyncio.run(body())


def test_console_departure_is_saved_separately_and_rejects_stale_choice(tmp_path, monkeypatch):
    async def body():
        monkeypatch.setattr(app_settings, "SUPERVISOR_PEER", "127.0.0.1")
        monkeypatch.setattr(runtime, "JobModbusService", DepartureModbusService)
        async with runtime.job_service(tmp_path) as (service, _transport, _clock):
            await hand_over(service)
            async with TestClient(TestServer(create_job_console(Console("supervisor-fixture", TOKEN, service=service)))) as client:
                state = await (await client.get("/api/charging", headers=HEADERS)).json()
                headers = {**HEADERS, "X-Amtron-CSRF": state["csrf"]}
                deadline = (datetime.now(UTC) + timedelta(hours=4)).isoformat()
                choice = {"action": "departure", "value": deadline, "revision": state["departure"]["revision"], "confirm": True}
                saved = await client.post("/api/charging", headers=headers, json=choice)
                assert saved.status == 200
                result = await saved.json()
                assert result["departure"]["deadline"] == deadline
                assert service.departure.editor == "app"
                assert service.departure.selection_vehicle == CAR
                assert service.departure.store.load()["trip"]["deadline"] == deadline
                assert service.control.soc_goal.target_percent == 80
                assert service.jobs.job is None
                assert (await client.post("/api/charging", headers=headers, json={**choice, "value": None})).status == 409
                choice.update(value=None, revision=result["departure"]["revision"])
                cleared = await client.post("/api/charging", headers=headers, json=choice)
                assert cleared.status == 200
                assert service.departure.trip.deadline is None
                assert service.departure.trip.source == "manual"
                assert service.control.soc_goal.target_percent == 80
    asyncio.run(body())


def test_charging_console_rejects_malformed_and_unavailable_edits(tmp_path, monkeypatch):
    async def body():
        monkeypatch.setattr(app_settings, "SUPERVISOR_PEER", "127.0.0.1")
        monkeypatch.setattr(runtime, "JobModbusService", DepartureModbusService)
        async with (
            runtime.job_service(tmp_path) as (service, _transport, _clock),
            TestClient(TestServer(create_job_console(Console("supervisor-fixture", TOKEN, service=service)))) as client,
        ):
            response = await client.get("/api/charging", headers=HEADERS)
            state = await response.json()
            headers = {**HEADERS, "X-Amtron-CSRF": state["csrf"]}
            assert (await client.get("/api/charging?force=1", headers=HEADERS)).status == 400
            choice = {"action": "energy", "value": 10, "revision": state["revision"], "confirm": True}
            for patch in [{"revision": 12}, {"value": -1}, {"value": True}, {"value": {}}, {"action": []}, {"action": "departure", "value": "bad-date"}]:
                assert (await client.post("/api/charging", headers=headers, json=choice | patch)).status == 400
            assert (await client.post("/api/charging?force=1", headers=headers, json=choice)).status == 400
            service.control.startup_hold = True
            assert (await client.post("/api/charging", headers=headers, json=choice)).status == 409
            service.control.startup_hold = False
            service._stopping = True
            departure = {**choice, "action": "departure", "value": None}
            assert (await client.post("/api/charging", headers=headers, json=departure)).status == 409
            service._stopping = False
            control, service.control = service.control, None
            try:
                assert (await client.get("/api/charging", headers=HEADERS)).status == 503
            finally:
                service.control = control
    asyncio.run(body())


def test_service_refuses_missing_cap_policy_and_malformed_handover(tmp_path, monkeypatch):

    from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
    async def body():
        monkeypatch.setattr(runtime, "JobModbusService", DepartureModbusService)
        async with runtime.job_service(tmp_path) as (service, _transport, _clock):
            serial = service.identity.serial_number
            async def command(**payload):
                return await service.command({"expected_serial_number": serial, **payload})
            for source, maximum in [(True, False), (CAR, "false")]:
                with pytest.raises(ValueError, match=r"source|maximum_required"):
                    await command(command="soc_source", source=source, maximum_required=maximum)
            with pytest.raises(DeviceError, match="soc_goal_source_changed"):
                await command(command="soc_goal_choice", value=50, expected_choice_revision=service.choice_revision)
            with pytest.raises(ValueError, match="invalid departure choice"):
                await command(command="departure_choice", deadline=None, revision=False)
            control, service.control = service.control, None
            try:
                with pytest.raises(DeviceError, match="service_unavailable"):
                    await command(command="departure_choice", deadline=None, revision="a")
            finally:
                service.control = control
    asyncio.run(body())


@pytest.mark.parametrize("replug", [False, True])
def test_app_soc_choice_uses_only_the_current_plug_in(tmp_path, monkeypatch, replug):
    from custom_components.mennekes_amtron_rtu.device import registers as registers
    from tests.fakes import put
    async def body():
        monkeypatch.setattr(runtime, "JobModbusService", DepartureModbusService)
        async with runtime.job_service(tmp_path) as (service, transport, _clock):
            serial = service.identity.serial_number
            async def command(**payload):
                return await service.command({"expected_serial_number": serial, **payload})
            await command(command="soc_source", source=CAR, maximum_required=False)
            await command(command="soc_goal", source=CAR, value=90, soc=80, maximum=None)
            reading = service._soc_reading
            assert reading is not None
            if replug:
                put(transport.bank, registers.EVSE_STATE, 1)
                put(transport.bank, registers.CP_STATE, 10)
                await service.refresh()
                assert service._soc_reading is None
                put(transport.bank, registers.EVSE_STATE, 2)
                put(transport.bank, registers.CP_STATE, 11)
                await service.refresh()
                assert service._soc_observation_scope() != reading[3]
                # Even a retained old value cannot pass the scope check.
                service._soc_reading = reading
            await command(command="soc_goal_choice", value=70, expected_choice_revision=service.choice_revision)
            assert service.control.soc_goal.status == ("active" if replug else "reached")
    asyncio.run(body())
