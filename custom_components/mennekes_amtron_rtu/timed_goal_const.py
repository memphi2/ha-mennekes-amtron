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

CONF_TIMED_BAVARIAN_DEVICE: Final = "timed_bavarian_device"
CONF_TIMED_VEHICLE_TIME_ZONE: Final = "timed_vehicle_time_zone"
CONF_TIMED_TIMER1_DAYS: Final = "timed_timer1_days"
CONF_TIMED_TIMER2_DAYS: Final = "timed_timer2_days"
TIMED_WEEKDAYS: Final = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def timed_goal_is_enabled(options: Mapping[str, object]) -> bool:
    """Honor explicit choice; preserve existing configured departure planning."""
    enabled = options.get(CONF_TIMED_GOAL_ENABLED)
    if isinstance(enabled, bool):
        return enabled
    if any(options.get(key) for key in (
        CONF_TIMED_CAPACITY_KWH, CONF_TIMED_CAPACITY_ENTITY,
        CONF_TIMED_DEPARTURE_ENTITY, CONF_TIMED_CALENDAR_ENTITY,
    )):
        return True
    # Generic provider settings supersede the former BMW-specific choice.
    if "timed_vehicle_provider" in options or "timed_vehicle_device" in options:
        return (
            options.get("timed_vehicle_provider") not in (None, "none", "")
            and bool(options.get("timed_vehicle_device"))
        )
    return bool(options.get(CONF_TIMED_BAVARIAN_DEVICE))
