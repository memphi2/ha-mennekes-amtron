"""The app pages keep up with what the service gives them.

Every field of the two page payloads has to be used by its page, and every
status the service can report needs a text. A feature added to the service
therefore cannot stay invisible in the app without this test failing.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from custom_components.mennekes_amtron_rtu.device import console_http
from custom_components.mennekes_amtron_rtu.device.departure_plan import DEPARTURE_STATES
from custom_components.mennekes_amtron_rtu.device.energy_goal import ENERGY_GOAL_STATES
from custom_components.mennekes_amtron_rtu.device.service_overview import (
    overview_snapshot,
)
from custom_components.mennekes_amtron_rtu.device.soc_goal import SOC_GOAL_STATES
from tests import service_guest_fixture as runtime
from tests.test_departure_planner import hand_over
from tests.test_service_charge_http import TOKEN, Console
from tests.test_service_overview import snapshot

_DEVICE = Path(console_http.__file__).parent
CHARGING = (_DEVICE / "app_charging.js").read_text(encoding="utf-8")
OVERVIEW = (_DEVICE / "app_overview.js").read_text(encoding="utf-8")


def _unused(payload: dict, source: str, *, skip: frozenset[str] = frozenset()) -> list[str]:
    """Keys, one level deep, that the page's script never reads."""
    keys = set(payload) - skip
    for value in payload.values():
        if isinstance(value, dict):
            keys |= set(value)
    return sorted(key for key in keys if not re.search(rf"\b{key}\b", source))


def test_the_charging_page_uses_every_field_the_service_sends(tmp_path):
    async def body():
        async with runtime.guest_service(tmp_path) as (service, _transport, _clock):
            await hand_over(service)
            await service.command({
                "command": "cost_goal", "value": 10, "price": 0.3,
                "expected_serial_number": service.identity.serial_number,
            })
            payload = Console("supervisor-fixture", TOKEN, service=service)._choices()
            assert payload["departure"] is not None
            assert payload["cost"] is not None
            assert _unused(payload, CHARGING) == []

    asyncio.run(body())


def test_the_overview_uses_every_field_the_service_sends():
    assert _unused(overview_snapshot(snapshot()), OVERVIEW) == []


@pytest.mark.parametrize(("states", "prefix"), [
    (ENERGY_GOAL_STATES, ""), (SOC_GOAL_STATES, ""), (DEPARTURE_STATES, "plan_"),
])
def test_every_status_of_a_target_has_a_text_on_the_charging_page(states, prefix):
    missing = [
        state for state in states
        if not re.search(rf'(?:^|[\s,{{])"?{prefix}{state}"?:\[', CHARGING, re.MULTILINE)
    ]
    assert missing == []
