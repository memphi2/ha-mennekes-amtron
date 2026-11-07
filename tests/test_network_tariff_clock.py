"""Tariff retries follow German civil time, including DST and entry unload."""

import asyncio
from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.mennekes_amtron_rtu import network_tariff_year as tariff_year


@pytest.mark.parametrize("ha_zone", ["UTC", "America/Los_Angeles", "Asia/Tokyo"])
@pytest.mark.parametrize(("now", "expected", "following"), [
    ("2026-12-31T22:59:59+00:00", "2026-12-31T23:00:05+00:00", "2027-01-01T23:00:05+00:00"),
    ("2026-03-28T23:00:06+00:00", "2026-03-29T22:00:05+00:00", "2026-03-30T22:00:05+00:00"),
    ("2026-10-24T22:00:06+00:00", "2026-10-25T23:00:05+00:00", "2026-10-26T23:00:05+00:00"),
])
def test_daily_retry_uses_berlin_midnight_and_unload_cancels_rearmed_timer(
    tmp_path, monkeypatch, ha_zone, now, expected, following,
):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.config.time_zone = ha_zone
        monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", ZoneInfo(ha_zone))
        clock = [datetime.fromisoformat(now)]
        monkeypatch.setattr(tariff_year.dt_util, "utcnow", lambda: clock[0])
        timers = []

        def track(_hass, action, at):
            cancel = Mock()
            timers.append((action, at, cancel))
            return cancel

        monkeypatch.setattr(tariff_year, "async_track_point_in_utc_time", track)
        action = Mock()
        cancel = tariff_year.async_track_tariff_midnight(hass, action)
        assert timers[0][1] == datetime.fromisoformat(expected)
        clock[0] = timers[0][1]
        timers[0][0](clock[0])
        action.assert_called_once_with(clock[0])
        assert timers[1][1] == datetime.fromisoformat(following)
        cancel()
        timers[1][2].assert_called_once()
        await hass.async_stop()

    asyncio.run(run())
