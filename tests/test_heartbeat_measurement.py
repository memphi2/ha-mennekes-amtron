"""Total heartbeat count cannot hide a gap or a heartbeat that stopped."""

from tests.heartbeat_measurement import HeartbeatMeasurement


def test_healthy_delivery_and_silent_tail():
    measured = HeartbeatMeasurement(0)
    for now in (5, 10, 15):
        measured.delivered(now)
    assert measured.maximum_gap(20) == 5
    assert measured.maximum_gap(26) == 11


def test_late_delivery_stays_visible_after_a_burst():
    measured = HeartbeatMeasurement(0)
    for now in (5, 20, 21, 22, 23, 24):
        measured.delivered(now)
    assert measured.deliveries == 6
    assert measured.maximum_gap(25) == 15
