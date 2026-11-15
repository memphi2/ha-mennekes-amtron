"""A completed session the service has not saved yet reaches storage when it stops."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from custom_components.mennekes_amtron_rtu.device.enums import EvseState
from tests.test_session_service import complete, observe, owner


def test_stop_saves_the_latest_delayed_session_and_never_the_replaced_one(tmp_path):
    async def run():
        clock = [1000.0]
        with patch("custom_components.mennekes_amtron_rtu.device.service_runtime.wall_clock", side_effect=lambda: clock[0]):
            service, transport = await owner(tmp_path)
            real_save = service._store.save
            saved = []

            def first_history_save_fails(state):
                if state.session_history.latest is not None and not saved:
                    saved.append(None)
                    raise OSError("disk full")
                saved.append(state.session_history.snapshot())
                real_save(state)

            service._store.save = first_history_save_fails
            await complete(service, transport, clock)
            first = service.snapshot()["charging_session"]["latest"]
            assert service._session_history_dirty
            # The retry waits a minute; a second charge finishes meanwhile.
            await observe(service, transport, clock, 1205, EvseState.EV_CONNECTED, 112)
            await observe(service, transport, clock, 1210, EvseState.CHARGING, 112)
            await observe(service, transport, clock, 1300, EvseState.EV_CONNECTED, 120)
            await observe(service, transport, clock, 1320, EvseState.IDLE, 120)
            second = service.snapshot()["charging_session"]["latest"]
            assert second["session_id"] != first["session_id"]
            assert service._session_history_dirty
            assert saved == [None]
            await service.stop()
            assert not service._session_history_dirty
            stored = json.loads(await asyncio.to_thread(service.state_path.read_text))
            assert stored["charging_session_history"]["latest"] == second
            # Stopping retried the delayed save at once. Only the latest charge
            # is saved: the unsaved first one was replaced, never written.
            latest = [entry["latest"] for entry in saved[1:]]
            assert latest
            assert all(entry in (None, second) for entry in latest)
            assert second in latest

    asyncio.run(run())
