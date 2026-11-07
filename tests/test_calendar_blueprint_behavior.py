"""Execute calendar planning and climate blueprints as real HA automations."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import load_yaml_dict

from custom_components.mennekes_amtron_rtu.blueprint_installer import SOURCE_DIR
from tests.test_home_assistant_smoke import _boot

CALENDAR = "calendar.trips"
ARM = "input_boolean.next_trip"
DEPARTURE = "datetime.wallbox_departure"
MARKER = "input_text.last_climate_departure"


def _expand(name: str, inputs: dict[str, Any]) -> dict[str, Any]:
    path = SOURCE_DIR / name
    blueprint = Blueprint(
        load_yaml_dict(str(path)),
        expected_domain="automation",
        path=str(path),
        schema=BLUEPRINT_SCHEMA,
    )
    configured = BlueprintInputs(
        blueprint,
        {
            "alias": "Trip test",
            "id": "trip_test",
            "use_blueprint": {"path": str(path), "input": inputs},
        },
    )
    configured.validate()
    return configured.async_substitute()


async def _setup_calendar(hass: Any) -> None:
    config = _expand(
        "calendar_departure.yaml",
        {
            "calendar_entity": CALENDAR,
            "event_title": "Departure",
            "arm_next_trip": ARM,
            "departure_time": DEPARTURE,
            "planning_lead_hours": 12,
        },
    )
    assert await async_setup_component(hass, "automation", {"automation": [config]})
    await hass.async_block_till_done()
    assert hass.states.get("automation.trip_test").state == "on"


async def _run_again(hass: Any) -> None:
    await hass.services.async_call(
        "automation",
        "trigger",
        {"entity_id": "automation.trip_test", "skip_condition": False},
        blocking=True,
    )
    await hass.async_block_till_done()


def test_calendar_chooses_one_exact_future_timed_event_and_disarms(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        hass = await _boot(tmp_path / "config")
        writes: list[dict[str, Any]] = []
        queries: list[dict[str, Any]] = []
        now = dt_util.utcnow()
        expected = (now + timedelta(hours=2)).isoformat()
        try:

            async def events(call):
                queries.append(call.data)
                return {
                    CALENDAR: {
                        "events": [
                            {
                                "summary": "Departure",
                                "start": (now + timedelta(hours=4)).isoformat(),
                            },
                            {"summary": "Departure", "start": expected},
                            {
                                "summary": "departure",
                                "start": (now + timedelta(hours=1)).isoformat(),
                            },
                            {"summary": "Departure", "start": now.date().isoformat()},
                            {
                                "summary": "Departure",
                                "start": (now - timedelta(hours=1)).isoformat(),
                            },
                            {
                                "summary": "Departure",
                                "start": (now + timedelta(minutes=30))
                                .replace(tzinfo=None)
                                .isoformat(),
                            },
                            {"summary": "Departure", "start": "Tinvalid"},
                            {"summary": "Departure", "start": 123},
                            {
                                "summary": "Departure",
                                "start": (now + timedelta(hours=13)).isoformat(),
                            },
                            None,
                        ]
                    }
                }

            async def set_departure(call):
                writes.append(call.data)
                hass.states.async_set(DEPARTURE, call.data["datetime"])

            async def disarm(call):
                hass.states.async_set(ARM, "off")

            hass.services.async_register(
                "calendar",
                "get_events",
                events,
                supports_response=SupportsResponse.ONLY,
            )
            hass.services.async_register("datetime", "set_value", set_departure)
            hass.services.async_register("input_boolean", "turn_off", disarm)
            hass.states.async_set(CALENDAR, "off")
            hass.states.async_set(ARM, "off")
            hass.states.async_set(DEPARTURE, "unknown")
            await _setup_calendar(hass)
            hass.states.async_set(ARM, "on")
            await hass.async_block_till_done()
            assert [call["datetime"] for call in writes] == [expected]
            assert hass.states.get(ARM).state == "off"
            assert len(queries) == 1
            assert (
                43199
                < (
                    dt_util.parse_datetime(queries[0]["end_date_time"])
                    - dt_util.parse_datetime(queries[0]["start_date_time"])
                ).total_seconds()
                < 43201
            )
            await _run_again(hass)
            hass.states.async_set(ARM, "on")
            await hass.async_block_till_done()
            assert len(writes) == len(queries) == 1  # Never overwrite a selected trip.
        finally:
            await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))


@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        {},
        {CALENDAR: None},
        {CALENDAR: {"events": None}},
        {CALENDAR: {"events": "invalid"}},
        {CALENDAR: {"events": []}},
    ],
)
def test_calendar_empty_or_invalid_response_keeps_request_armed(
    tmp_path: Path, response: Any
) -> None:
    async def run() -> None:
        hass = await _boot(tmp_path / "config")
        writes = []
        try:

            async def events(call):
                return response

            async def write(call):
                writes.append(call.data)

            hass.services.async_register(
                "calendar",
                "get_events",
                events,
                supports_response=SupportsResponse.ONLY,
            )
            hass.services.async_register("datetime", "set_value", write)
            hass.states.async_set(CALENDAR, "off")
            hass.states.async_set(ARM, "off")
            hass.states.async_set(DEPARTURE, "unknown")
            await _setup_calendar(hass)
            hass.states.async_set(ARM, "on")
            await hass.async_block_till_done()
            assert writes == []
            assert hass.states.get(ARM).state == "on"
        finally:
            await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))


@pytest.mark.parametrize("changed", ["disarmed", "manual_departure", "unavailable"])
def test_calendar_rechecks_user_changes_while_fetching(
    tmp_path: Path, changed: str
) -> None:
    async def run() -> None:
        hass = await _boot(tmp_path / "config")
        started, release = asyncio.Event(), asyncio.Event()
        writes = []
        try:

            async def events(call):
                started.set()
                await release.wait()
                return {
                    CALENDAR: {
                        "events": [
                            {
                                "summary": "Departure",
                                "start": (
                                    dt_util.utcnow() + timedelta(hours=2)
                                ).isoformat(),
                            }
                        ]
                    }
                }

            async def write(call):
                writes.append(call.data)

            hass.services.async_register(
                "calendar",
                "get_events",
                events,
                supports_response=SupportsResponse.ONLY,
            )
            hass.services.async_register("datetime", "set_value", write)
            hass.states.async_set(CALENDAR, "off")
            hass.states.async_set(ARM, "off")
            hass.states.async_set(DEPARTURE, "unknown")
            await _setup_calendar(hass)
            hass.states.async_set(ARM, "on")
            await asyncio.wait_for(started.wait(), 3)
            if changed == "disarmed":
                hass.states.async_set(ARM, "off")
            else:
                hass.states.async_set(
                    DEPARTURE,
                    "unavailable"
                    if changed == "unavailable"
                    else (dt_util.utcnow() + timedelta(hours=3)).isoformat(),
                )
            release.set()
            await hass.async_block_till_done()
            assert writes == []
        finally:
            release.set()
            await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))


@pytest.mark.parametrize("calendar_state", ["unknown", "unavailable"])
def test_calendar_does_not_query_unavailable_source(
    tmp_path: Path, calendar_state: str
) -> None:
    async def run() -> None:
        hass = await _boot(tmp_path / "config")
        queries = []
        try:

            async def events(call):
                queries.append(call.data)
                return {}

            hass.services.async_register(
                "calendar",
                "get_events",
                events,
                supports_response=SupportsResponse.ONLY,
            )
            hass.states.async_set(CALENDAR, calendar_state)
            hass.states.async_set(ARM, "off")
            hass.states.async_set(DEPARTURE, "unknown")
            await _setup_calendar(hass)
            hass.states.async_set(ARM, "on")
            await hass.async_block_till_done()
            assert queries == []
        finally:
            await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))


async def _setup_climate(hass: Any) -> None:
    config = _expand(
        "precondition_before_departure.yaml",
        {
            "departure_time": DEPARTURE,
            "last_attempted_departure": MARKER,
            "climate_lead_minutes": 15,
            "climate_actions": [{"action": "test.start_climate"}],
        },
    )
    assert await async_setup_component(hass, "automation", {"automation": [config]})
    await hass.async_block_till_done()
    assert hass.states.get("automation.trip_test").state == "on"


@pytest.mark.parametrize("fail_climate", [False, True])
def test_climate_marks_before_action_and_runs_only_once_per_trip(
    tmp_path: Path, fail_climate: bool
) -> None:
    async def run() -> None:
        hass = await _boot(tmp_path / "config")
        attempts = []
        deadline = dt_util.utcnow() + timedelta(minutes=10)
        try:

            async def mark(call):
                hass.states.async_set(MARKER, call.data["value"])

            async def climate(call):
                attempts.append(int(hass.states.get(MARKER).state))
                if fail_climate:
                    raise HomeAssistantError("vehicle offline")

            hass.services.async_register("input_text", "set_value", mark)
            hass.services.async_register("test", "start_climate", climate)
            hass.states.async_set(MARKER, "")
            hass.states.async_set(DEPARTURE, "unknown")
            await _setup_climate(hass)
            hass.states.async_set(DEPARTURE, deadline.isoformat())
            await hass.async_block_till_done()
            assert attempts == [int(deadline.timestamp())]
            # Same persisted helper value also guards restart/time-pattern checks.
            await _run_again(hass)
            assert attempts == [int(deadline.timestamp())]
            next_deadline = deadline + timedelta(minutes=1)
            hass.states.async_set(DEPARTURE, next_deadline.isoformat())
            await hass.async_block_till_done()
            assert attempts == [
                int(deadline.timestamp()),
                int(next_deadline.timestamp()),
            ]
        finally:
            await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))


@pytest.mark.parametrize(
    ("minutes", "marker_state"),
    [
        (30, ""),
        (-1, ""),
        (10, "unavailable"),
    ],
)
def test_climate_requires_future_due_departure_and_restorable_helper(
    tmp_path: Path,
    minutes: int,
    marker_state: str,
) -> None:
    async def run() -> None:
        hass = await _boot(tmp_path / "config")
        writes = []
        try:

            async def record(call):
                writes.append(call.service)

            hass.services.async_register("input_text", "set_value", record)
            hass.services.async_register("test", "start_climate", record)
            hass.states.async_set(MARKER, marker_state)
            hass.states.async_set(DEPARTURE, "unknown")
            await _setup_climate(hass)
            hass.states.async_set(
                DEPARTURE, (dt_util.utcnow() + timedelta(minutes=minutes)).isoformat()
            )
            await hass.async_block_till_done()
            assert writes == []
        finally:
            await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))


def test_climate_marker_survives_a_real_home_assistant_restart(tmp_path: Path) -> None:
    """Use HA's real input_text service and RestoreEntity storage."""
    import shutil

    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED

    async def run() -> None:
        config_dir = tmp_path / "config"
        deadline = dt_util.utcnow() + timedelta(minutes=10)
        attempts = []
        for boot_number in range(2):
            if boot_number:
                shutil.rmtree(config_dir / "custom_components")
            hass = await _boot(config_dir)
            try:
                assert await async_setup_component(
                    hass,
                    "input_text",
                    {
                        "input_text": {
                            "last_climate_departure": {
                                "name": "Last climate departure",
                            }
                        },
                    },
                )

                async def climate(call, boot=boot_number):
                    attempts.append(boot)

                hass.services.async_register("test", "start_climate", climate)
                hass.states.async_set(DEPARTURE, deadline.isoformat())
                await _setup_climate(hass)
                # Exercise the blueprint's actual start trigger after helpers restore.
                hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
                await hass.async_block_till_done()
                assert int(hass.states.get(MARKER).state) == int(deadline.timestamp())
                assert attempts == [0]
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))
