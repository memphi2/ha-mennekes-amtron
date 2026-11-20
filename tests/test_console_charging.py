"""Admin app controls share durable HA choices without arbitrary service writes."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from aiohttp.test_utils import TestClient, TestServer

from custom_components.mennekes_amtron_rtu.device import app_settings
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.console_http import (
    create_console_app,
)
from tests import service_guest_fixture as runtime
from tests.test_departure_planner import CAR, hand_over
from tests.test_service_charge_http import HEADERS, TOKEN, Console


def test_console_targets_auth_cas_and_vehicle_maximum(tmp_path, monkeypatch):
    async def body():
        monkeypatch.setattr(app_settings, "SUPERVISOR_PEER", "127.0.0.1")
        async with (
            runtime.guest_service(tmp_path) as (service, _transport, _clock),
            TestClient(TestServer(create_console_app(Console("supervisor-fixture", TOKEN, service=service)))) as client,
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
            above = await client.post("/api/charging", headers=headers, json=choice)
            assert (above.status, (await above.json())["error"]) == (409, "soc_goal_invalid")
            # The top of the scale is allowed and means the car's limit.
            choice.update(value=75, revision=service.choice_revision)
            assert (await client.post("/api/charging", headers=headers, json=choice)).status == 200
            assert (service.control.soc_goal.target_percent, service.control.soc_goal.chosen_percent) == (75, 100)
            reading = service.soc_choice._reading
            choice.update(value=70, revision=service.choice_revision)
            saved = await client.post("/api/charging", headers=headers, json=choice)
            assert saved.status == 200
            assert service.soc_choice._reading == reading  # An app edit cannot refresh old vehicle telemetry.
            assert service.control.soc_goal.target_percent == 70
            assert service.control.soc_goal.maximum_percent == 75
            assert service.control.soc_goal.source == CAR
            for bad in [{**choice, "source": "foreign"}, {**choice, "confirm": False}, {**choice, "action": "write_register"}]:
                assert (await client.post("/api/charging", headers=headers, json=bad)).status == 400
    asyncio.run(body())


def test_console_departure_is_saved_separately_and_rejects_stale_choice(tmp_path, monkeypatch):
    async def body():
        monkeypatch.setattr(app_settings, "SUPERVISOR_PEER", "127.0.0.1")
        async with runtime.guest_service(tmp_path) as (service, _transport, _clock):
            await hand_over(service)
            async with TestClient(TestServer(create_console_app(Console("supervisor-fixture", TOKEN, service=service)))) as client:
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
                assert service.guest.scope is None
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
        async with (
            runtime.guest_service(tmp_path) as (service, _transport, _clock),
            TestClient(TestServer(create_console_app(Console("supervisor-fixture", TOKEN, service=service)))) as client,
        ):
            response = await client.get("/api/charging", headers=HEADERS)
            state = await response.json()
            headers = {**HEADERS, "X-Amtron-CSRF": state["csrf"]}
            assert (await client.get("/api/charging?force=1", headers=HEADERS)).status == 400
            choice = {"action": "energy", "value": 10, "revision": state["revision"], "confirm": True}
            for patch in [{"revision": 12}, {"value": True}, {"value": {}}, {"action": []}, {"action": "departure", "value": "bad-date"}]:
                assert (await client.post("/api/charging", headers=headers, json=choice | patch)).status == 400
            # A number that is not a permitted target is a refusal with a name.
            response = await client.post("/api/charging", headers=headers, json=choice | {"value": -1})
            assert (response.status, (await response.json())["error"]) == (409, "energy_goal_invalid")
            assert (await client.post("/api/charging?force=1", headers=headers, json=choice)).status == 400
            service.control.startup_hold = True
            assert (await client.post("/api/charging", headers=headers, json=choice)).status == 409
            service.control.startup_hold = False
            service.stopping = True
            departure = {**choice, "action": "departure", "value": None}
            assert (await client.post("/api/charging", headers=headers, json=departure)).status == 409
            service.stopping = False
            control, service.control = service.control, None
            try:
                assert (await client.get("/api/charging", headers=HEADERS)).status == 503
            finally:
                service.control = control
    asyncio.run(body())


def test_service_refuses_missing_cap_policy_and_malformed_handover(tmp_path, monkeypatch):

    from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
    async def body():
        async with runtime.guest_service(tmp_path) as (service, _transport, _clock):
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
        async with runtime.guest_service(tmp_path) as (service, transport, _clock):
            serial = service.identity.serial_number
            async def command(**payload):
                return await service.command({"expected_serial_number": serial, **payload})
            await command(command="soc_source", source=CAR, maximum_required=False)
            await command(command="soc_goal", source=CAR, value=90, soc=80, maximum=None)
            reading = service.soc_choice._reading
            assert reading is not None
            if replug:
                put(transport.bank, registers.EVSE_STATE, 1)
                put(transport.bank, registers.CP_STATE, 10)
                await service.refresh()
                assert service.soc_choice._reading is None
                put(transport.bank, registers.EVSE_STATE, 2)
                put(transport.bank, registers.CP_STATE, 11)
                await service.refresh()
                assert service.soc_choice._scope() not in (None, reading[3])
                # Even a retained old value cannot pass the scope check.
                service.soc_choice._reading = reading
            await command(command="soc_goal_choice", value=70, expected_choice_revision=service.choice_revision)
            assert service.control.soc_goal.status == ("active" if replug else "reached")
    asyncio.run(body())


def test_guest_console_requires_csrf_and_uses_one_shared_start(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(app_settings, "SUPERVISOR_PEER", "127.0.0.1")
        async with runtime.guest_service(tmp_path) as (service, _transport, _clock), TestClient(
            TestServer(create_console_app(Console("supervisor-fixture", TOKEN, service=service)))
        ) as client:
            state = await (await client.get("/api/charging", headers=HEADERS)).json()
            headers = {**HEADERS, "X-Amtron-CSRF": state["csrf"]}
            payload = {"action":"guest", "value":True, "revision":state["guest_revision"], "confirm":True}
            assert (await client.post("/api/charging", headers=HEADERS, json=payload)).status == 403
            assert service.guest.scope is None
            assert (await client.post("/api/charging", headers=headers, json={**payload,"value":False})).status == 400
            response = await client.post("/api/charging", headers=headers, json=payload)
            assert response.status == 200
            assert (await response.json())["guest_active"] is True
            scope = service.guest.scope
            assert (await client.post("/api/charging", headers=headers, json=payload)).status == 409
            assert service.guest.scope == scope
            fresh = await (await client.get("/api/charging", headers=HEADERS)).json()
            pause = {"action":"pause", "value":True, "revision":fresh["revision"], "confirm":True}
            assert (await client.post("/api/charging", headers=headers, json={**pause, "value":False})).status == 400
            assert (await client.post("/api/charging", headers=HEADERS, json=pause)).status == 403
            assert (await client.post("/api/charging", headers=headers, json=pause)).status == 200
            assert service.control.pause_requested is True or service.control.pending_action is not None
            assert service.guest.scope == scope
    asyncio.run(run())


def test_console_names_definite_guest_refusals_only(tmp_path, monkeypatch):
    from custom_components.mennekes_amtron_rtu.device import registers as R
    from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
    from tests.fakes import put

    async def body():
        monkeypatch.setattr(app_settings, "SUPERVISOR_PEER", "127.0.0.1")
        async with (
            runtime.guest_service(tmp_path) as (service, transport, _clock),
            TestClient(TestServer(create_console_app(Console("supervisor-fixture", TOKEN, service=service)))) as client,
        ):
            put(transport.bank, R.LOCK_EVSE, 1)
            await service.refresh()
            state = await (await client.get("/api/charging", headers=HEADERS)).json()
            headers = {**HEADERS, "X-Amtron-CSRF": state["csrf"]}
            guest = {"action": "guest", "value": True, "revision": state["guest_revision"], "confirm": True}
            refused = await client.post("/api/charging", headers=headers, json=guest)
            assert refused.status == 409
            assert (await refused.json())["error"] == "guest_start_not_permitted"

            async def lost(_payload):
                raise DeviceError("service_unavailable")

            monkeypatch.setattr(service, "command", lost)
            unknown = await client.post("/api/charging", headers=headers, json=guest)
            assert (await unknown.json())["error"] == "charging_save_uncertain"
    asyncio.run(body())


def test_console_reports_a_guest_failure_after_a_write_as_uncertain(tmp_path, monkeypatch):
    from custom_components.mennekes_amtron_rtu.device.errors import device_error

    async def body():
        monkeypatch.setattr(app_settings, "SUPERVISOR_PEER", "127.0.0.1")
        async with (
            runtime.guest_service(tmp_path) as (service, _transport, _clock),
            TestClient(TestServer(create_console_app(Console("supervisor-fixture", TOKEN, service=service)))) as client,
        ):
            state = await (await client.get("/api/charging", headers=HEADERS)).json()
            headers = {**HEADERS, "X-Amtron-CSRF": state["csrf"]}

            async def released_then_stopped(_payload):
                raise device_error("guest_start_uncertain")  # The release write already applied.

            monkeypatch.setattr(service, "command", released_then_stopped)
            guest = {"action": "guest", "value": True, "revision": state["guest_revision"], "confirm": True}
            response = await client.post("/api/charging", headers=headers, json=guest)
            assert (await response.json())["error"] == "charging_save_uncertain"

            # A connection lost while reading the write back, or a disk that
            # fails afterwards, is as uncertain: no bare server error.
            for failure in (AmtronConnectionError("lost"), OSError("full")):
                async def failed(_payload, failure=failure):
                    raise failure

                monkeypatch.setattr(service, "command", failed)
                response = await client.post("/api/charging", headers=headers, json=guest)
                assert (response.status, (await response.json())["error"]) == (503, "charging_save_uncertain")
                assert response.headers["Cache-Control"] == "no-store"
    asyncio.run(body())


def test_app_names_a_hint_for_exactly_the_definite_refusals():
    import re
    from pathlib import Path

    from custom_components.mennekes_amtron_rtu.device import console_http

    source = Path(console_http.__file__).with_name("app_charging.js").read_text(encoding="utf-8")
    mapping = re.search(r"const refusals=\{(.*?)\};", source, re.DOTALL).group(1)
    assert set(re.findall(r"(\w+):\"\w+\"", mapping)) == console_http._REFUSALS


def test_console_arms_a_cost_ceiling_at_the_last_reported_price(tmp_path, monkeypatch):
    async def body():
        monkeypatch.setattr(app_settings, "SUPERVISOR_PEER", "127.0.0.1")
        async with (
            runtime.guest_service(tmp_path) as (service, _transport, _clock),
            TestClient(TestServer(create_console_app(Console("supervisor-fixture", TOKEN, service=service)))) as client,
        ):
            state = await (await client.get("/api/charging", headers=HEADERS)).json()
            assert (state["cost"], state["cost_price"], state["cost_price_at"]) == (None, None, None)
            assert state["guest_confirmation_required"] is False
            headers = {**HEADERS, "X-Amtron-CSRF": state["csrf"]}
            choice = {"action": "cost", "value": 10, "revision": state["revision"], "confirm": True}
            refused = await client.post("/api/charging", headers=headers, json=choice)
            assert (refused.status, (await refused.json())["error"]) == (409, "cost_goal_price_unknown")
            # Home Assistant reports the price; the app needs no price of its own.
            await service.command({
                "command": "cost_price", "price": 0.25, "currency": "CHF",
                "expected_serial_number": service.identity.serial_number,
            })
            assert service.snapshot()["control"]["cost_price"] == 0.25
            # The currency comes with the price and reaches the page.
            assert service.snapshot()["control"]["cost_currency"] == "CHF"
            assert (await (await client.get("/api/charging", headers=HEADERS)).json())["cost_currency"] == "CHF"
            for wrong in ("chf", "EURO", 7):
                with pytest.raises(ValueError, match="three-letter"):
                    await service.command({
                        "command": "cost_price", "price": 0.25, "currency": wrong,
                        "expected_serial_number": service.identity.serial_number,
                    })
            told = (await (await client.get("/api/charging", headers=HEADERS)).json())["cost_price_at"]
            assert str(told).endswith("+00:00")
            saved = await client.post("/api/charging", headers=headers, json=choice)
            state = await saved.json()
            assert saved.status == 200
            assert state["cost"] == {"amount": 10, "spent": 0.0, "price": 0.25}
            assert (state["energy"]["target"], state["cost_price"]) == (40.0, 0.25)
            wrong = await client.post("/api/charging", headers=headers, json={**choice, "value": 0.05, "revision": state["revision"]})
            assert (wrong.status, (await wrong.json())["error"]) == (409, "cost_goal_invalid")
            ended = await client.post("/api/charging", headers=headers, json={**choice, "value": 0, "revision": state["revision"]})
            state = await ended.json()
            assert (state["cost"], state["energy"]["target"]) == (None, 0)

    asyncio.run(body())


def test_console_names_a_command_that_must_wait_and_a_past_time(tmp_path, monkeypatch):
    from custom_components.mennekes_amtron_rtu.device.client_errors import (
        AmtronRateLimitedError,
    )
    from custom_components.mennekes_amtron_rtu.device.errors import validation_error

    async def body():
        monkeypatch.setattr(app_settings, "SUPERVISOR_PEER", "127.0.0.1")
        async with (
            runtime.guest_service(tmp_path) as (service, _transport, _clock),
            TestClient(TestServer(create_console_app(Console("supervisor-fixture", TOKEN, service=service)))) as client,
        ):
            state = await (await client.get("/api/charging", headers=HEADERS)).json()
            headers = {**HEADERS, "X-Amtron-CSRF": state["csrf"]}
            choice = {"action": "pause", "value": True, "revision": state["revision"], "confirm": True}
            guest = {**choice, "action": "guest", "revision": state["guest_revision"]}
            limited = AmtronRateLimitedError("state_change", 5.0)
            for request, error, expected in (
                (choice, limited, (409, "write_rate_limited")),
                # The queue refuses a guest start before its first write.
                (guest, limited, (409, "write_rate_limited")),
                (choice, validation_error("target_time_not_future"), (409, "target_time_not_future")),
            ):
                async def refuse(_payload, error=error):
                    raise error

                monkeypatch.setattr(service, "command", refuse)
                refused = await client.post("/api/charging", headers=headers, json=request)
                assert (refused.status, (await refused.json())["error"]) == expected

    asyncio.run(body())


def test_app_reads_behind_a_long_command_end_and_the_overview_shows_the_last_state(tmp_path, monkeypatch):
    from custom_components.mennekes_amtron_rtu.device import (
        console_http,
        service_runtime,
    )
    from custom_components.mennekes_amtron_rtu.device.service_overview import (
        service_overview,
    )

    # The overview's own limit must not end its patient read first.
    assert app_settings.OVERVIEW_SECONDS > service_runtime._PATIENCE_SECONDS

    async def body():
        monkeypatch.setattr(app_settings, "SUPERVISOR_PEER", "127.0.0.1")
        monkeypatch.setattr(console_http, "_READ_SECONDS", 0.05)
        async with runtime.guest_service(tmp_path) as (service, _transport, _clock):
            monkeypatch.setattr("custom_components.mennekes_amtron_rtu.device.service_runtime._PATIENCE_SECONDS", 0.05)
            console = Console(
                "supervisor-fixture", TOKEN, service=service, overview=lambda: service_overview(service),
            )
            async with TestClient(TestServer(create_console_app(console))) as client:
                assert (await client.get("/api/overview", headers=HEADERS)).status == 200
                await service.command_lock.acquire()
                try:
                    # A read that waited for the command would keep its request
                    # slot after the page gave up; two of them blocked the app.
                    for path in ("/api/charging", "/api/registers"):
                        response = await client.get(path, headers=HEADERS)
                        assert (response.status, await response.json()) == (503, {"error": "busy"})
                    # The overview waits shortly and shows the last published state.
                    assert (await client.get("/api/overview", headers=HEADERS)).status == 200
                finally:
                    service.command_lock.release()
                assert (await client.get("/api/charging", headers=HEADERS)).status == 200

    asyncio.run(body())
