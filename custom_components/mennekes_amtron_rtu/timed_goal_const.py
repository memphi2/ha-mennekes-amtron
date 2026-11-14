"""Optional Home Assistant sources for a single timed charging goal."""

from collections.abc import Mapping
from typing import Final

CONF_TIMED_GOAL_ENABLED: Final = "timed_goal_enabled"
CONF_TIMED_GOAL_RESET_PENDING: Final = "timed_goal_reset_pending"
CONF_TIMED_CAPACITY_KWH: Final = "timed_capacity_kwh"
CONF_TIMED_CAPACITY_ENTITY: Final = "timed_capacity_entity"
CONF_TIMED_MINIMUM_SOC: Final = "timed_minimum_soc"
CONF_TIMED_DEPARTURE_ENTITY: Final = "timed_departure_entity"
CONF_TIMED_CALENDAR_ENTITY: Final = "timed_calendar_entity"
CONF_TIMED_CALENDAR_TITLE: Final = "timed_calendar_title"

CONF_TIMED_VEHICLE_TIME_ZONE: Final = "timed_vehicle_time_zone"
CONF_TIMED_TIMER1_DAYS: Final = "timed_timer1_days"
CONF_TIMED_TIMER2_DAYS: Final = "timed_timer2_days"
TIMED_WEEKDAYS: Final = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def timed_goal_is_enabled(options: Mapping[str, object]) -> bool:
    """Departure planning is an explicit opt-in."""
    return options.get(CONF_TIMED_GOAL_ENABLED) is True
