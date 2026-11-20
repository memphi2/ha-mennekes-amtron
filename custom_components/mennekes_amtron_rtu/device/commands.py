"""Every command the service takes: its fields, whether it needs the wallbox, and what runs it.

The service dispatches from this table and Home Assistant's client reads it,
so both agree on what a command is without a list of their own.
"""

from __future__ import annotations

# The handlers are parts of the one service.
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .enums import RequestedPhases, SolarChargingMode
from .errors import validation_error
from .service_config import integer

if TYPE_CHECKING:
    from .control import AmtronControl
    from .service_runtime import ModbusService

Handler = Callable[["ModbusService", "AmtronControl", dict[str, Any]], Awaitable[None]]
Direct = Callable[["ModbusService", dict[str, Any]], Awaitable[dict[str, Any]]]
# What every command carries besides its own fields.
ENVELOPE = frozenset({"command", "expected_serial_number"})


@dataclass(frozen=True, slots=True)
class Command:
    """One command of the service."""

    # The fields it takes besides the envelope.
    fields: frozenset[str] = frozenset()
    # Runs under the command lock, after the checks every command shares.
    run: Handler | None = None
    # Does everything itself: its own lock, checks and answer.
    direct: Direct | None = None
    # False for plan data, rule settings and cancelling, which are accepted
    # while the wallbox does not answer and need no poll afterwards.
    wallbox: bool = True
    # The one command a rejected saved intent still takes.
    reset: bool = False

    def refuses(self, payload: Mapping[str, Any]) -> bool:
        """Whether the payload carries a field this command does not take."""
        return bool(payload.keys() - self.fields - ENVELOPE)


def _fields(*names: str) -> frozenset[str]:
    return frozenset(names)


def _real(value: object) -> float:
    """A number at all. Whether it is a permitted one, the controller says:
    a wrong value is the caller's error with a name, not a malformed request."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("value must be a number")
    try:
        return float(value)
    except OverflowError:
        raise ValueError("value must be a number") from None


async def _plug_charge_config(_service: ModbusService, control: AmtronControl, payload: dict[str, Any]) -> None:
    enabled, policy = payload.get("value"), payload.get("policy", "")
    if type(enabled) is not bool or not isinstance(policy, str):
        raise ValueError("invalid Plug & Charge configuration")
    await control.async_configure_plug_charge(enabled, policy=policy)


async def _unplug_release_config(service: ModbusService, _control: AmtronControl, payload: dict[str, Any]) -> None:
    # A setting of the rule only: nothing is written to the wallbox.
    service.client.write_state.unplug_release.configure(payload.get("value"))


async def _plug_charge_authorize(_service: ModbusService, control: AmtronControl, payload: dict[str, Any]) -> None:
    session, deadline = payload.get("session"), payload.get("deadline")
    if not isinstance(session, str) or not isinstance(deadline, str) or len(deadline) > 64:
        raise ValueError("invalid Plug & Charge authorization")
    await control.async_authorize_plug_charge(session, deadline=datetime.fromisoformat(deadline))


async def _cancel_pending(_service: ModbusService, control: AmtronControl, _payload: dict[str, Any]) -> None:
    await control.async_cancel_pending_change()


async def _set_current(_service: ModbusService, control: AmtronControl, payload: dict[str, Any]) -> None:
    unlimited = payload.get("allow_unlimited", False)
    if not isinstance(unlimited, bool):
        raise ValueError("allow_unlimited must be boolean")
    await control.async_set_charging_current(_real(payload.get("value")), allow_unlimited=unlimited)


async def _pause(_service: ModbusService, control: AmtronControl, _payload: dict[str, Any]) -> None:
    await control.async_pause_charging()


async def _energy_goal(_service: ModbusService, control: AmtronControl, payload: dict[str, Any]) -> None:
    await control.async_set_energy_goal(_real(payload.get("value")))


async def _cost(_service: ModbusService, control: AmtronControl, payload: dict[str, Any]) -> None:
    # Home Assistant knows the price, the service the meter: the amount and
    # its target live here, in one place.
    if control.goal_controller is None or not control.supports_energy_goal:
        raise validation_error("energy_goal_not_supported")
    price = payload.get("price")
    control.goal_controller.note_currency(payload.get("currency"))
    if payload["command"] == "cost_goal":
        await control.goal_controller.async_set_cost_target(_real(payload.get("value")), price)
    else:
        await control.goal_controller.async_set_price(price)


async def _soc_source(service: ModbusService, control: AmtronControl, payload: dict[str, Any]) -> None:
    await service.soc_choice.configure(control, payload)


async def _soc(service: ModbusService, control: AmtronControl, payload: dict[str, Any]) -> None:
    await service.soc_choice.apply(control, payload["command"], payload)


async def _resume(service: ModbusService, control: AmtronControl, payload: dict[str, Any]) -> None:
    await control.async_resume_charging(
        current_cap=_real(payload.get("current_cap", service.config.current_cap)),
    )


async def _switch(_service: ModbusService, control: AmtronControl, payload: dict[str, Any]) -> None:
    value = payload.get("value")
    if not isinstance(value, bool):
        raise ValueError("value must be boolean")
    if payload["command"] == "release":
        await control.async_set_charging_release(value)
    else:
        await control.async_set_lock_evse(value)


async def _solar(_service: ModbusService, control: AmtronControl, payload: dict[str, Any]) -> None:
    await control.async_set_solar_charging_mode(SolarChargingMode(integer(payload.get("value"), 0, 3)))


async def _phases(_service: ModbusService, control: AmtronControl, payload: dict[str, Any]) -> None:
    await control.async_set_requested_phases(RequestedPhases(integer(payload.get("value"), 0, 1)))


async def _recover(_service: ModbusService, control: AmtronControl, _payload: dict[str, Any]) -> None:
    await control.async_recover_from_error()


async def _restart(_service: ModbusService, control: AmtronControl, _payload: dict[str, Any]) -> None:
    await control.async_restart()


async def _acknowledge(service: ModbusService, control: AmtronControl, _payload: dict[str, Any]) -> None:
    await service.acknowledge_intent_reset(control)


async def _departure(service: ModbusService, payload: dict[str, Any]) -> dict[str, Any]:
    return await service.planner.command(payload)


async def _guest_start(service: ModbusService, payload: dict[str, Any]) -> dict[str, Any]:
    return await service.guest.start(payload)


async def _charge_note(service: ModbusService, payload: dict[str, Any]) -> dict[str, Any]:
    return await service.charge_note(payload)


COMMANDS: dict[str, Command] = {
    "set_current": Command(_fields("value", "allow_unlimited"), _set_current),
    "pause": Command(run=_pause),
    "resume": Command(_fields("current_cap"), _resume),
    "release": Command(_fields("value"), _switch),
    "lock": Command(_fields("value"), _switch),
    "solar": Command(_fields("value"), _solar),
    "phases": Command(_fields("value"), _phases),
    "recover": Command(run=_recover),
    "restart": Command(run=_restart),
    "cancel_pending": Command(run=_cancel_pending, wallbox=False),
    "energy_goal": Command(_fields("value", "expected_choice_revision"), _energy_goal),
    "cost_goal": Command(_fields("value", "price", "currency", "expected_choice_revision"), _cost),
    "cost_price": Command(_fields("price", "currency"), _cost),
    "soc_source": Command(_fields("source", "maximum_required"), _soc_source, wallbox=False),
    "soc_goal": Command(_fields("value", "source", "soc", "soc_age", "maximum"), _soc),
    "soc_observation": Command(
        _fields("source", "soc", "soc_age", "maximum", "expected_soc_revision"), _soc, wallbox=False,
    ),
    "soc_goal_choice": Command(_fields("value", "expected_choice_revision"), _soc),
    "plug_charge_config": Command(_fields("value", "policy"), _plug_charge_config),
    "plug_charge_authorize": Command(_fields("session", "deadline"), _plug_charge_authorize),
    "unplug_release_config": Command(_fields("value"), _unplug_release_config, wallbox=False),
    "acknowledge_intent_reset": Command(run=_acknowledge, reset=True),
    "guest_start": Command(_fields("expected_guest_revision"), direct=_guest_start),
    "departure_choice": Command(_fields("deadline", "revision"), direct=_departure, wallbox=False),
    "departure_trip": Command(
        _fields("deadline", "source", "withdraw", "expected_selection"), direct=_departure, wallbox=False,
    ),
    "departure_inputs": Command(_fields("value"), direct=_departure, wallbox=False),
    "charge_note": Command(_fields("plugged_at", "note"), direct=_charge_note, wallbox=False),
}


def needs_wallbox(name: str, values: Mapping[str, Any]) -> bool:
    """Whether a command is only taken while the wallbox answers."""
    # Switching the gate off is a setting; switching it on closes the release.
    return COMMANDS[name].wallbox and not (name == "plug_charge_config" and values.get("value") is False)
