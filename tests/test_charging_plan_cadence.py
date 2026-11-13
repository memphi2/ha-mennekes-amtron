"""Keep the finishing lookahead aligned with its command owner."""

from custom_components.mennekes_amtron_rtu.charging_plan import (
    FINISHING_WINDOW_HOURS,
)
from custom_components.mennekes_amtron_rtu.timed_goal_observer import (
    _MIN_WRITE_INTERVAL,
)


def test_finishing_window_covers_one_observer_write_interval():
    assert FINISHING_WINDOW_HOURS * 3600 == _MIN_WRITE_INTERVAL
