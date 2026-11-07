"""Optional Home Assistant sources for a single timed charging goal."""

from typing import Final

CONF_TIMED_CAPACITY_KWH: Final = "timed_capacity_kwh"
CONF_TIMED_CAPACITY_ENTITY: Final = "timed_capacity_entity"
CONF_TIMED_DEPARTURE_ENTITY: Final = "timed_departure_entity"
CONF_TIMED_CALENDAR_ENTITY: Final = "timed_calendar_entity"
CONF_TIMED_CALENDAR_TITLE: Final = "timed_calendar_title"

CONF_TIMED_BAVARIAN_DEVICE: Final = "timed_bavarian_device"
CONF_TIMED_VEHICLE_TIME_ZONE: Final = "timed_vehicle_time_zone"
CONF_TIMED_TIMER1_DAYS: Final = "timed_timer1_days"
CONF_TIMED_TIMER2_DAYS: Final = "timed_timer2_days"
TIMED_WEEKDAYS: Final = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
