"""Option names and sources of a car's departure planning."""

from collections.abc import Mapping
from typing import Final

CONF_DEPARTURE_ENABLED: Final = "departure_enabled"
CONF_DEPARTURE_RESET_PENDING: Final = "departure_reset_pending"
CONF_DEPARTURE_CAPACITY_KWH: Final = "departure_capacity_kwh"
CONF_DEPARTURE_CAPACITY_ENTITY: Final = "departure_capacity_entity"
CONF_DEPARTURE_MINIMUM_SOC: Final = "departure_minimum_soc"
CONF_DEPARTURE_ENTITY: Final = "departure_entity"
CONF_DEPARTURE_CALENDAR_ENTITY: Final = "departure_calendar_entity"
CONF_DEPARTURE_CALENDAR_TITLE: Final = "departure_calendar_title"

CONF_DEPARTURE_VEHICLE_TIME_ZONE: Final = "departure_vehicle_time_zone"
CONF_DEPARTURE_TIMER1_DAYS: Final = "departure_timer1_days"
CONF_DEPARTURE_TIMER2_DAYS: Final = "departure_timer2_days"
DEPARTURE_WEEKDAYS: Final = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def departure_is_enabled(options: Mapping[str, object]) -> bool:
    """Departure planning is an explicit opt-in."""
    return options.get(CONF_DEPARTURE_ENABLED) is True
