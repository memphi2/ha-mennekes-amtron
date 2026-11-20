"""One table says what a command is, for the service and for Home Assistant's client."""
from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.device.commands import (
    COMMANDS,
    ENVELOPE,
    needs_wallbox,
)
from tests.test_modbus_service import service_fixture

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
            assert not transport.writes
        finally:
            await service.stop()

    asyncio.run(run())
