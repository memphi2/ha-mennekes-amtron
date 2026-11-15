"""Run the shipped downgrade blueprint as a real Home Assistant automation."""

from __future__ import annotations

import asyncio
from pathlib import Path

from homeassistant.setup import async_setup_component

from tests.test_home_assistant_smoke import _boot
from tests.test_solar_blueprint_behavior import _expand


def test_a_downgrade_and_its_release_are_each_notified_once(tmp_path: Path) -> None:
    async def run() -> None:
        hass = await _boot(tmp_path / "config")
        calls: list[dict] = []
        try:

            async def record(call):
                calls.append(dict(call.data))

            hass.services.async_register("notify", "test", record)
            status = "sensor.x_charging_status"
            attributes = {"evse_state": "charging", "cp_state": "C2"}
            hass.states.async_set(status, "charging", attributes)
            config = _expand(
                "downgrade_notification.yaml",
                {"charging_status": status, "notify_action": "notify.test"},
            )
            assert await async_setup_component(hass, "automation", {"automation": [config]})
            await hass.async_block_till_done()
            assert hass.states.get("automation.pv_test").state == "on"

            async def show(state: str, downgraded_to: float | None = None) -> None:
                extra = {} if downgraded_to is None else {"downgraded_to": downgraded_to}
                hass.states.async_set(status, state, {**attributes, **extra})
                await hass.async_block_till_done()

            # Another attribute changing is no downgrade.
            attributes["cp_state"] = "C1"
            await show("charging")
            assert calls == []
            await show("charging", 8)
            await show("charging", 8)
            assert calls == [{
                "title": "Wallbox downgraded",
                "message": "The grid operator limited the wallbox to 8 A.",
            }]
            await show("charging")
            assert calls[1:] == [{
                "title": "Wallbox downgrade released",
                "message": "The grid operator released the wallbox limit.",
            }]
            # Losing and regaining the wallbox is neither a downgrade nor a
            # release, whatever the limit was before and after.
            await show("charging", 8)
            del calls[:]
            for state, limit in (
                ("unavailable", None), ("charging", 8), ("unknown", None),
                ("unknown", 8), ("charging", 8), ("unavailable", None), ("charging", None),
            ):
                await show(state, limit)
            assert calls == []
        finally:
            await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=60))
