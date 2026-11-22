"""One table says what a command is, for the service and for Home Assistant's client."""
from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.device.commands import (
    COMMANDS,
    ENVELOPE,
    needs_wallbox,
)
from custom_components.mennekes_amtron_rtu.device.errors import DeviceValidationError
from tests.test_modbus_service import current_writes, service_fixture

SERIAL = "ABC123456789"


def test_every_command_has_exactly_one_way_to_run():
    for name, spec in COMMANDS.items():
        assert (spec.run is None) != (spec.direct is None), name
        assert not spec.fields & ENVELOPE, name
    # Only one command is taken while the saved intent is rejected.
    assert [name for name, spec in COMMANDS.items() if spec.reset] == ["acknowledge_intent_reset"]


def test_plan_data_rule_settings_and_cancelling_need_no_wallbox():
    without = {name for name in COMMANDS if not needs_wallbox(name, {})}
    assert without == {
        "cancel_pending", "soc_source", "soc_observation", "unplug_release_config",
        "departure_choice", "departure_trip", "departure_inputs", "charge_note",
    }
    # Switching the gate on closes the release; switching it off is a setting.
    assert needs_wallbox("plug_charge_config", {"value": True})
    assert not needs_wallbox("plug_charge_config", {"value": False})


def test_a_command_refuses_fields_that_are_not_its_own(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            for payload in (
                {"command": "pause", "value": True},
                {"command": "energy_goal", "value": 5, "price": 0.3},
                {"command": "set_current", "value": 10, "source": "x"},
                {"command": "soc_goal", "value": 80, "source": "x", "expected_soc_revision": "y"},
                {"command": "departure_inputs", "value": {}, "deadline": None},
            ):
                with pytest.raises(ValueError, match="unknown command field"):
                    await service.command({**payload, "expected_serial_number": SERIAL})
            for payload in ({"command": "fly"}, {"command": 7}, {}, {"command": ["pause"]}):
                with pytest.raises(ValueError, match="unknown command"):
                    await service.command({**payload, "expected_serial_number": SERIAL})
            with pytest.raises(ValueError, match="expected_serial_number is required"):
                await service.command({"command": "pause"})
            assert not current_writes(transport)
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize(("payload", "error"), [
    ({"command": "energy_goal", "value": 2000}, "energy_goal_invalid"),
    ({"command": "energy_goal", "value": -1}, "energy_goal_invalid"),
    ({"command": "cost_goal", "value": 0.05, "price": 0.3, "currency": "EUR"}, "cost_goal_invalid"),
    ({"command": "set_current", "value": -1}, "charging_current_invalid"),
    ({"command": "set_current", "value": 3}, "charging_current_invalid"),
])
def test_a_number_that_is_not_permitted_is_refused_by_name(tmp_path, payload, error):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            with pytest.raises(DeviceValidationError, match=error):
                await service.command({**payload, "expected_serial_number": SERIAL})
            with pytest.raises(ValueError, match="must be a number"):
                await service.command({**payload, "value": "7", "expected_serial_number": SERIAL})
            with pytest.raises(ValueError, match="must be a number"):
                await service.command({**payload, "value": 10**400, "expected_serial_number": SERIAL})
            assert not current_writes(transport)
        finally:
            await service.stop()

    asyncio.run(run())


def test_a_grant_that_ends_a_pause_takes_only_a_true_or_false(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            for end_pause in ("yes", 1, None):
                with pytest.raises(ValueError, match="invalid Plug & Charge authorization"):
                    await service.command({
                        "command": "plug_charge_authorize", "session": "a" * 32,
                        "deadline": "2026-01-01T00:00:00+00:00", "end_pause": end_pause,
                        "expected_serial_number": SERIAL,
                    })
            assert not current_writes(transport)
        finally:
            await service.stop()

    asyncio.run(run())


def test_ending_the_targets_stop_pause_leaves_any_other_pause(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            service.control.pause_requested = True
            await service.command({"command": "soc_stop_pause_end", "expected_serial_number": SERIAL})
            assert service.control.pause_requested is True
            assert not current_writes(transport)
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize(("status", "ends"), [
    ("paused_until_start", False), ("queued", False), ("charging", False), ("error", False),
    ("waiting_for_soc", True), ("disabled", True),
])
def test_an_armed_plan_keeps_a_pause_a_grant_would_end(tmp_path, monkeypatch, status, ends):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            monkeypatch.setattr(service.planner.controller, "status", status)
            seen = []

            async def authorize(session, *, deadline, end_pause):
                seen.append(end_pause)

            monkeypatch.setattr(service.control, "async_authorize_plug_charge", authorize)
            await service.command({
                "command": "plug_charge_authorize", "session": "a" * 32,
                "deadline": "2026-01-01T00:00:00+00:00", "end_pause": True,
                "expected_serial_number": SERIAL,
            })
            assert seen == [ends]
        finally:
            await service.stop()

    asyncio.run(run())


def test_rejected_cost_change_keeps_the_amount_price_and_currency(tmp_path):
    async def run():
        service, _, _ = await service_fixture(tmp_path)
        try:
            async def command(name, **fields):
                return await service.command({"command": name, "expected_serial_number": SERIAL, **fields})

            with pytest.raises(DeviceValidationError, match="cost_goal_price_unknown"):
                await command("cost_goal", value=10, price=0.25)
            await command("cost_goal", value=10, price=0.25, currency="EUR")
            goal = service.control.energy_goal
            original = goal.snapshot()
            with pytest.raises(DeviceValidationError):
                await command("cost_goal", value=0.05, price=0.50, currency="CHF")
            assert service.goal.currency == "EUR"
            with pytest.raises(DeviceValidationError):
                await command("cost_price", price=0.50, currency="CHF")
            assert goal.snapshot() == original
            assert service.goal.price == 0.25
            assert goal.restore(original).cost.currency == "EUR"
            await command("cost_goal", value=0, currency="CHF")
            await command("cost_price", price=0.50, currency="CHF")
            assert service.goal.currency == "CHF"
        finally:
            await service.stop()

    asyncio.run(run())
