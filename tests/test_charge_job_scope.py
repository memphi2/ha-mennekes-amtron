"""Ended physical sessions and inconsistent disk state cannot grant a start."""

import pytest

from custom_components.mennekes_amtron_rtu.device.charge_job import ChargeJob
from tests.test_charge_job import job, observe


def test_unplug_also_ends_the_scope_of_a_completed_or_stopped_order():
    value = job()
    value.stop()
    observe(value, present=False)
    assert value.scope_ended
    assert ChargeJob.restore(value.snapshot()).scope_ended


@pytest.mark.parametrize("changes", [
    {"scope_ended": 1}, {"scope_ended": True}, {"reason": "user_stop"},
    {"state": "interrupted", "reason": "charging"},
    {"state": "reached", "reason": "energy_reached"},
    {"state": "waiting_meter", "reason": "meter_unavailable"},
])
def test_inconsistent_saved_state_cannot_restore_permission(changes):
    raw = job().snapshot()
    raw.update(changes)
    with pytest.raises(ValueError, match="job"):
        ChargeJob.restore(raw)
